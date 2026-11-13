"""数据库连接：engine / SessionLocal / get_db / init_db。"""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from config.settings import settings

Base = declarative_base()

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def _create_database_if_absent(url: str) -> None:
    """MySQL 下自动建库，避免手动 CREATE DATABASE。其他方言直接跳过。"""
    if not url.startswith("mysql"):
        return
    parsed = make_url(url)
    if not parsed.database:
        return
    server_engine = create_engine(parsed.set(database=None), future=True)
    try:
        with server_engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{parsed.database}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
    finally:
        server_engine.dispose()


def init_db() -> None:
    """建库 + 建表。幂等，可重复调用。"""
    from database import models  # noqa: F401  确保模型已注册到 Base.metadata

    _create_database_if_absent(settings.database_url)
    Base.metadata.create_all(bind=engine)
    _ensure_profile_columns()
    _ensure_message_columns()
    _ensure_major_columns()


# create_all 只建新表，不会给已有表加列：画像新增字段在这里补齐
_PROFILE_COLUMNS = {
    "adjust_accepted": "SMALLINT NULL",
    "exam_year": "INT NULL",
    "special_qualification": "VARCHAR(50) NULL",
    "preferred_regions": "VARCHAR(200) NULL",
}

# 历史对话回放要恢复学院 / 招生网链接，助手消息补存 links
_MESSAGE_COLUMNS = {"links": "VARCHAR(500) NULL"}

# 专业新增系级归属（college_departments.id）
_MAJOR_COLUMNS = {"department_id": "INT NULL"}


def _ensure_columns(table: str, columns: dict) -> None:
    with engine.connect() as conn:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
            existing = {row[1] for row in rows}
        else:
            rows = conn.execute(text(f"SHOW COLUMNS FROM {table}")).all()
            existing = {row[0] for row in rows}
        for column, ddl in columns.items():
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        conn.commit()


def _ensure_profile_columns() -> None:
    _ensure_columns("student_profiles", _PROFILE_COLUMNS)


def _ensure_message_columns() -> None:
    _ensure_columns("messages", _MESSAGE_COLUMNS)


def _ensure_major_columns() -> None:
    _ensure_columns("majors", _MAJOR_COLUMNS)


def get_db():
    """FastAPI 依赖：统一在这里收口事务。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
