"""用户服务：注册、登录校验、考生画像。"""

from typing import Optional

import bcrypt
from sqlalchemy.orm import Session

from config.errors import bad_request
from database.models import StudentProfile, User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def register(db: Session, username: str, password: str, role: str = "user") -> User:
    username = (username or "").strip()
    if not (3 <= len(username) <= 50):
        raise bad_request("用户名长度需在 3~50 字之间")
    if not (6 <= len(password or "") <= 64):
        raise bad_request("密码长度需在 6~64 位之间")
    if role not in {"user", "admin"}:
        raise bad_request("角色非法")

    exists = db.query(User.id).filter(User.username == username).first()
    if exists:
        raise bad_request("用户名已存在")

    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.username == (username or "").strip()).first()
    if user is None or not verify_password(password or "", user.password_hash):
        return None
    return user


def get_profile(db: Session, user_id: int) -> Optional[StudentProfile]:
    return db.query(StudentProfile).filter(StudentProfile.user_id == user_id).first()


def upsert_profile(db: Session, user_id: int, payload: dict) -> StudentProfile:
    profile = get_profile(db, user_id)
    if profile is None:
        profile = StudentProfile(user_id=user_id, province=payload.get("province") or "未知")
        db.add(profile)

    for field in (
        "province",
        "score",
        "rank_no",
        "subject_combo",
        "interest_tags",
        "career_intent",
        "adjust_accepted",
        "exam_year",
        "special_qualification",
        "preferred_regions",
    ):
        if field in payload and payload[field] is not None:
            setattr(profile, field, payload[field])

    db.flush()
    return profile
