"""用户提问与管理员受理路由。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_user, ok, require_admin
from config.errors import bad_request, not_found
from database.connection import get_db
from database.models import User, UserQuestion

router = APIRouter(prefix="/api/questions", tags=["用户提问"])

QUESTION_CATEGORIES = {"policy", "major", "score", "other"}
_DEDUPE_WINDOW = timedelta(minutes=5)


class QuestionRequest(BaseModel):
    category: str = "other"
    content: str


class AnswerRequest(BaseModel):
    answer_content: str


def _serialize(row: UserQuestion, username: str | None = None) -> dict:
    data = {
        "id": row.id,
        "category": row.category,
        "content": row.content,
        "status": row.status,
        "answer_content": row.answer_content,
        "answered_at": row.answered_at,
        "created_at": row.created_at,
    }
    if username is not None:
        data["username"] = username
    return data


def _get_question(db: Session, question_id: int) -> UserQuestion:
    row = db.get(UserQuestion, question_id)
    if row is None:
        raise not_found("提问不存在")
    return row


@router.post("")
def submit_question(
    payload: QuestionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    category = (payload.category or "other").strip().lower()
    content = (payload.content or "").strip()
    if category not in QUESTION_CATEGORIES:
        raise bad_request("提问分类非法")
    if not (10 <= len(content) <= 500):
        raise bad_request("提问正文需在 10~500 字之间")

    # 防刷：同用户同内容 5 分钟内只保留一条
    recent = (
        db.query(UserQuestion)
        .filter(
            UserQuestion.user_id == user.id,
            UserQuestion.content == content,
            UserQuestion.created_at >= datetime.now() - _DEDUPE_WINDOW,
        )
        .first()
    )
    if recent is not None:
        return ok({**_serialize(recent), "duplicated": True})

    row = UserQuestion(user_id=user.id, category=category, content=content)
    db.add(row)
    db.flush()
    return ok({**_serialize(row), "duplicated": False})


@router.get("/my")
def my_questions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(UserQuestion)
        .filter(UserQuestion.user_id == user.id)
        .order_by(UserQuestion.id.desc())
        .limit(50)
        .all()
    )
    return ok([_serialize(row) for row in rows])


@router.get("")
def list_questions(
    status: str | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(UserQuestion, User.username).join(User, User.id == UserQuestion.user_id)
    if status:
        if status not in {"pending", "answered", "closed"}:
            raise bad_request("提问状态非法")
        query = query.filter(UserQuestion.status == status)
    rows = query.order_by(UserQuestion.id.desc()).limit(200).all()

    counts = {"pending": 0, "answered": 0, "closed": 0}
    for (status_value,) in db.query(UserQuestion.status).all():
        counts[status_value] = counts.get(status_value, 0) + 1

    return ok(
        {
            "items": [_serialize(row, username) for row, username in rows],
            "counts": counts,
        }
    )


@router.post("/{question_id}/answer")
def answer_question(
    question_id: int,
    payload: AnswerRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    answer = (payload.answer_content or "").strip()
    if not (1 <= len(answer) <= 2000):
        raise bad_request("答复正文需在 1~2000 字之间")

    row = _get_question(db, question_id)
    if row.status == "closed":
        raise bad_request("已关闭的提问不能再答复")

    row.answer_content = answer
    row.status = "answered"
    row.answered_by = admin.id
    row.answered_at = datetime.now()
    db.flush()
    return ok(_serialize(row))


@router.post("/{question_id}/close")
def close_question(
    question_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _get_question(db, question_id)
    if row.status == "closed":
        return ok(_serialize(row))
    row.status = "closed"
    db.flush()
    return ok(_serialize(row))
