"""专业 / 学院清单直答：问“有哪些专业、几个学院”时直接查库列举，不走大模型。"""

from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import CollegeWebsite, EnrollmentPlan, Major, StudentProfile, User
from processor.college_links import match_colleges
from processor.course_query import is_course_question
from processor.score_query import (
    PROVINCES,
    _extract_major_type,
    _extract_province,
    _finalize_major_type,
    _normalize_major_type,
    match_majors,
)

LIST_MARKERS = ("有哪些", "有什么", "哪些", "全部", "所有", "清单", "名单", "几个", "设置")
COLLEGE_MARKERS = ("几个学院", "哪些学院", "学院有哪些", "有哪些学院", "学院设置", "多少个学院")


def _chunks(items: List[str], size: int = 6) -> List[str]:
    return ["、".join(items[i : i + size]) for i in range(0, len(items), size)]


def _catalog_scope(db: Session, user: Optional[User], question: str) -> tuple:
    """专业目录口径：问句里说过的省份 / 科类优先，没说再取考生资料。"""
    province = _extract_province(question)
    major_type = _extract_major_type(question)
    used_profile = False

    profile = None
    if user is not None:
        profile = (
            db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
        )
    if profile:
        if not province and profile.province in PROVINCES:
            province = profile.province
            used_profile = True
        if not major_type:
            major_type = _normalize_major_type(profile.subject_combo)
            used_profile = bool(major_type) or used_profile

    return province, _finalize_major_type(province, major_type), used_profile


def _scope_lead(province: str, major_type: str, used_profile: bool, fallback: bool = False) -> str:
    scope = " · ".join(value for value in (province, major_type) if value)
    if not scope:
        return ""
    if fallback:
        return f"（{scope}暂未收录，以下为全校口径）"
    source = "你的资料" if used_profile else "筛选条件"
    return f"按{source}（{scope}），"


def _college_list(db: Session) -> Optional[dict]:
    names = [
        row.name
        for row in db.query(CollegeWebsite)
        .filter(CollegeWebsite.is_enabled == 1)
        .order_by(CollegeWebsite.id)
        .all()
    ]
    if not names:
        return None
    lines = [f"东北师范大学目前设有 {len(names)} 个学院（学部）："]
    lines.extend("· " + line for line in _chunks(names, 4))
    lines.append("想了解某个学院的官网或专业设置，直接说学院名称即可。")
    return {"answer": "\n".join(lines), "refs": ["college_websites#学院清单"], "intent": "major"}


def _latest_year(db: Session) -> Optional[int]:
    return db.query(func.max(EnrollmentPlan.year)).scalar()


def _plan_major_names(
    db: Session,
    year: int,
    plan_type_like: str,
    province: str = "",
    major_type: str = "",
) -> List[str]:
    query = (
        db.query(Major.display_name, Major.name)
        .join(EnrollmentPlan, EnrollmentPlan.major_id == Major.id)
        .filter(EnrollmentPlan.year == year)
    )
    if plan_type_like:
        query = query.filter(EnrollmentPlan.plan_type.like(f"%{plan_type_like}%"))
    if province:
        query = query.filter(EnrollmentPlan.province == province)
    if major_type:
        query = query.filter(EnrollmentPlan.major_type == major_type)
    rows = query.distinct().all()
    return sorted({(display or name) for display, name in rows})


def _major_type_names(
    db: Session, year: int, major_type: str, province: str = ""
) -> List[str]:
    query = (
        db.query(Major.display_name, Major.name)
        .join(EnrollmentPlan, EnrollmentPlan.major_id == Major.id)
        .filter(EnrollmentPlan.year == year, EnrollmentPlan.major_type == major_type)
    )
    if province:
        query = query.filter(EnrollmentPlan.province == province)
    rows = query.distinct().all()
    return sorted({(display or name) for display, name in rows})


def build_catalog_answer(
    db: Session, question: str, user: Optional[User] = None
) -> Optional[dict]:
    # 问课程 / 培养方案时问的不是专业清单，交给课程直答（须先于学院清单判断）
    if is_course_question(question):
        return None

    if any(marker in question for marker in COLLEGE_MARKERS):
        return _college_list(db)

    # 转专业、辅修等问题里的“专业”不是问招生专业清单
    if any(marker in question for marker in ("转专业", "转系", "换专业", "辅修")):
        return None
    if "专业" not in question or not any(marker in question for marker in LIST_MARKERS):
        return None
    # 问到具体专业或学院（如“计算机有哪些课程”“生命科学学院有哪些专业”）时交给其他链路
    if match_majors(db, question) or match_colleges(db, question):
        return None

    year = _latest_year(db)
    if year is None:
        return None

    province, major_type, used_profile = _catalog_scope(db, user, question)

    if "中外合作" in question:
        names = _plan_major_names(db, year, "中外合作", province, major_type)
        fallback = not names and bool(province or major_type)
        if fallback:
            names = _plan_major_names(db, year, "中外合作")
        if not names:
            return None
        lead = _scope_lead(province, major_type, used_profile, fallback)
        lines = [f"{lead}{year} 年中外合作办学招生专业共 {len(names)} 个："]
        lines.extend("· " + line for line in _chunks(names))
        lines.append("中外合作办学学费较高（2.6 万~6 万元/学年），报考前请确认培养模式与学位授予方式。")
        return {
            "answer": "\n".join(lines),
            "refs": [f"enrollment_plans#{year}/中外合作办学"],
            "intent": "major",
        }

    if not major_type:
        # 老高考口径的“文科 / 理科”按新高考的史类 / 物理类近似对应
        if "文科" in question or "文史" in question:
            major_type = "历史类"
        elif "理科" in question or "理工" in question:
            major_type = "物理类"
    if major_type and ("招" in question or "报" in question):
        names = _major_type_names(db, year, major_type, province)
        fallback = not names and bool(province)
        if fallback:
            names = _major_type_names(db, year, major_type)
        if not names:
            return None
        lead = _scope_lead(province, major_type, used_profile, fallback)
        lines = [f"{lead}{year} 年招收{major_type}考生的专业共 {len(names)} 个："]
        lines.extend("· " + line for line in _chunks(names))
        lines.append("同一专业在不同省份的招生科类可能不同，以本省招生计划为准。")
        return {
            "answer": "\n".join(lines),
            "refs": [f"enrollment_plans#{year}/{major_type}"],
            "intent": "major",
        }

    names = _plan_major_names(db, year, "", province, major_type)
    fallback = not names and bool(province or major_type)
    if fallback:
        names = _plan_major_names(db, year, "")
    if not names:
        return None
    lead = _scope_lead(province, major_type, used_profile, fallback)
    lines = [f"{lead}{year} 年本科招生专业共 {len(names)} 个（含试验班、公费师范、专项与中外合作类型）："]
    lines.extend("· " + line for line in _chunks(names))
    lines.append("想看某个专业的最低分、学费或招生人数，直接说专业名称即可。")
    return {
        "answer": "\n".join(lines),
        "refs": [f"enrollment_plans#{year}/专业清单"],
        "intent": "major",
    }
