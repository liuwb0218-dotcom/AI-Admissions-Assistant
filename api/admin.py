"""管理端统计路由。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import ok, require_admin
from config.errors import bad_request
from config.settings import settings
from database.connection import get_db
from database.models import (
    Announcement,
    Conversation,
    Message,
    User,
    UserQuestion,
)
from processor.model_config import (
    PROVIDER_LABELS,
    PROVIDERS,
    add_custom_provider,
    add_llm_model,
    get_llm_config,
    list_local_models,
    provider_models,
    save_llm_config,
)

router = APIRouter(prefix="/api/admin", tags=["管理端"])


class LlmSettingRequest(BaseModel):
    provider: str
    model: str | None = None


class LlmModelRequest(BaseModel):
    provider: str
    model: str


class LlmProviderRequest(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str


@router.get("/stats")
def stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(func.count(User.id)).scalar() or 0
    admins = db.query(func.count(User.id)).filter(User.role == "admin").scalar() or 0
    conversations = db.query(func.count(Conversation.id)).scalar() or 0
    user_messages = (
        db.query(func.count(Message.id)).filter(Message.role == "user").scalar() or 0
    )

    question_counts = {"pending": 0, "answered": 0, "closed": 0}
    for status, count in (
        db.query(UserQuestion.status, func.count(UserQuestion.id))
        .group_by(UserQuestion.status)
        .all()
    ):
        question_counts[status] = count

    category_counts = [
        {"category": category, "count": count}
        for category, count in (
            db.query(UserQuestion.category, func.count(UserQuestion.id))
            .group_by(UserQuestion.category)
            .order_by(func.count(UserQuestion.id).desc())
            .all()
        )
    ]

    intent_counts = [
        {"intent": intent or "other", "count": count}
        for intent, count in (
            db.query(Message.intent, func.count(Message.id))
            .filter(Message.role == "user")
            .group_by(Message.intent)
            .order_by(func.count(Message.id).desc())
            .all()
        )
    ]

    announcements = db.query(func.count(Announcement.id)).scalar() or 0
    published = (
        db.query(func.count(Announcement.id))
        .filter(Announcement.is_published == 1)
        .scalar()
        or 0
    )

    return ok(
        {
            "users": users,
            "admins": admins,
            "conversations": conversations,
            "user_messages": user_messages,
            "questions": question_counts,
            "question_categories": category_counts,
            "intents": intent_counts,
            "announcements": {"total": announcements, "published": published},
        }
    )


@router.get("/llm")
def get_llm(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    config = get_llm_config(db)
    local_models = list_local_models()
    providers = []
    for provider in PROVIDERS:
        models = provider_models(config, provider, local_models)
        if provider == "local":
            available = bool(local_models)
        elif provider == "deepseek":
            available = bool(settings.deepseek_api_key.strip())
        else:
            available = True
        providers.append(
            {
                "value": provider,
                "label": PROVIDER_LABELS[provider],
                "models": models,
                "available": available,
            }
        )

    for provider, item in config["custom_providers"].items():
        providers.append(
            {
                "value": provider,
                "label": item["name"],
                "models": provider_models(config, provider),
                "available": True,
                "base_url": item["base_url"],
                "custom": True,
            }
        )

    return ok(
        {
            "provider": config["provider"],
            "model": config["model"],
            "providers": providers,
        }
    )


@router.put("/llm")
def update_llm(
    payload: LlmSettingRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    provider = (payload.provider or "").strip().lower()
    config = get_llm_config(db)
    if provider not in set(PROVIDERS) | set(config["custom_providers"]):
        raise bad_request("不支持的模型后端")
    model = (payload.model or "").strip()
    if model and len(model) > 100:
        raise bad_request("模型名称过长")

    current = save_llm_config(db, provider, model or None)
    db.commit()
    return ok({"provider": current["provider"], "model": current["model"]})


@router.post("/llm/models")
def create_llm_model(
    payload: LlmModelRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    provider = (payload.provider or "").strip().lower()
    config = get_llm_config(db)
    if provider not in set(PROVIDERS) | set(config["custom_providers"]):
        raise bad_request("不支持的模型后端")
    if provider == "mock":
        raise bad_request("离线兜底无需添加模型")
    model = (payload.model or "").strip()
    if not model:
        raise bad_request("请填写模型名称")
    if len(model) > 100:
        raise bad_request("模型名称过长")

    config = add_llm_model(db, provider, model)
    db.commit()
    return ok(
        {
            "provider": provider,
            "model": model,
            "models": provider_models(config, provider),
        }
    )


@router.post("/llm/providers")
def create_llm_provider(
    payload: LlmProviderRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    name = (payload.name or "").strip()
    base_url = (payload.base_url or "").strip()
    api_key = (payload.api_key or "").strip()
    model = (payload.model or "").strip()

    if not name:
        raise bad_request("请填写后端名称")
    if len(name) > 20:
        raise bad_request("后端名称过长（最多 20 字）")
    if not base_url.startswith(("http://", "https://")):
        raise bad_request("Base URL 需以 http:// 或 https:// 开头")
    if len(base_url) > 200:
        raise bad_request("Base URL 过长")
    if len(api_key) > 200:
        raise bad_request("API Key 过长")
    if not model:
        raise bad_request("请填写模型名称")
    if len(model) > 100:
        raise bad_request("模型名称过长")

    try:
        config, provider = add_custom_provider(db, name, base_url, api_key, model)
    except ValueError as exc:
        raise bad_request(str(exc))
    db.commit()
    item = config["custom_providers"][provider]
    return ok(
        {
            "provider": provider,
            "label": item["name"],
            "base_url": item["base_url"],
            "models": provider_models(config, provider),
            "custom": True,
        }
    )

