"""问答路由。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_user, ok
from database.connection import get_db
from database.models import User
from processor import chat_service

router = APIRouter(prefix="/api/chat", tags=["智能问答"])


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    message: str


@router.post("")
def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ok(chat_service.handle_chat(db, user, payload.message, payload.conversation_id))
