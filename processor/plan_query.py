"""招生计划直答：问“招多少人 / 招生计划 / 名额 / 学制”时直接查 enrollment_plans。"""

from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import EnrollmentPlan, Major, StudentProfile
from processor.score_query import (
    PROVINCES,
    _extract_major_type,
    _extract_province,
    _finalize_major_type,
    _normalize_major_type,
    match_majors,
)


def _profile_scope(profile: Optional[StudentProfile]) -> tuple:
    """考生资料兜底：问句没写省份 / 科类时用资料里填的。"""
    if profile is None:
        return "", ""
    province = profile.province if profile.province in PROVINCES else ""
    major_type = _finalize_major_type(
        province, _normalize_major_type(profile.subject_combo)
    )
    return province, major_type

PLAN_MARKERS = (
    "招多少人",
    "招多少",
    "招几人",
    "招生人数",
    "招生计划",
    "计划数",
    "名额",
    "多少人",
    "学制",
    "读几年",
)
# 计划同比问题由 build_plan_change_answer 接管，build_plan_answer 不处理
CHANGE_MARKERS = ("变化", "对比", "增减", "调整", "扩招", "缩招")
DURATION_MARKERS = ("学制", "读几年", "几年制")


def _plan_total(rows: List[EnrollmentPlan]) -> int:
    return sum(row.plan_count or 0 for row in rows)


def _major_blocks(majors: List[Major], rows: List[EnrollmentPlan], with_province: bool) -> List[str]:
    lines: List[str] = []
    for major in majors:
        items = [row for row in rows if row.major_id == major.id and row.plan_count]
        if not items:
            continue
        label = major.display_name or major.name
        items.sort(key=lambda row: -(row.plan_count or 0))
        if with_province:
            detail = "；".join(f"{row.plan_type} {row.plan_count} 人" for row in items)
        else:
            detail = "；".join(
                f"{row.province} {row.plan_count} 人" for row in items
            )
        edu = next((row.edu_len for row in items if row.edu_len), "")
        suffix = f"（学制 {edu}）" if edu else ""
        lines.append(f"· {label}{suffix}：{detail}")
    return lines


def _build_duration_answer(db: Session, majors: List[Major], year: int) -> Optional[dict]:
    rows = (
        db.query(EnrollmentPlan)
        .filter(
            EnrollmentPlan.major_id.in_([major.id for major in majors]),
            EnrollmentPlan.year == year,
            EnrollmentPlan.edu_len.isnot(None),
        )
        .all()
    )
    if not rows:
        return None
    lines = [f"按 {year} 年招生计划："]
    for major in majors:
        items = [row for row in rows if row.major_id == major.id]
        if not items:
            continue
        label = major.display_name or major.name
        durations = sorted({row.edu_len for row in items})
        lines.append(f"· {label}：学制 {' / '.join(durations)}")
    if len(lines) == 1:
        return None
    return {
        "answer": "\n".join(lines),
        "refs": [f"enrollment_plans#{year}"],
        "intent": "plan",
    }


def build_plan_answer(
    db: Session,
    question: str,
    context_question: str = "",
    profile: Optional[StudentProfile] = None,
) -> Optional[dict]:
    if not any(marker in question for marker in PLAN_MARKERS):
        return None
    if any(marker in question for marker in CHANGE_MARKERS):
        return None

    year = db.query(func.max(EnrollmentPlan.year)).scalar()
    if year is None:
        return None

    majors = match_majors(db, question)
    if not majors and context_question:
        # “招生计划呢”这类追问没有专业，承接上一条问题
        majors = match_majors(db, context_question)
    province = _extract_province(question)
    major_type = _extract_major_type(question)
    if not province or not major_type:
        # 问句没写全时用考生资料补齐，避免默认按全国口径回答
        profile_province, profile_major_type = _profile_scope(profile)
        province = province or profile_province
        major_type = major_type or profile_major_type

    if majors and any(marker in question for marker in DURATION_MARKERS):
        return _build_duration_answer(db, majors, year)

    if majors:
        def _query_rows(target_province: str):
            query = db.query(EnrollmentPlan).filter(
                EnrollmentPlan.major_id.in_([major.id for major in majors]),
                EnrollmentPlan.year == year,
                EnrollmentPlan.plan_count.isnot(None),
            )
            if target_province:
                query = query.filter(EnrollmentPlan.province == target_province)
            if major_type:
                query = query.filter(EnrollmentPlan.major_type == major_type)
            return query.all()

        rows = _query_rows(province)
        if not rows:
            return None
        blocks = _major_blocks(majors, rows, with_province=bool(province))
        if not blocks:
            return None
        scope = province or "全国"
        lines = [f"按 {year} 年招生计划，{scope}的招生人数为："]
        lines.extend(blocks)
        lines.append(
            f"合计 {_plan_total(rows)} 人。计划数含普通类、专项、公费师范等全部类型，"
            "具体以省考试院公布为准。"
        )
        return {
            "answer": "\n".join(lines),
            "refs": [f"enrollment_plans#{year}/{province or '全国'}"],
            "intent": "plan",
        }

    if not province:
        return None

    query = db.query(EnrollmentPlan).filter(
        EnrollmentPlan.year == year,
        EnrollmentPlan.province == province,
        EnrollmentPlan.plan_count.isnot(None),
    )
    if major_type:
        query = query.filter(EnrollmentPlan.major_type == major_type)
    rows = query.all()
    if not rows:
        return None

    by_type = {}
    for row in rows:
        by_type.setdefault(row.major_type, []).append(row)
    majors_count = len({row.major_id for row in rows})
    scope = province + (f" · {major_type}" if major_type else "")
    lines = [
        f"按 {year} 年招生计划，{scope}共 {_plan_total(rows)} 人，"
        f"涉及 {majors_count} 个专业："
    ]
    if major_type:
        # 已按科类过滤，直接给计划类型明细，不再重复一遍科类名
        lines.extend(f"· {plan_type}：{total} 人" for plan_type, total in _sum_by_plan_type(rows))
    else:
        for name, items in sorted(by_type.items(), key=lambda kv: -_plan_total(kv[1])):
            detail = "、".join(
                f"{plan_type} {total} 人" for plan_type, total in _sum_by_plan_type(items)
            )
            lines.append(f"· {name}：{_plan_total(items)} 人（{detail}）")
    lines.append("想查某个具体专业招多少人，直接说专业名称即可。")
    return {
        "answer": "\n".join(lines),
        "refs": [f"enrollment_plans#{year}/{province}"],
        "intent": "plan",
    }


def _sum_by_plan_type(rows: List[EnrollmentPlan]) -> List[tuple]:
    totals = {}
    for row in rows:
        totals[row.plan_type] = totals.get(row.plan_type, 0) + (row.plan_count or 0)
    return sorted(totals.items(), key=lambda kv: -kv[1])


def _counts_by_major(db: Session, year: int, province: str, major_type: str) -> dict:
    """按专业汇总计划数，口径与 /api/school/plan-diff 一致。"""
    query = db.query(
        EnrollmentPlan.major_id, func.sum(EnrollmentPlan.plan_count)
    ).filter(EnrollmentPlan.year == year, EnrollmentPlan.plan_count.isnot(None))
    if province:
        query = query.filter(EnrollmentPlan.province == province)
    if major_type:
        query = query.filter(EnrollmentPlan.major_type == major_type)
    return {int(major_id): int(total or 0) for major_id, total in query.group_by(EnrollmentPlan.major_id).all()}


def build_plan_change_answer(
    db: Session, question: str, profile: Optional[StudentProfile] = None
) -> Optional[dict]:
    """招生计划同比：新增 / 停招 / 计划增减。"""
    if not any(marker in question for marker in CHANGE_MARKERS + ("增加", "减少", "多了", "少了")):
        return None
    if "计划" not in question and "招生" not in question and "招" not in question:
        return None

    years = [
        year
        for (year,) in db.query(EnrollmentPlan.year)
        .distinct()
        .order_by(EnrollmentPlan.year.desc())
        .limit(2)
        .all()
    ]
    if len(years) < 2:
        return None
    to_year, from_year = years[0], years[1]

    province = _extract_province(question)
    major_type = _extract_major_type(question)
    if not province or not major_type:
        profile_province, profile_major_type = _profile_scope(profile)
        province = province or profile_province
        major_type = major_type or profile_major_type
    scope = province or "全国"
    if major_type:
        scope += f" · {major_type}"

    from_counts = _counts_by_major(db, from_year, province, major_type)
    to_counts = _counts_by_major(db, to_year, province, major_type)
    if not from_counts and not to_counts:
        return None

    names = {
        row.id: (row.display_name or row.name)
        for row in db.query(Major).filter(Major.id.in_(set(from_counts) | set(to_counts))).all()
    }
    refs = [f"enrollment_plans#{from_year}vs{to_year}/{scope}"]

    majors = match_majors(db, question)
    if majors:
        lines = [f"{from_year} → {to_year} 年，{scope}的招生计划变化："]
        changed = False
        for major in majors:
            before = from_counts.get(major.id, 0)
            after = to_counts.get(major.id, 0)
            if not before and not after:
                continue
            changed = True
            label = major.display_name or major.name
            if before == after:
                lines.append(f"· {label}：{after} 人，与 {from_year} 年持平")
            else:
                sign = "+" if after > before else ""
                lines.append(f"· {label}：{before} 人 → {after} 人（{sign}{after - before}）")
        if not changed:
            return None
        return {"answer": "\n".join(lines), "refs": refs, "intent": "plan"}

    added = [(names.get(mid, f"专业#{mid}"), count) for mid, count in to_counts.items() if mid not in from_counts]
    removed = [(names.get(mid, f"专业#{mid}"), count) for mid, count in from_counts.items() if mid not in to_counts]
    changed = [
        (names.get(mid, f"专业#{mid}"), count - from_counts[mid], count)
        for mid, count in to_counts.items()
        if mid in from_counts and count != from_counts[mid]
    ]

    from_total, to_total = sum(from_counts.values()), sum(to_counts.values())
    delta = to_total - from_total
    sign = "+" if delta > 0 else ""
    lines = [
        f"{from_year} → {to_year} 年，{scope}招生计划 {from_total} 人 → {to_total} 人（{sign}{delta} 人）。"
    ]
    if added:
        detail = "、".join(f"{name} {count} 人" for name, count in sorted(added, key=lambda x: -x[1])[:5])
        lines.append(f"· 新增专业 {len(added)} 个：{detail}")
    if removed:
        detail = "、".join(f"{name} {count} 人" for name, count in sorted(removed, key=lambda x: -x[1])[:5])
        lines.append(f"· 停招专业 {len(removed)} 个：{detail}")
    if changed:
        detail = "、".join(
            f"{name} {after - step}→{after}（{'+' if step > 0 else ''}{step}）"
            for name, step, after in sorted(changed, key=lambda x: -abs(x[1]))[:5]
        )
        lines.append(f"· 计划调整 {len(changed)} 个：{detail}")
    if not (added or removed or changed):
        lines.append("· 各专业计划数与上年一致。")
    lines.append("以上为招生计划对比，最终以省考试院公布为准。")
    return {"answer": "\n".join(lines), "refs": refs, "intent": "plan"}
