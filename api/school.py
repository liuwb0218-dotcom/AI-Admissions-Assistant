"""院校投档线与招生计划变动路由。"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_current_user, ok
from database.connection import get_db
from database.models import EnrollmentPlan, Major, SchoolAdmissionScore

router = APIRouter(prefix="/api/school", tags=["school"])


def _serialize_score(row: SchoolAdmissionScore) -> dict:
    return {
        "year": row.year,
        "batch": row.batch,
        "group_name": row.group_name,
        "plan_type": row.plan_type,
        "subject_requirement": row.subject_requirement,
        "min_score": row.min_score,
        "min_rank": row.min_rank,
        "province": row.province,
        "major_type": row.major_type,
    }


@router.get("/scores")
def list_school_scores(
    province: str | None = None,
    major_type: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """按省份 / 科类返回所有年份的院校专业组投档线。"""
    if not province or not major_type:
        return ok([])
    rows = (
        db.query(SchoolAdmissionScore)
        .filter(
            SchoolAdmissionScore.province == province,
            SchoolAdmissionScore.major_type == major_type,
        )
        .order_by(SchoolAdmissionScore.year.desc(), SchoolAdmissionScore.min_score.desc())
        .all()
    )
    return ok([_serialize_score(row) for row in rows])


def _plan_counts(db: Session, province: str, year: int) -> dict[int, int]:
    """同一专业可能拆成多个计划类型，这里按专业汇总计划数。"""
    rows = (
        db.query(EnrollmentPlan.major_id, func.sum(EnrollmentPlan.plan_count))
        .filter(EnrollmentPlan.province == province, EnrollmentPlan.year == year)
        .group_by(EnrollmentPlan.major_id)
        .all()
    )
    return {major_id: int(total or 0) for major_id, total in rows}


@router.get("/plan-diff")
def plan_diff(
    province: str,
    from_year: int,
    to_year: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """对比两个年份的招生计划：新增专业、停招专业、计划数变化。"""
    from_counts = _plan_counts(db, province, from_year)
    to_counts = _plan_counts(db, province, to_year)

    major_ids = set(from_counts) | set(to_counts)
    names: dict[int, str] = {}
    if major_ids:
        names = {
            row.id: row.display_name or row.name
            for row in db.query(Major).filter(Major.id.in_(major_ids)).all()
        }

    added, removed, changed = [], [], []
    for major_id, count in to_counts.items():
        if major_id not in from_counts:
            added.append({"major": names.get(major_id, f"专业#{major_id}"), "plan_count": count})
    for major_id, count in from_counts.items():
        if major_id not in to_counts:
            removed.append({"major": names.get(major_id, f"专业#{major_id}"), "plan_count": count})
        else:
            delta = to_counts[major_id] - count
            if delta:
                changed.append(
                    {
                        "major": names.get(major_id, f"专业#{major_id}"),
                        "from_count": count,
                        "to_count": to_counts[major_id],
                        "delta": delta,
                    }
                )

    added.sort(key=lambda item: (-item["plan_count"], item["major"]))
    removed.sort(key=lambda item: (-item["plan_count"], item["major"]))
    changed.sort(key=lambda item: (-abs(item["delta"]), item["major"]))

    return ok(
        {
            "province": province,
            "from_year": from_year,
            "to_year": to_year,
            "added": added,
            "removed": removed,
            "changed": changed,
        }
    )
