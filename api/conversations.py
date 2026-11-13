"""会话查询路由：普通用户只能看自己的会话。"""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_current_user, ok
from config.errors import bad_request, forbidden, not_found
from database.connection import get_db
from database.models import Conversation, Message, User

router = APIRouter(prefix="/api/conversations", tags=["会话"])


class RenameRequest(BaseModel):
    title: str


def _own_conversation(db: Session, user: User, conversation_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise not_found("会话不存在")
    if conversation.user_id != user.id:
        raise forbidden("无权访问他人会话")
    return conversation


def _load_links(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _summary(text: str | None) -> str:
    return " ".join((text or "").split())[:60]


@router.get("")
def list_conversations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.last_active_at.desc())
        .all()
    )

    ids = [row.id for row in rows]
    counts: dict[int, int] = {}
    last_by_conversation: dict[int, str] = {}
    if ids:
        counts = dict(
            db.query(Message.conversation_id, func.count(Message.id))
            .filter(Message.conversation_id.in_(ids))
            .group_by(Message.conversation_id)
            .all()
        )
        last_rows = (
            db.query(Message.conversation_id, func.max(Message.id))
            .filter(Message.conversation_id.in_(ids))
            .group_by(Message.conversation_id)
            .all()
        )
        contents = {
            row.id: row.content
            for row in db.query(Message.id, Message.content)
            .filter(Message.id.in_([message_id for _, message_id in last_rows]))
            .all()
        }
        last_by_conversation = {
            conversation_id: contents.get(message_id, "")
            for conversation_id, message_id in last_rows
        }

    return ok(
        [
            {
                "id": row.id,
                "title": row.title,
                "created_at": row.created_at,
                "last_active_at": row.last_active_at,
                "message_count": counts.get(row.id, 0),
                "last_message": _summary(last_by_conversation.get(row.id)),
            }
            for row in rows
        ]
    )


@router.get("/{conversation_id}/messages")
def list_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _own_conversation(db, user, conversation_id)

    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id)
        .all()
    )
    return ok(
        [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "intent": row.intent,
                "refs": row.refs,
                "links": _load_links(row.links),
                "created_at": row.created_at,
            }
            for row in rows
        ]
    )


@router.patch("/{conversation_id}")
def rename_conversation(
    conversation_id: int,
    payload: RenameRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _own_conversation(db, user, conversation_id)
    title = (payload.title or "").strip()
    if not title:
        raise bad_request("会话名称不能为空")
    if len(title) > 50:
        raise bad_request("会话名称不能超过 50 字")

    conversation.title = title
    db.flush()
    return ok({"id": conversation.id, "title": conversation.title})


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _own_conversation(db, user, conversation_id)
    deleted = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .delete(synchronize_session=False)
    )
    db.delete(conversation)
    db.flush()
    return ok({"id": conversation_id, "deleted_messages": deleted})
