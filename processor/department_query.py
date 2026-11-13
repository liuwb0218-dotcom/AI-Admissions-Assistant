"""专业归属直答：回答“XX 专业属于哪个学院”，数据来自 majors.department。"""

from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from database.models import Major, User
from processor.score_query import match_majors

# 问句里出现这些表述才按“专业归属”直答，避免抢走分数、计划类问题
DEPARTMENT_PATTERNS = (
    "哪个学院",
    "什么学院",
    "哪个学部",
    "什么学部",
    "哪个院",
    "哪个系",
    "什么系",
    "属于哪",
    "归属",
)


def is_department_question(question: str) -> bool:
    text = (question or "").strip()
    if not text or len(text) > 60:
        return False
    return any(keyword in text for keyword in DEPARTMENT_PATTERNS)


def _label(major: Major) -> str:
    return major.display_name or major.name


def build_department_answer(
    db: Session, question: str, user: Optional[User] = None
) -> Optional[dict]:
    """专业 -> 学院直答；没匹配到专业时返回 None，归属缺失时给出提示。"""
    if not is_department_question(question):
        return None

    majors = match_majors(db, question)
    if not majors:
        return None

    groups: Dict[str, List[str]] = {}
    unknown: List[str] = []
    for major in majors:
        department = (major.department or "").strip()
        if not department:
            unknown.append(_label(major))
            continue
        names = groups.setdefault(department, [])
        if _label(major) not in names:
            names.append(_label(major))

    if not groups:
        return {
            "answer": f"暂未收录「{'、'.join(unknown[:4])}」的学院归属，"
            "可查看招生网或咨询招生办确认。",
            "refs": [],
            "intent": "department",
        }

    lines = []
    for department, names in groups.items():
        shown = "、".join(names[:6])
        suffix = f" 等 {len(names)} 个专业" if len(names) > 6 else ""
        if "、" in department:
            lines.append(f"{shown}{suffix}由{department}共同开设。")
        else:
            lines.append(f"{shown}{suffix}属于{department}。")
    if unknown:
        lines.append(f"另有 {'、'.join(unknown[:4])} 暂未收录学院归属，可查看招生网或咨询招生办。")
    if any(keyword in question for keyword in ("哪个系", "什么系", "哪些系", "系里")):
        lines.append(
            "系一级的划分暂未收录，目前可确认到学院；"
            "具体系所设置可查看学院官网的“机构设置”栏目。"
        )

    departments = "、".join(groups)
    return {
        "answer": "\n".join(lines),
        "refs": [f"majors.department#{departments[:80]}"],
        "intent": "department",
    }
