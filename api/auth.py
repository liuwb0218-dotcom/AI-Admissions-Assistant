"""认证与考生画像路由。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import create_access_token, get_current_user, ok
from config.errors import bad_request
from database.connection import get_db
from database.models import User
from services import profile_service, user_service

router = APIRouter(prefix="/api/auth", tags=["认证"])
profile_router = APIRouter(prefix="/api", tags=["考生画像"])


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class ProfileRequest(BaseModel):
    province: str | None = None
    score: int | None = None
    rank_no: int | None = None
    subject_combo: str | None = None
    interest_tags: str | None = None
    career_intent: str | None = None
    adjust_accepted: int | None = None
    exam_year: int | None = None
    special_qualification: str | None = None
    preferred_regions: str | None = None


@router.post("/register")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    user = user_service.register(db, payload.username, payload.password)
    return ok({"id": user.id, "username": user.username, "role": user.role})


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = user_service.authenticate(db, payload.username, payload.password)
    if user is None:
        raise bad_request("用户名或密码错误")
    return ok(
        {
            "access_token": create_access_token(user),
            "token_type": "bearer",
            "username": user.username,
            "role": user.role,
        }
    )


@profile_router.get("/profile")
def read_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = user_service.get_profile(db, user.id)
    return ok(profile_service.serialize(profile))


@profile_router.put("/profile")
def update_profile(
    payload: ProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = user_service.get_profile(db, user.id)
    data = profile_service.validate(
        payload.model_dump(exclude_none=True),
        profile.province if profile else None,
    )
    profile = user_service.upsert_profile(db, user.id, data)
    return ok(profile_service.serialize(profile))
