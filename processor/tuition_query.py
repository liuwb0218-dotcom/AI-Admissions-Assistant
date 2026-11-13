"""学费直答：招生计划里有逐专业学费，直接查库回答，避免落到“资料中未查到”。"""

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import EnrollmentPlan
from processor.score_query import match_majors

TUITION_MARKERS = ("学费", "学费标准", "收费")


def _money(value: Optional[int]) -> str:
    # 学费为 0 表示公费师范 / 优师专项等免费计划
    return "免学费" if not value else f"{value} 元/学年"


def _range(low: Optional[int], high: Optional[int]) -> str:
    if low == high:
        return _money(low)
    if not low:
        return f"0~{high} 元/学年（含免学费计划）"
    return f"{low}~{high} 元/学年"


def build_tuition_answer(
    db: Session, question: str, context_question: str = ""
) -> Optional[dict]:
    if not any(marker in question for marker in TUITION_MARKERS):
        return None

    year = db.query(func.max(EnrollmentPlan.year)).scalar()
    if year is None:
        return None

    majors = match_majors(db, question)
    from_context = False
    if not majors and context_question:
        # 追问“学费呢”本身没有专业，承接上一条问题里的专业
        majors = match_majors(db, context_question)
        from_context = bool(majors)
    if majors:
        # 同名专业可能拆成普通类 / 公费师范等多条，逐条列出，避免只答其中一个
        blocks = []
        refs = []
        for major in majors:
            rows = (
                db.query(
                    EnrollmentPlan.plan_type,
                    func.min(EnrollmentPlan.tuition),
                    func.max(EnrollmentPlan.tuition),
                )
                .filter(
                    EnrollmentPlan.major_id == major.id,
                    EnrollmentPlan.year == year,
                    EnrollmentPlan.tuition.isnot(None),
                )
                .group_by(EnrollmentPlan.plan_type)
                .order_by(func.min(EnrollmentPlan.tuition))
                .all()
            )
            if not rows:
                continue
            label = major.display_name or major.name
            blocks.append(
                "\n".join([f"{label}："] + [f"· {pt}：{_range(low, high)}" for pt, low, high in rows])
            )
            refs.append(f"enrollment_plans#{year}/{major.name}")
        if not blocks:
            return None
        header = f"按 {year} 年招生计划：" + ("（承接上一条问题中的专业）" if from_context else "")
        lines = [header] + blocks
        lines.append("以上为招生计划中的学费标准，不含住宿费；最终以当年入学缴费通知为准。")
        return {"answer": "\n".join(lines), "refs": refs, "intent": "plan"}

    rows = (
        db.query(
            EnrollmentPlan.plan_type,
            func.min(EnrollmentPlan.tuition),
            func.max(EnrollmentPlan.tuition),
            func.count(EnrollmentPlan.id),
        )
        .filter(EnrollmentPlan.year == year, EnrollmentPlan.tuition.isnot(None))
        .group_by(EnrollmentPlan.plan_type)
        .order_by(func.min(EnrollmentPlan.tuition))
        .all()
    )
    if not rows:
        return None

    lines = [f"按 {year} 年招生计划，各计划类型的学费标准如下："]
    lines.extend(
        f"· {plan_type}：{_range(low, high)}（{count} 条专业记录）"
        for plan_type, low, high, count in rows
    )
    lines.append("同一专业在不同省份 / 计划类型下学费可能不同；告诉我具体专业名称，我可以查该专业的学费。")
    return {"answer": "\n".join(lines), "refs": [f"enrollment_plans#{year}"], "intent": "plan"}
