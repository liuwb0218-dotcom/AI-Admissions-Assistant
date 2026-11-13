"""公告路由：用户查看已发布公告，管理员编辑、发布、下架。

发布/编辑已发布公告时，同事务把公告回灌进 knowledge_docs（source_type=announcement），
下架时删除回灌条目；同步失败不回滚发布状态，由检索侧按 is_published 兜底（需求 5.5）。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_user, ok, require_admin
from config.errors import bad_request, forbidden, not_found
from database.connection import get_db
from database.models import Announcement, KnowledgeDoc, User, UserQuestion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/announcements", tags=["公告"])

ANNOUNCEMENT_CATEGORIES = {"policy", "major", "score", "other"}


class AnnouncementRequest(BaseModel):
    title: str
    content: str
    category: str = "policy"
    related_question_id: int | None = None


def _serialize(row: Announcement) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "category": row.category,
        "related_question_id": row.related_question_id,
        "is_published": row.is_published,
        "published_at": row.published_at,
        "knowledge_synced": row.knowledge_synced,
        "view_count": row.view_count,
        "created_at": row.created_at,
    }


def _validate(payload: AnnouncementRequest) -> tuple[str, str, str]:
    title = (payload.title or "").strip()
    content = (payload.content or "").strip()
    category = (payload.category or "policy").strip().lower()
    if not (1 <= len(title) <= 200):
        raise bad_request("公告标题需在 1~200 字之间")
    if not (10 <= len(content) <= 5000):
        raise bad_request("公告正文需在 10~5000 字之间")
    if category not in ANNOUNCEMENT_CATEGORIES:
        raise bad_request("公告分类非法")
    return title, content, category


def _sync_knowledge(db: Session, row: Announcement) -> None:
    doc = (
        db.query(KnowledgeDoc)
        .filter(KnowledgeDoc.source_announcement_id == row.id)
        .first()
    )
    if doc is None:
        db.add(
            KnowledgeDoc(
                category="announcement",
                title=row.title[:200],
                content=row.content,
                source_file="招生办公告",
                source_type="announcement",
                source_announcement_id=row.id,
                sensitivity_level=0,
                keywords=row.category,
            )
        )
    else:
        doc.title = row.title[:200]
        doc.content = row.content
        doc.updated_at = datetime.now()
    db.flush()


def _sync_safely(db: Session, row: Announcement, remove: bool = False) -> None:
    """同步失败只标记 knowledge_synced=2，不影响公告本身的发布状态。"""
    try:
        with db.begin_nested():
            if remove:
                db.query(KnowledgeDoc).filter(
                    KnowledgeDoc.source_announcement_id == row.id
                ).delete()
            else:
                _sync_knowledge(db, row)
        row.knowledge_synced = 0 if remove else 1
    except Exception:  # 唯一键冲突、数据库异常等
        logger.exception("公告知识库同步失败 announcement_id=%s", row.id)
        row.knowledge_synced = 2


def _get_announcement(db: Session, announcement_id: int) -> Announcement:
    row = db.get(Announcement, announcement_id)
    if row is None:
        raise not_found("公告不存在")
    return row


@router.get("")
def list_announcements(
    include_draft: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if include_draft and user.role != "admin":
        raise forbidden("只有管理员可以查看草稿公告")
    query = db.query(Announcement)
    if not include_draft:
        query = query.filter(Announcement.is_published == 1)
    rows = query.order_by(Announcement.id.desc()).limit(100).all()
    return ok([_serialize(row) for row in rows])


@router.get("/{announcement_id}")
def get_announcement(
    announcement_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _get_announcement(db, announcement_id)
    if row.is_published != 1 and user.role != "admin":
        raise not_found("公告不存在")
    if row.is_published == 1:
        row.view_count += 1
    return ok(_serialize(row))


@router.post("")
def create_announcement(
    payload: AnnouncementRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    title, content, category = _validate(payload)
    if payload.related_question_id is not None:
        if db.get(UserQuestion, payload.related_question_id) is None:
            raise bad_request("关联提问不存在")

    row = Announcement(
        title=title,
        content=content,
        category=category,
        related_question_id=payload.related_question_id,
        created_by=admin.id,
    )
    db.add(row)
    db.flush()
    return ok(_serialize(row))


@router.put("/{announcement_id}")
def update_announcement(
    announcement_id: int,
    payload: AnnouncementRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    title, content, category = _validate(payload)
    row = _get_announcement(db, announcement_id)
    row.title = title
    row.content = content
    row.category = category
    if payload.related_question_id is not None:
        row.related_question_id = payload.related_question_id
    db.flush()

    if row.is_published == 1:
        _sync_safely(db, row)
    return ok(_serialize(row))


@router.post("/{announcement_id}/publish")
def publish_announcement(
    announcement_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _get_announcement(db, announcement_id)
    if row.is_published != 1:
        row.is_published = 1
        row.published_at = datetime.now()
    db.flush()
    _sync_safely(db, row)
    return ok(_serialize(row))


@router.post("/{announcement_id}/unpublish")
def unpublish_announcement(
    announcement_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _get_announcement(db, announcement_id)
    row.is_published = 0
    db.flush()
    _sync_safely(db, row, remove=True)
    return ok(_serialize(row))
