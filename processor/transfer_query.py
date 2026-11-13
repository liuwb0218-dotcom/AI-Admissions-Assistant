"""转专业直答：先问意向专业，再给出学校统一条件和该专业所属学院。"""

from typing import List, Optional

from sqlalchemy.orm import Session

from database.models import Major, Message, User
from processor.score_query import match_majors

# 触发词与追问轮次的 intent 标记
TRANSFER_MARKERS = ("转专业", "转系", "换专业", "转入专业")
TRANSFER_INTENT = "transfer_slot"

# 学校统一条件，来自《东北师范大学全日制本科生转专业实施办法》
BASIC_CONDITIONS = (
    "在读一、二年级的本科非定向生；"
    "所读专业不属于艺术类、体育类、中外合作办学等特殊招生专业；"
    "在校期间无违法违纪行为、未受过处分，且从未转过专业；"
    "身体条件符合转入专业的体检标准。"
)
QUOTA_RULES = (
    "各专业每年春季学期组织一次，计划接收人数原则上不低于本专业一年级在读人数的 5%，"
    "转专业名额一般不超过该专业当年招生人数的 10%；二年级申请原则上须留一级。"
)


def is_transfer_question(question: str) -> bool:
    return any(marker in (question or "") for marker in TRANSFER_MARKERS)


def _in_transfer_flow(db: Session, conversation_id: Optional[int]) -> bool:
    """上一轮助手是否在追问“想转入哪个专业”。"""
    if not conversation_id:
        return False
    row = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
        )
        .order_by(Message.id.desc())
        .first()
    )
    return bool(row and row.intent == TRANSFER_INTENT)


def _ask_major_answer(retry: bool = False) -> dict:
    if retry:
        answer = (
            "没有识别到具体的专业名称。可以告诉我你想转入的专业，"
            "例如“化学”“计算机类”，我再说明该专业所属学院和转入要求。"
        )
    else:
        answer = (
            "转专业一般在大一学年结束后申请，每学年春季学期组织一次。"
            "你想转入哪个专业？告诉我专业名称，我再说明该专业所属学院和转入要求。"
        )
    return {"answer": answer, "refs": [], "intent": TRANSFER_INTENT}


def _requirement_answer(majors: List[Major]) -> dict:
    labels = list(dict.fromkeys(major.display_name or major.name for major in majors))[:3]
    departments = list(dict.fromkeys(major.department for major in majors if major.department))
    lines = []
    if departments:
        lines.append(f"「{'、'.join(labels)}」所属：{'、'.join(departments)}。")
    else:
        lines.append(f"「{'、'.join(labels)}」所属学院暂未收录。")
    lines.append(f"学校统一条件：{BASIC_CONDITIONS}")
    lines.append(f"名额与时间：{QUOTA_RULES}")
    lines.append(
        "具体准入细则、考核方式和笔试面试科目由学院每年春季学期公布，"
        "以教务处和学院当年通知为准；可查看学院官网“本科生教育”栏目。"
    )
    return {
        "answer": "\n".join(lines),
        "refs": [
            "转专业与辅修政策#转专业与辅修政策",
            "转专业实施办法#东北师范大学全日制本科生转专业实施办法",
        ],
        "intent": "transfer",
    }


def build_transfer_answer(
    db: Session, question: str, conversation_id: Optional[int] = None,
    user: Optional[User] = None,
) -> Optional[dict]:
    """转专业问题：没给专业先追问，给了专业再说明条件。"""
    in_flow = _in_transfer_flow(db, conversation_id)
    if not in_flow and not is_transfer_question(question):
        return None

    majors = match_majors(db, question)
    if majors:
        return _requirement_answer(majors)
    if in_flow:
        # 明显是新问题就退出追问，交给后续链路
        if len(question) > 12 or any(marker in question for marker in ("？", "什么", "怎么", "多少")):
            return None
        return _ask_major_answer(retry=True)
    return _ask_major_answer()
