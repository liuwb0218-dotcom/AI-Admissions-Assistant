"""专业查询路由：每个专业都返回最低分 / 最低位次，并标注数据口径。"""

from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_current_user, ok
from config.errors import not_found
from database.connection import get_db
from database.models import AdmissionScore, EnrollmentPlan, Major, StudentProfile, User

router = APIRouter(prefix="/api/majors", tags=["专业"])

_PREFERRED_MAJOR_TYPES = ["物理类", "历史类", "综合改革"]
_PREFERRED_PROVINCE = "吉林"
_HISTORY_LIMIT = 3
# 官方明确不公布分专业分数的计划类型
_UNPUBLISHED_PLAN_TYPES = ("高校专项计划", "艺术类", "体育类")


def _default_major_type(province: str) -> str:
    from services.profile_service import normalize_subject

    return normalize_subject(province, "物理") or "物理类"


def _resolve_scope(
    db: Session,
    user: User,
    year: int | None,
    province: str | None,
    major_type: str | None,
) -> tuple[int, str, str]:
    """口径优先级：请求参数 → 考生画像 → 默认吉林/物理类。"""
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    province = province or (profile.province if profile else None) or _PREFERRED_PROVINCE
    major_type = (
        major_type or (profile.subject_combo if profile else None) or _default_major_type(province)
    )
    if year is None:
        year = db.query(func.max(AdmissionScore.year)).scalar()
    return int(year or 0), province, major_type


def _best(rows: list[AdmissionScore]) -> AdmissionScore:
    return min(
        rows,
        key=lambda row: (row.min_score, row.min_rank if row.min_rank is not None else 10**9),
    )


def _score_index(
    db: Session, province: str, major_type: str
) -> dict[int, list[AdmissionScore]]:
    rows = (
        db.query(AdmissionScore)
        .filter(
            AdmissionScore.province == province,
            AdmissionScore.major_type == major_type,
            AdmissionScore.min_score.isnot(None),
        )
        .all()
    )
    index: dict[int, list[AdmissionScore]] = defaultdict(list)
    for row in rows:
        index[row.major_id].append(row)
    return index


def _pick_score(
    index: dict[int, list[AdmissionScore]], major_id: int, year: int
) -> tuple[AdmissionScore | None, bool, str]:
    """先取目标年份，再回退到本省最近年份，保证卡片不空。"""
    rows = index.get(major_id) or []
    current = [row for row in rows if row.year == year]
    if current:
        return _best(current), False, ""
    older = [row for row in rows if row.year != year]
    if older:
        latest = max(row.year for row in older)
        return _best([row for row in older if row.year == latest]), True, f"参考 {latest} 年本省数据"
    return None, False, ""


def _history(rows: list[AdmissionScore]) -> list[dict]:
    best_by_year: dict[int, AdmissionScore] = {}
    for row in rows:
        current = best_by_year.get(row.year)
        if current is None or (row.min_score, row.min_rank or 10**9) < (
            current.min_score,
            current.min_rank or 10**9,
        ):
            best_by_year[row.year] = row
    years = sorted(best_by_year, reverse=True)[:_HISTORY_LIMIT]
    return [
        {
            "year": year,
            "min_score": best_by_year[year].min_score,
            "min_rank": best_by_year[year].min_rank,
        }
        for year in years
    ]


def _plan_status(
    plans: list[EnrollmentPlan], year: int
) -> tuple[str, str]:
    rows = [row for row in plans if row.year == year]
    if not rows:
        return "not_planned", "本省当年无该专业招生计划"
    if any(row.plan_type in _UNPUBLISHED_PLAN_TYPES for row in rows):
        return "unpublished_special", "该计划类型不公布分专业分数"
    return "unpublished", "该专业尚未公布录取分数"


def _serialize(
    row: Major,
    score: AdmissionScore | None = None,
    history: list[dict] | None = None,
    is_reference: bool = False,
    note: str = "",
    status: str = "published",
    status_text: str = "",
) -> dict:
    data = {
        "id": row.id,
        "name": row.name,
        "display_name": row.display_name,
        "major_attr": row.major_attr,
        "department": row.department,
        "subject_requirement": row.subject_requirement,
        "description": row.description,
        "score": None,
        "score_history": history or [],
        "score_status": status,
        "score_note": note or status_text,
    }
    if score is not None:
        data["score"] = {
            "year": score.year,
            "province": score.province,
            "major_type": score.major_type,
            "plan_type": score.plan_type,
            "min_score": score.min_score,
            "min_rank": score.min_rank,
            "is_reference": is_reference,
            "note": note,
        }
    return data


def _preferred_first(values: list[str], preferred: list[str]) -> list[str]:
    ordered = [item for item in preferred if item in values]
    return ordered + sorted(item for item in values if item not in ordered)


@router.get("/options")
def major_options(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    years = [row[0] for row in db.query(AdmissionScore.year).distinct().all()]
    provinces = [row[0] for row in db.query(AdmissionScore.province).distinct().all()]
    major_types = [row[0] for row in db.query(AdmissionScore.major_type).distinct().all()]
    return ok(
        {
            "years": sorted(years, reverse=True),
            "provinces": _preferred_first(provinces, [_PREFERRED_PROVINCE]),
            "major_types": _preferred_first(major_types, _PREFERRED_MAJOR_TYPES),
        }
    )


@router.get("")
def list_majors(
    keyword: str | None = None,
    year: int | None = None,
    province: str | None = None,
    major_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    year, province, major_type = _resolve_scope(db, user, year, province, major_type)
    scores = _score_index(db, province, major_type)

    # 本省完全没有数据时，借用吉林同科类作为参考，并明确标注来源
    reference: dict[int, list[AdmissionScore]] = {}
    if province != _PREFERRED_PROVINCE:
        reference = _score_index(db, _PREFERRED_PROVINCE, major_type)

    plans: dict[int, list[EnrollmentPlan]] = defaultdict(list)
    for row in db.query(EnrollmentPlan).filter(
        EnrollmentPlan.province == province,
        EnrollmentPlan.major_type == major_type,
    ):
        plans[row.major_id].append(row)

    query = db.query(Major)
    if keyword:
        query = query.filter(Major.name.like(f"%{keyword}%"))
    rows = query.order_by(Major.id).all()

    payload = []
    for row in rows:
        score, is_reference, note = _pick_score(scores, row.id, year)
        history = _history(scores.get(row.id, []))
        if score is None and reference:
            score, _, _ = _pick_score(reference, row.id, year)
            if score is not None:
                is_reference = True
                note = f"参考 {score.province} {score.year} 年数据"
        status, status_text = (
            ("published", note)
            if score is not None
            else _plan_status(plans.get(row.id, []), year)
        )
        payload.append(
            _serialize(
                row,
                score,
                history,
                is_reference,
                note,
                status,
                status_text,
            )
        )
    return ok(payload)


@router.get("/{major_id}")
def get_major(
    major_id: int,
    year: int | None = Query(default=None),
    province: str | None = Query(default=None),
    major_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.get(Major, major_id)
    if row is None:
        raise not_found("专业不存在")

    year, province, major_type = _resolve_scope(db, user, year, province, major_type)
    scores = _score_index(db, province, major_type)
    score, is_reference, note = _pick_score(scores, row.id, year)
    if score is None and province != _PREFERRED_PROVINCE:
        score, _, _ = _pick_score(
            _score_index(db, _PREFERRED_PROVINCE, major_type), row.id, year
        )
        if score is not None:
            is_reference = True
            note = f"参考 {score.province} {score.year} 年数据"

    plans = (
        db.query(EnrollmentPlan)
        .filter(
            EnrollmentPlan.major_id == row.id,
            EnrollmentPlan.province == province,
            EnrollmentPlan.major_type == major_type,
        )
        .all()
    )
    status, status_text = (
        ("published", note) if score is not None else _plan_status(plans, year)
    )
    return ok(
        _serialize(
            row,
            score,
            _history(scores.get(row.id, [])),
            is_reference,
            note,
            status,
            status_text,
        )
    )
