"""API 公共依赖：统一响应包装、JWT 签发与解析、角色校验。"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from config.errors import AppError
from config.settings import settings
from database.connection import get_db
from database.models import User

security = HTTPBearer(auto_error=False)


def ok(data=None) -> dict:
    return {"code": 0, "message": "ok", "data": data}


def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user.id), "role": user.role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise AppError(4003, "未登录或登录已过期", 401)
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError:
        raise AppError(4003, "登录凭证无效或已过期", 401)

    user = db.get(User, int(payload.get("sub", 0)))
    if user is None:
        raise AppError(4003, "用户不存在", 401)
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise AppError(4003, "需要管理员权限", 403)
    return user
