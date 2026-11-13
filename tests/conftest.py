"""测试夹具：用 SQLite 隔离测试库，模型后端固定为 mock，避免依赖外部服务。"""

import os
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent.parent / "test_ai_advisor.db"

# 必须在导入任何项目模块之前设置，settings 在 import 时读取环境变量
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["JWT_SECRET"] = "test-secret-key-for-pytest-only-32bytes"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def prepared_database():
    from collector.importer import import_all
    from database.connection import SessionLocal, engine, init_db

    if TEST_DB.exists():
        TEST_DB.unlink()

    init_db()
    db = SessionLocal()
    try:
        import_all(db, include_neu=False)
        db.commit()
    finally:
        db.close()

    yield

    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(scope="session")
def client():
    from main import app

    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str = "pass123456") -> str:
    client.post("/api/auth/register", json={"username": username, "password": password})
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    return response.json()["data"]["access_token"]
