"""分数问答：问句命中具体专业且是分数类问题时，直接查录取分数表作答。

不经过检索 + 大模型生成，避免出现“资料中未提供具体分数”这类无效回答。
"""

import re
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import (
    AdmissionScore,
    EnrollmentPlan,
    Major,
    Message,
    SchoolAdmissionScore,
    ScoreRankSegment,
    StudentProfile,
    User,
)

SCORE_KEYWORDS = (
    "多少分",
    "几分",
    "能上",
    "能不能上",
    "能录取",
    "录取吗",
    "能进",
    "能考上",
    "能报",
    "有希望",
    "位次",
    "排名",
    "分数线",
    "最低分",
    "录取线",
    "投档线",
    "分够",
    "分数",
)
MAX_MAJORS = 4
MAX_RECOMMEND = 8

# 问句没有命中具体专业时，命中这些词说明用户想按分数让系统推荐专业
RECOMMEND_KEYWORDS = (
    "哪些专业",
    "什么专业",
    "专业选择",
    "推荐",
    "能报什么",
    "可选专业",
    "适合",
    "合适",
    "匹配",
)

# 问句没有命中具体专业时，命中这些词则改用院校投档线兜底
SCHOOL_KEYWORDS = ("东北师范大学", "东北师大", "东师", "学校", "投档线")
SCHOOL_NAME = "东北师范大学"

# 分数匹配分步收集：省份 → 科类 → 分数 → 位次 → 是否接受调剂
SLOT_INTENT = "score_flow"
SLOT_PROMPT_PREFIX = "【志愿匹配】"
SLOT_TITLES = {
    "province": "省份",
    "major_type": "科类",
    "score": "分数",
    "rank": "位次",
    "adjust": "是否调剂",
}
BARE_SCORE_PATTERN = re.compile(r"(?<!\d)(\d{3})(?!\d)")
RANK_HINT_PATTERN = re.compile(r"(?:位次|排名|名次)\s*(?:约|是|大概|在|为|有)?\s*(\d{1,6})")
BARE_RANK_PATTERN = re.compile(r"^\s*(\d{1,6})\s*(?:名|位)?\s*$")
# 只写了分数又问专业，如“599可以报什么专业”
RECOMMEND_INTENT_PATTERN = re.compile(
    r"能报|可以报|报什么|哪些专业|什么专业|专业选择|可选专业|推荐|适合|合适|匹配"
)
# 考生只回“物理”“历史”“物化生”这类简称时，也要能识别出科类
_BARE_MAJOR_TYPE_PATTERNS = (
    ("物理类", re.compile(r"物理|理科|理工|物化|物生|物地")),
    ("历史类", re.compile(r"历史|文科|文史|史政|政史|史地")),
)
# 分步追问时，考生可能只回“物理”“物化生”这类简称
_SLOT_MAJOR_TYPE_PATTERNS = _BARE_MAJOR_TYPE_PATTERNS + (
    ("综合改革", re.compile(r"综合|不分文理")),
)
_UNKNOWN_REPLIES = ("不知道", "不清楚", "不记得", "不晓得", "忘记", "忘了", "没查", "没有", "暂无")
_QUESTION_MARKERS = ("？", "?", "多少", "吗", "怎么", "什么", "如何", "哪个", "哪些", "介绍", "区别")
_NEGATIVE_ADJUST = ("不接受", "不服从", "不愿意", "不同意", "不考虑", "不要", "不调剂", "不行", "否")
_POSITIVE_ADJUST = ("接受", "服从", "愿意", "同意", "可以", "都行", "无所谓", "是")
# 只回一个“不”字也算明确拒绝，但要和“不知道”区分开
_SHORT_NEGATIVE = ("不", "否")
# 分步追问的回溯轮数：每轮一问一答，留足 6 轮的余量
FLOW_LOOKBACK = 24

# 问句里直接带分数 / 位次，如“588分能录取吗”“位次8000能上吗”
INLINE_SCORE_PATTERN = re.compile(r"(\d{3})\s*分")
INLINE_RANK_PATTERN = re.compile(r"(?:位次|排名)\s*(\d{1,6})")
# “我分数598”“考了598”这类写法
SCORE_BEFORE_PATTERN = re.compile(r"(?:分数|考了|考)\s*在?\s*(\d{3})")

MISSING_PROFILE_ANSWER = (
    "不同省份的招生计划和位次要求差别很大，同一个分数在不同省份能报的专业完全不同。\n"
    "你是哪个省份的考生？比如“吉林”“河南”；如果方便，把科类也一起说，"
    "例如“吉林 物理类”，我就直接按你们省的往年数据回答。"
)

# 数据库收录的省份；问句里出现省份时优先按该省口径回答
PROVINCES = (
    "北京",
    "天津",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
)
# 高考综合改革省份：不区分物理类 / 历史类
COMPREHENSIVE_PROVINCES = ("上海", "浙江", "北京", "天津", "山东", "海南")
# 仍按理工 / 文史划分科类的省份
LEGACY_PROVINCES = ("新疆", "西藏")

_MAJOR_TYPE_PATTERNS = (
    ("物理类", re.compile(r"物理类|物理科|物理组合|选物理|理科|理工")),
    ("历史类", re.compile(r"历史类|历史科|历史组合|选历史|文科|文史")),
    ("综合改革", re.compile(r"综合改革|不分文理|综合类")),
)

# 考生只回一句“河南”“物理类”时，合并上一轮问题使用
MAX_SCOPE_REPLY_LEN = 12


def _extract_province(text: str) -> str:
    for province in sorted(PROVINCES, key=len, reverse=True):
        if province in text:
            return province
    return ""


def _extract_major_type(text: str) -> str:
    for name, pattern in _MAJOR_TYPE_PATTERNS:
        if pattern.search(text):
            return name
    # 再按简称兜底：“物理”“物化生”“历史”
    return _slot_major_type(text, _BARE_MAJOR_TYPE_PATTERNS)


def _finalize_major_type(province: str, major_type: str) -> str:
    """按省份口径调整科类：综合改革省份统一，新疆 / 西藏用理工 / 文史。"""
    if province in COMPREHENSIVE_PROVINCES:
        return "综合改革"
    if province in LEGACY_PROVINCES:
        if major_type == "物理类":
            return "理工"
        if major_type == "历史类":
            return "文史"
    return major_type


def _ask_major_type_answer(province: str) -> str:
    return (
        f"已按{province}查询，但{province}的考生要分物理类 / 历史类，"
        "两类的分数线和位次差别很大。你是物理类还是历史类？"
    )


def _recent_messages(
    db: Session, conversation_id: Optional[int], limit: int = 12
) -> List[Message]:
    if not conversation_id:
        return []
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )


def _recent_province(db: Session, conversation_id: Optional[int]) -> str:
    """本会话里考生明确说过的省份，避免每轮都重复问。"""
    for row in _recent_messages(db, conversation_id):
        if row.role == "user":
            province = _extract_province(row.content or "")
            if province:
                return province
    return ""


def _recent_major_type(db: Session, conversation_id: Optional[int]) -> str:
    """本会话里考生明确说过的科类，避免“物理”答过还要再问一次。"""
    for row in _recent_messages(db, conversation_id):
        if row.role == "user":
            major_type = _extract_major_type(row.content or "")
            if major_type:
                return major_type
    return ""


def _awaiting_scope(db: Session, conversation_id: Optional[int]) -> bool:
    """上一轮助手是否在追问省份 / 科类。"""
    for row in _recent_messages(db, conversation_id):
        if row.role == "assistant":
            content = row.content or ""
            return "哪个省份" in content or ("物理类" in content and "历史类" in content)
    return False


def _effective_question(db: Session, conversation_id: Optional[int], question: str) -> str:
    """考生只补充了省份 / 科类时，把上一轮的成绩问题合并进来再解析。"""
    if is_score_question(question):
        return question
    if len(question) > MAX_SCOPE_REPLY_LEN:
        return question
    if not (_extract_province(question) or _extract_major_type(question)):
        return question
    if not _awaiting_scope(db, conversation_id):
        return question
    for row in _recent_messages(db, conversation_id):
        if row.role == "user" and is_score_question(row.content or ""):
            return f"{row.content} {question}"
    return question


def _resolve_scope(
    db: Session, user: User, question: str, conversation_id: Optional[int] = None
) -> tuple:
    """解析省份 / 科类：问句 > 本会话近期 > 考生资料，都没有再追问。"""
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    province = _extract_province(question) or _recent_province(db, conversation_id)
    major_type = _extract_major_type(question) or _recent_major_type(db, conversation_id)
    # 资料里的省份作为兜底，已填过就不用再问一遍
    if not province and profile and profile.province in PROVINCES:
        province = profile.province
    if not major_type and profile:
        major_type = _normalize_major_type(profile.subject_combo)
    return province, _finalize_major_type(province, major_type), profile


def is_score_question(text: str) -> bool:
    if any(keyword in text for keyword in SCORE_KEYWORDS):
        return True
    if (
        INLINE_SCORE_PATTERN.search(text)
        or INLINE_RANK_PATTERN.search(text)
        or SCORE_BEFORE_PATTERN.search(text)
    ):
        return True
    # “599可以报什么专业”：问句只给了分数，靠推荐意图判断
    return bool(RECOMMEND_INTENT_PATTERN.search(text) and _extract_score_value(text))


def _base_name(name: str) -> str:
    """去掉“（中外合作办学）”这类后缀，只保留专业本名。"""
    return re.split(r"[（(]", name)[0].strip()


def _match_strength(base: str, text: str) -> int:
    if len(base) < 2:
        return 0
    if base in text:
        return 100
    # “计算机”这类简称：从长到短找专业名前缀是否出现在问句里
    for size in range(min(len(base), 8), 2, -1):
        if base[:size] in text:
            return 10 + size
    return 0


def match_majors(db: Session, text: str) -> List[Major]:
    scored = []
    for row in db.query(Major).all():
        strength = _match_strength(_base_name(row.name), text)
        if strength:
            scored.append((strength, row))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    majors, seen = [], set()
    for _, row in scored:
        if row.id in seen:
            continue
        seen.add(row.id)
        majors.append(row)
        if len(majors) >= MAX_MAJORS:
            break
    return majors


def _normalize_major_type(value: str | None) -> str:
    """把画像里的选科组合归一到录取数据里的科类；无法判断时返回空串。"""
    text = (value or "").strip()
    if not text:
        return ""
    if "综合" in text:
        return "综合改革"
    if "历史" in text or "史" in text or "文" in text:
        return "历史类"
    if "物理" in text or text.startswith("物") or "理" in text:
        return "物理类"
    return ""


def _collect_scores(
    db: Session, major_ids: List[int], province: str, major_type: str, year: int
) -> Dict[int, List[AdmissionScore]]:
    rows = (
        db.query(AdmissionScore)
        .filter(
            AdmissionScore.major_id.in_(major_ids),
            AdmissionScore.province == province,
            AdmissionScore.major_type == major_type,
            AdmissionScore.year == year,
            AdmissionScore.min_score.isnot(None),
        )
        .order_by(AdmissionScore.min_score.desc())
        .all()
    )
    grouped: Dict[int, List[AdmissionScore]] = {}
    for row in rows:
        grouped.setdefault(row.major_id, []).append(row)
    for major_rows in grouped.values():
        major_rows.sort(
            key=lambda row: (
                0 if "一般计划" in (row.plan_type or "") else 1,
                -(row.min_score or 0),
            )
        )
    return grouped


def _reference_row(rows: List[AdmissionScore]) -> AdmissionScore:
    """做冲稳保判断时优先用一般计划，没有就取最低分那条。"""
    for row in rows:
        if "一般计划" in (row.plan_type or ""):
            return row
    return rows[-1]


# 冲刺志愿的分数上限差：高于考生成绩超过 10 分就不建议冲
SPRINT_SCORE_GAP = 10


def _alternative_majors(
    db: Session,
    province: str,
    major_type: str,
    year: int,
    candidate_score: int,
    exclude_ids: List[int],
) -> List[tuple]:
    """同省招生计划内、最低分在考生成绩 0~10 分之间的其他专业。"""
    plan_year = (
        db.query(func.max(EnrollmentPlan.year))
        .filter(
            EnrollmentPlan.province == province,
            EnrollmentPlan.major_type == major_type,
        )
        .scalar()
    )
    if plan_year is None:
        return []
    plan_ids = (
        db.query(EnrollmentPlan.major_id)
        .filter(
            EnrollmentPlan.province == province,
            EnrollmentPlan.major_type == major_type,
            EnrollmentPlan.year == plan_year,
        )
        .distinct()
    )
    query = (
        db.query(
            Major.display_name,
            Major.name,
            AdmissionScore.min_score,
            AdmissionScore.min_rank,
        )
        .join(AdmissionScore, AdmissionScore.major_id == Major.id)
        .filter(
            AdmissionScore.major_id.in_(plan_ids),
            AdmissionScore.year == year,
            AdmissionScore.province == province,
            AdmissionScore.major_type == major_type,
            AdmissionScore.min_score.isnot(None),
            AdmissionScore.min_score >= candidate_score,
            AdmissionScore.min_score <= candidate_score + SPRINT_SCORE_GAP,
        )
        .order_by(AdmissionScore.min_score)
    )
    if exclude_ids:
        query = query.filter(Major.id.notin_(exclude_ids))

    alternatives: List[tuple] = []
    seen = set()
    for display, name, min_score, min_rank in query.limit(40):
        label = display or name
        if label in seen:
            continue
        seen.add(label)
        alternatives.append((label, min_score, min_rank))
        if len(alternatives) >= 5:
            break
    return alternatives


def _is_school_question(text: str) -> bool:
    return any(keyword in text for keyword in SCHOOL_KEYWORDS)


def _school_line(
    db: Session, province: str, major_type: str, year: int
) -> Optional[SchoolAdmissionScore]:
    """取某年院校最低投档线：优先本科批普通类，其次普通类，最后取全部最低分。"""
    rows = (
        db.query(SchoolAdmissionScore)
        .filter(
            SchoolAdmissionScore.year == year,
            SchoolAdmissionScore.province == province,
            SchoolAdmissionScore.major_type == major_type,
            SchoolAdmissionScore.min_score.isnot(None),
        )
        .all()
    )
    if not rows:
        return None
    preferred = [
        row
        for row in rows
        if (row.batch or "") == "本科批" and (row.plan_type or "") == "普通类"
    ]
    if not preferred:
        preferred = [row for row in rows if (row.plan_type or "") == "普通类"]
    if not preferred:
        preferred = rows
    return min(preferred, key=lambda row: (row.min_score or 0, row.min_rank or 10**9))


def _build_school_answer(
    db: Session, user: User, question: str, conversation_id: Optional[int] = None
) -> Dict:
    """院校层面兜底：按考生所在省份 + 科类回答学校最低投档线。"""
    province, major_type, profile = _resolve_scope(db, user, question, conversation_id)
    if not province:
        return {"answer": MISSING_PROFILE_ANSWER, "refs": []}
    if not major_type:
        return {"answer": _ask_major_type_answer(province), "refs": []}

    years = [
        row[0]
        for row in db.query(SchoolAdmissionScore.year)
        .filter(
            SchoolAdmissionScore.province == province,
            SchoolAdmissionScore.major_type == major_type,
            SchoolAdmissionScore.min_score.isnot(None),
        )
        .distinct()
        .order_by(SchoolAdmissionScore.year.desc())
        .limit(2)
        .all()
    ]
    if not years:
        return {
            "answer": f"暂未收录 {province} · {major_type} 的院校投档线，"
            "可以到「专业查询」页切换其他省份 / 科类查看。",
            "refs": [],
        }

    latest = _school_line(db, province, major_type, years[0])
    if latest is None:
        return {
            "answer": f"暂未收录 {province} · {major_type} 的院校投档线，"
            "可以到「专业查询」页切换其他省份 / 科类查看。",
            "refs": [],
        }

    lines: List[str] = []
    latest_rank = f" / 最低位次 {latest.min_rank}" if latest.min_rank else ""
    lines.append(
        f"以 {latest.year} 年 {province} · {major_type} 为例，{SCHOOL_NAME}最低投档线"
        f"（{latest.batch or '本科批'} · {latest.plan_type or '普通类'}）"
        f"{latest.min_score} 分{latest_rank}。"
    )

    if len(years) >= 2:
        previous = _school_line(db, province, major_type, years[1])
        if previous is not None:
            previous_rank = f" / 最低位次 {previous.min_rank}" if previous.min_rank else ""
            diff = latest.min_score - previous.min_score
            lines.append(
                f"{previous.year} 年同一口径为 {previous.min_score} 分{previous_rank}，"
                f"两年相差 {diff} 分（样本仅两年，仅供参考）。"
            )

    candidate_score, candidate_rank = _candidate_values(profile, question)

    # 位次优先于分数：位次口径更稳定
    if candidate_rank and latest.min_rank:
        if candidate_rank <= latest.min_rank:
            lines.append(
                f"根据往年录取判断：你的位次约 {candidate_rank}，比{SCHOOL_NAME}最低投档位次 "
                f"{latest.min_rank} 更靠前，按往年数据在投档线之上；"
                "但今年分数线有概率提高，建议作为稳妥志愿，并预留保底专业。"
            )
        else:
            lines.append(
                f"根据往年录取判断：你的位次约 {candidate_rank}，低于{SCHOOL_NAME}最低投档位次 "
                f"{latest.min_rank}，按往年数据把握不大；若今年分数线继续上涨，"
                "录取难度会更大，建议作为冲刺志愿。"
            )
    elif candidate_score:
        if candidate_score >= latest.min_score:
            lines.append(
                f"根据往年录取判断：你的分数（{candidate_score}）在最低投档线之上，"
                "但今年分数线有概率提高，建议作为稳妥志愿并预留保底。"
            )
        else:
            lines.append(
                f"根据往年录取判断：你的分数（{candidate_score}）低于最低投档线，"
                "若今年分数线继续上涨，录取难度会更大，建议作为冲刺志愿。"
            )

    lines.append("以上为往年录取数据，仅供参考；最终以当年招生计划、省控线和选科要求为准。")
    return {
        "answer": "\n".join(lines),
        "refs": [f"school_admission_scores#{latest.year}/{province}/{major_type}"],
    }


def _candidate_values(profile: Optional[StudentProfile], question: str) -> tuple:
    """考生分数 / 位次：问句里明确说了就用问句的，否则用画像。"""
    inline_rank = INLINE_RANK_PATTERN.search(question)
    inline_score = INLINE_SCORE_PATTERN.search(question) or SCORE_BEFORE_PATTERN.search(question)
    rank = (int(inline_rank.group(1)) if inline_rank else None) or (
        profile.rank_no if profile and profile.rank_no else None
    )
    score = (int(inline_score.group(1)) if inline_score else None) or (
        profile.score if profile and profile.score else None
    )
    return score, rank


def _extract_score_value(text: str) -> Optional[int]:
    """取高考分数：优先“598分”“分数598”，其次裸写的三位数。"""
    for pattern in (INLINE_SCORE_PATTERN, SCORE_BEFORE_PATTERN, BARE_SCORE_PATTERN):
        match = pattern.search(text or "")
        if match:
            value = int(match.group(1))
            if 100 <= value <= 750:
                return value
    return None


def _extract_rank_value(text: str) -> Optional[int]:
    match = RANK_HINT_PATTERN.search(text or "")
    return int(match.group(1)) or None if match else None


def _extract_adjust(text: str) -> Optional[bool]:
    """是否接受专业调剂：只有明确表态才返回布尔值。"""
    value = (text or "").strip()
    # “不知道 / 不清楚”是没主意，不是不接受
    if _is_unknown_reply(value):
        return None
    if "调剂" in value:
        if any(word in value for word in _NEGATIVE_ADJUST):
            return False
        if any(word in value for word in _POSITIVE_ADJUST):
            return True
        return None
    # “接受”“不行”这类一句话回答
    if len(value) > 6:
        return None
    if any(word in value for word in _NEGATIVE_ADJUST):
        return False
    if value in _SHORT_NEGATIVE:
        return False
    if any(word in value for word in _POSITIVE_ADJUST):
        return True
    return None


def _is_unknown_reply(text: str) -> bool:
    return any(word in (text or "") for word in _UNKNOWN_REPLIES)


def _slot_major_type(
    text: str, patterns: tuple = _SLOT_MAJOR_TYPE_PATTERNS
) -> str:
    for name, pattern in patterns:
        if pattern.search(text or ""):
            return name
    return ""


def _empty_slots() -> Dict:
    return {
        "province": "",
        "major_type": "",
        "score": None,
        "rank": None,
        "adjust": None,
        "score_unknown": False,
        "rank_unknown": False,
    }


def _parse_slots(
    texts: List[str], profile: Optional[StudentProfile] = None, pending: str = ""
) -> Dict:
    """从若干轮回答里抽取省份 / 科类 / 分数 / 位次 / 调剂，后说的覆盖先说的。"""
    slots = _empty_slots()
    values = [text or "" for text in texts]
    for index, text in enumerate(values):
        is_last = index == len(values) - 1
        province = _extract_province(text)
        if province:
            slots["province"] = province
        major_type = _extract_major_type(text)
        if not major_type and is_last and pending == "major_type":
            major_type = _slot_major_type(text)
        if major_type:
            slots["major_type"] = major_type
        score = _extract_score_value(text)
        if score:
            slots["score"] = score
        rank = _extract_rank_value(text)
        if rank is None and is_last and pending == "rank":
            match = BARE_RANK_PATTERN.match(text)
            if match:
                rank = int(match.group(1)) or None
            elif _is_unknown_reply(text):
                slots["rank_unknown"] = True
        if rank:
            slots["rank"] = rank
        if is_last and pending == "score" and not score and _is_unknown_reply(text):
            slots["score_unknown"] = True
        adjust = _extract_adjust(text)
        if adjust is not None:
            slots["adjust"] = adjust

    # 画像兜底：只补这一轮没说到的项，不覆盖考生当轮亲口说的内容
    if not pending and profile:
        if not slots["province"] and profile.province in PROVINCES:
            slots["province"] = profile.province
        if not slots["score"] and profile.score:
            slots["score"] = profile.score
        if not slots["rank"] and profile.rank_no:
            slots["rank"] = profile.rank_no
        if slots["adjust"] is None and profile.adjust_accepted is not None:
            slots["adjust"] = bool(profile.adjust_accepted)
        if not slots["major_type"]:
            slots["major_type"] = _normalize_major_type(profile.subject_combo)

    if slots["province"]:
        slots["major_type"] = _finalize_major_type(slots["province"], slots["major_type"])
    return slots


def _merge_slots(base: Dict, extra: Dict) -> Dict:
    merged = dict(base)
    for key in ("province", "major_type"):
        if extra.get(key):
            merged[key] = extra[key]
    for key in ("score", "rank", "adjust"):
        if extra.get(key) is not None:
            merged[key] = extra[key]
    merged["score_unknown"] = bool(base.get("score_unknown") or extra.get("score_unknown"))
    merged["rank_unknown"] = bool(base.get("rank_unknown") or extra.get("rank_unknown"))
    if merged["province"]:
        merged["major_type"] = _finalize_major_type(merged["province"], merged["major_type"])
    return merged


def _next_missing_slot(slots: Dict, skip_optional: bool = False) -> str:
    """按 省份 → 科类 → 分数 → 位次 → 调剂 的顺序返回下一个要问的槽位。

    skip_optional=True 时位次和调剂都按“不限”处理，不再追问。
    """
    if not slots["province"]:
        return "province"
    if not slots["major_type"]:
        return "major_type"
    if not slots["score"] and not slots["score_unknown"]:
        return "score"
    if skip_optional:
        return ""
    if not slots["rank"] and not slots["rank_unknown"]:
        return "rank"
    if slots["adjust"] is None:
        return "adjust"
    return ""


def _slot_question(slot: str, slots: Dict) -> str:
    prefix = SLOT_PROMPT_PREFIX
    summary = " · ".join(
        value for value in (slots["province"], slots["major_type"]) if value
    )
    if slot == "province":
        return (
            f"{prefix}为按往年录取数据匹配专业，先确认几项信息。\n"
            "你是哪个省份的考生？（例如“吉林”“河南”）"
        )
    if slot == "major_type":
        if slots["province"] in LEGACY_PROVINCES:
            return f"{prefix}已记录：{summary}。\n你是理工类还是文史类？"
        return (
            f"{prefix}已记录：{summary}。\n"
            "你的选科属于物理类还是历史类？（例如“物理类”“物化生”）"
        )
    if slot == "score":
        return (
            f"{prefix}已记录：{summary}。\n"
            "你的高考分数是多少？（例如 599）"
        )
    if slot == "rank":
        score_text = f"{slots['score']} 分" if slots["score"] else "未提供"
        return (
            f"{prefix}已记录：{summary}，分数 {score_text}。\n"
            "你的位次（全省排名）是多少？不清楚可以回复“不知道”，"
            "我会跳过位次、按分数帮你匹配专业。"
        )
    return (
        f"{prefix}最后一步：\n"
        "志愿表里是否接受专业调剂？（回复“接受”或“不接受”）"
    )


def _advance_slots(db: Session, slots: Dict, skip_optional: bool = False) -> Dict:
    pending = _next_missing_slot(slots, skip_optional)
    if pending:
        return {"answer": _slot_question(pending, slots), "refs": [], "intent": SLOT_INTENT}
    return _recommend_by_slots(db, slots)


def _is_recommend_question(text: str) -> bool:
    return any(keyword in text for keyword in RECOMMEND_KEYWORDS)


def _wants_major_match(text: str) -> bool:
    """有推荐意图、且明确在问专业 / 志愿，才按资料去匹配（或追问缺口）。"""
    if not _is_recommend_question(text):
        return False
    return "专业" in text or "志愿" in text


def _profile_recommend_ready(
    question: str, profile: Optional[StudentProfile]
) -> bool:
    """考生资料已够匹配（省份 + 科类 + 分数 / 位次）且问句在问专业 / 志愿。"""
    if profile is None:
        return False
    if not _wants_major_match(question):
        return False
    if profile.province not in PROVINCES:
        return False
    major_type = _finalize_major_type(
        profile.province, _normalize_major_type(profile.subject_combo)
    )
    if not major_type:
        return False
    return bool(profile.score or profile.rank_no)


def _build_recommendation(
    db: Session,
    user: User,
    question: str,
    conversation_id: Optional[int] = None,
    profile: Optional[StudentProfile] = None,
) -> Optional[Dict]:
    """按分数推荐专业：信息不全时逐项追问，齐全后给出匹配结果。"""
    # 既没提推荐意图也没给分数 / 位次的，交给检索链路，避免把普通问句变成追问
    if not _is_recommend_question(question) and not (
        _extract_score_value(question) or _extract_rank_value(question)
    ):
        return None
    if profile is None:
        profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    slots = _parse_slots([question], profile)
    # 资料已经够匹配时，位次和调剂按“不限”处理，直接给结果
    return _advance_slots(db, slots, skip_optional=_profile_recommend_ready(question, profile))


def _conversation_tail(db: Session, conversation_id: Optional[int]) -> List[Message]:
    if not conversation_id:
        return []
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(FLOW_LOOKBACK)
        .all()
    )
    return list(reversed(rows))


def _flow_state(db: Session, conversation_id: Optional[int]) -> Optional[List[str]]:
    """上一轮若在追问志愿信息，返回 [触发问句, 之后的回答...]；否则返回 None。"""
    if not conversation_id:
        return None
    rows = _conversation_tail(db, conversation_id)
    if not rows or rows[-1].role != "assistant" or rows[-1].intent != SLOT_INTENT:
        return None
    index = len(rows) - 1
    while (
        index >= 0
        and rows[index].role == "assistant"
        and rows[index].intent == SLOT_INTENT
    ):
        index -= 2
    trigger = index + 1
    if trigger >= len(rows) or rows[trigger].role != "user":
        return None
    texts = [rows[trigger].content or ""]
    texts.extend(row.content or "" for row in rows[trigger + 1 :] if row.role == "user")
    return texts


def _is_slot_reply(text: str) -> bool:
    """判断本轮消息是不是在回答追问，而不是另起一个新问题。"""
    parsed = _parse_slots([text])
    if (
        parsed["province"]
        or parsed["major_type"]
        or parsed["score"]
        or parsed["rank"]
        or parsed["adjust"] is not None
    ):
        return True
    if _is_unknown_reply(text):
        return True
    return len(text) <= MAX_SCOPE_REPLY_LEN and not any(
        marker in text for marker in _QUESTION_MARKERS
    )


def _replay_flow(texts: List[str], profile: Optional[StudentProfile]) -> Dict:
    """按轮次回放：每轮先算还缺哪个槽位，再解析该轮回答。"""
    slots = _parse_slots([texts[0]], profile)
    for reply in texts[1:]:
        pending = _next_missing_slot(slots)
        slots = _merge_slots(slots, _parse_slots([reply], profile, pending=pending))
    return slots


def _advance_flow(
    db: Session, texts: List[str], question: str, profile: Optional[StudentProfile]
) -> Dict:
    prior = _replay_flow(texts, profile)
    pending = _next_missing_slot(prior)
    current = _parse_slots([question], profile, pending=pending)
    return _advance_slots(db, _merge_slots(prior, current))


def _estimate_rank(
    db: Session, province: str, major_type: str, score: Optional[int]
) -> Optional[tuple]:
    """用一分一段表把分数换算成位次，返回 (年份, 位次)；没有该省数据返回 None。"""
    if not (province and major_type and score):
        return None
    row = (
        db.query(ScoreRankSegment)
        .filter(
            ScoreRankSegment.province == province,
            ScoreRankSegment.major_type == major_type,
            ScoreRankSegment.score <= score,
        )
        .order_by(ScoreRankSegment.year.desc(), ScoreRankSegment.score.desc())
        .first()
    )
    return (row.year, row.cumulative_count) if row else None


def _recommend_by_slots(db: Session, slots: Dict) -> Dict:
    """五个槽位齐全后，按往年一般计划录取数据匹配专业。"""
    province = slots["province"]
    major_type = slots["major_type"]
    candidate_score = slots["score"]
    candidate_rank = slots["rank"]
    adjust = slots["adjust"]

    if not candidate_score and not candidate_rank:
        return {
            "answer": "分数和位次都没有提供，暂时无法匹配。"
            "可以到「专业查询」页按省份 / 科类查看往年录取分数。",
            "refs": [],
            "intent": "score",
        }

    score_year = (
        db.query(func.max(AdmissionScore.year))
        .filter(
            AdmissionScore.province == province,
            AdmissionScore.major_type == major_type,
        )
        .scalar()
    )
    if score_year is None:
        return {
            "answer": f"暂未收录 {province} · {major_type} 的录取分数，"
            "可以到「专业查询」页切换其他省份 / 科类查看。",
            "refs": [],
            "intent": "score",
        }

    # 只推今年（最新计划年份）确实在招的一般计划专业
    plan_year = (
        db.query(func.max(EnrollmentPlan.year))
        .filter(
            EnrollmentPlan.province == province,
            EnrollmentPlan.major_type == major_type,
        )
        .scalar()
    )
    plan_ids: List[int] = []
    if plan_year is not None:
        plan_ids = [
            row[0]
            for row in db.query(EnrollmentPlan.major_id)
            .filter(
                EnrollmentPlan.province == province,
                EnrollmentPlan.major_type == major_type,
                EnrollmentPlan.year == plan_year,
                EnrollmentPlan.plan_type.like("%一般计划%"),
            )
            .distinct()
            .all()
        ]

    # 没填位次时用一分一段表补一个估算值，仅用于展示和说明，筛选仍以分数为准
    estimated = _estimate_rank(db, province, major_type, candidate_score)
    if candidate_rank:
        base, rank_note = f"你的位次约 {candidate_rank}", ""
    elif estimated:
        base = (
            f"你的分数是 {candidate_score} 分"
            f"（按 {estimated[0]} 年{province}一分一段表，位次约 {estimated[1]}）"
        )
        rank_note = f"位次是按 {estimated[0]} 年{province}一分一段表估算的，仅供参考。"
    else:
        base = f"你的分数是 {candidate_score} 分（未提供位次）"
        rank_note = "未提供位次，且暂未收录该省一分一段表，以下按分数匹配。"

    # 只用一般计划（普通类）做推荐：预科班、专项、中外合作对考生不通用
    query = db.query(AdmissionScore).filter(
        AdmissionScore.province == province,
        AdmissionScore.major_type == major_type,
        AdmissionScore.year == score_year,
        AdmissionScore.min_score.isnot(None),
        AdmissionScore.plan_type.like("%一般计划%"),
    )
    if plan_ids:
        query = query.filter(AdmissionScore.major_id.in_(plan_ids))
    rows = query.order_by(AdmissionScore.min_score.desc()).all()
    best: Dict[int, AdmissionScore] = {}
    for row in rows:
        best.setdefault(row.major_id, row)
    if not best:
        return {
            "answer": f"{score_year} 年 {province} · {major_type} 暂未收录一般计划的专业录取分数，"
            "可以到「专业查询」页换省份 / 科类查看。",
            "refs": [],
            "intent": "score",
        }

    majors = {
        row.id: row
        for row in db.query(Major).filter(Major.id.in_(list(best.keys()))).all()
    }
    entries = []
    for major_id, row in best.items():
        major = majors.get(major_id)
        if major is None:
            continue
        # 位次优先：位次数值越大，说明门槛越低
        if candidate_rank and row.min_rank:
            qualified = row.min_rank >= candidate_rank
        else:
            qualified = bool(candidate_score) and row.min_score <= candidate_score
        if qualified:
            entries.append((major, row))

    if not entries:
        lowest = min(best.values(), key=lambda row: row.min_score or 0)
        answer = (
            f"{base}。\n"
            f"按 {score_year} 年 {province} · {major_type} 的一般计划数据，"
            f"最低分最低的专业也要 {lowest.min_score} 分，"
            f"你提供的成绩暂时够不上；可以到「专业查询」页换科类，或查看其他批次 / 省份。"
        )
        if rank_note:
            answer = f"{rank_note}\n{answer}"
        return {
            "answer": answer,
            "refs": [f"admission_scores#{score_year}/{province}/{major_type}"],
            "intent": "score",
        }

    entries.sort(key=lambda item: -(item[1].min_score or 0))
    total = len(entries)
    shown = entries[:MAX_RECOMMEND]

    confirm = f"已确认：{province} · {major_type}，{base}"
    if adjust is not None:
        confirm += f"，{'接受专业调剂' if adjust else '不接受专业调剂'}"
    confirm += "。"

    if plan_year is not None:
        basis = (
            f"以 {plan_year} 年招生计划、{score_year} 年一般计划录取数据为准，"
        )
    else:
        basis = f"以 {score_year} 年一般计划录取数据为准，"
    lines: List[str] = [
        confirm,
        basis
        + f"按{candidate_rank and '位次' or '分数'}筛选出 {total} 个可报专业，"
        f"这里列出最低分最高的 {len(shown)} 个：",
    ]
    if rank_note:
        lines.insert(1, rank_note)

    refs: List[str] = []
    for index, (major, row) in enumerate(shown, start=1):
        label = major.display_name or major.name
        detail = f"{row.min_score} 分"
        if row.min_rank:
            detail += f" / 最低位次 {row.min_rank}"
        if candidate_score:
            detail += f"（比你的分数低 {candidate_score - (row.min_score or 0)} 分）"
        lines.append(f"{index}. {label}：{detail}")
        refs.append(f"admission_scores#{score_year}/{province}/{major_type}/{major.name}")

    close = [item for item in entries if candidate_score and candidate_score - (item[1].min_score or 0) <= 5]
    if close:
        lines.append(
            "其中排在最前面的几个与你的成绩只差 5 分以内，按往年数据属于冲刺区间；"
            "后面分差较大的可作为稳妥和保底志愿。"
        )
    else:
        if candidate_score:
            lines.append("以上专业最低分都低于你的成绩，可按分差从大到小安排稳妥与保底志愿。")
        else:
            lines.append("以上专业最低位次都在你之后，可按位次差从大到小安排稳妥与保底志愿。")
    if adjust is None:
        lines.append(
            "你还没有填写是否接受专业调剂，以上按“不限”处理；"
            "补充调剂意向后我可以给出更精确的志愿梯度建议。"
        )
    elif adjust:
        lines.append(
            "你接受专业调剂，建议在志愿表里勾选“服从专业调剂”，可以显著降低退档风险；"
            "分差 5 分以内的专业适合放在前两个志愿冲刺。"
        )
    else:
        lines.append(
            "你不接受专业调剂，退档风险会明显上升，建议只填分差 10 分以上的专业，"
            "并保留 1~2 个分差更大的保底专业。"
        )
    missing_plan = len(set(plan_ids)) - len(best)
    if plan_year is not None and missing_plan > 0:
        lines.append(
            f"{plan_year} 年招生计划里另有 {missing_plan} 个专业暂无往年录取分数，未计入上面的数量。"
        )
    lines.append("以上为往年一般计划数据，仅供参考；最终以当年招生计划、省控线和选科要求为准。")
    return {"answer": "\n".join(lines), "refs": refs, "intent": "score"}


def build_score_answer(
    db: Session, user: User, question: str, conversation_id: Optional[int] = None
) -> Optional[Dict]:
    """命中专业 + 分数意图时返回结构化回答，否则返回 None 走原链路。"""
    # 上一轮在追问志愿信息时，本轮回答直接续上，不再重新解析问句
    flow_texts = _flow_state(db, conversation_id)
    if flow_texts and _is_slot_reply(question):
        profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
        return _advance_flow(db, flow_texts, question, profile)

    question = _effective_question(db, conversation_id, question)
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    # 考生资料齐全且问句在问专业 / 志愿时，不依赖问句里的分数也直接匹配
    if not is_score_question(question) and not _wants_major_match(question):
        return None
    majors = match_majors(db, question)
    if not majors:
        # 没有命中专业时，院校 / 投档线类问题走院校投档线兜底
        if _is_school_question(question):
            return _build_school_answer(db, user, question, conversation_id)
        # “我 598 分能报哪些专业”这类问题按分数推荐专业
        return _build_recommendation(db, user, question, conversation_id, profile)

    province, major_type, profile = _resolve_scope(db, user, question, conversation_id)
    if not province:
        return {"answer": MISSING_PROFILE_ANSWER, "refs": []}
    if not major_type:
        return {"answer": _ask_major_type_answer(province), "refs": []}

    year = (
        db.query(func.max(AdmissionScore.year))
        .filter(
            AdmissionScore.province == province,
            AdmissionScore.major_type == major_type,
        )
        .scalar()
    )
    if year is None:
        return {
            "answer": f"暂未收录 {province} · {major_type} 的录取分数，"
            "可以到「专业查询」页切换其他省份 / 科类查看。",
            "refs": [],
        }

    grouped = _collect_scores(db, [major.id for major in majors], province, major_type, year)
    if not grouped:
        names = "、".join(major.display_name or major.name for major in majors)
        return {
            "answer": f"{year} 年 {province} · {major_type} 暂未收录「{names}」的录取分数，"
            "可以在「专业查询」页换年份 / 省份 / 科类再查。",
            "refs": [],
        }

    lines: List[str] = []
    lines.append(f"以 {year} 年 {province} · {major_type} 的录取数据为例：")

    # 一般计划（普通类）排在前面，作为主要参考
    entries = [(major, grouped[major.id]) for major in majors if grouped.get(major.id)]
    entries.sort(
        key=lambda item: (
            0 if any("一般计划" in (row.plan_type or "") for row in item[1]) else 1,
            -(item[1][0].min_score or 0),
        )
    )

    refs: List[str] = []
    for index, (major, rows) in enumerate(entries, start=1):
        parts = [
            f"{row.plan_type} 最低分 {row.min_score}"
            + (f" / 最低位次 {row.min_rank}" if row.min_rank else "")
            for row in rows
        ]
        lines.append(f"{index}. {major.display_name or major.name}：" + "；".join(parts))
        refs.append(f"admission_scores#{year}/{province}/{major_type}/{major.name}")

    reference_major, reference_rows = entries[0]
    reference_row = _reference_row(reference_rows)
    major_label = reference_major.display_name or reference_major.name
    min_score = reference_row.min_score
    min_rank = reference_row.min_rank

    candidate_score, candidate_rank = _candidate_values(profile, question)
    rank_note = (
        f"，你的位次约 {candidate_rank}、该专业最低位次 {min_rank}"
        if candidate_rank and min_rank
        else ""
    )

    if candidate_score and min_score:
        gap = min_score - candidate_score
        if gap <= 0:
            lines.append(
                f"根据往年录取判断：你的分数（{candidate_score}）高于「{major_label}」录取最低分（{min_score}）"
                f"{-gap} 分{rank_note}，建议把它作为稳妥志愿，并预留保底专业。"
            )
        elif gap <= SPRINT_SCORE_GAP:
            lines.append(
                f"根据往年录取判断：你的分数（{candidate_score}）低于「{major_label}」录取最低分（{min_score}）"
                f"{gap} 分{rank_note}，在 {SPRINT_SCORE_GAP} 分冲刺区间内，可以作为冲刺志愿。"
            )
        else:
            lines.append(
                f"根据往年录取判断：你的分数（{candidate_score}）低于「{major_label}」录取最低分（{min_score}）"
                f"{gap} 分，超出 {SPRINT_SCORE_GAP} 分冲刺区间，不建议把它作为冲刺志愿。"
            )
            alternatives = _alternative_majors(
                db,
                province,
                major_type,
                year,
                candidate_score,
                [major.id for major in majors],
            )
            if alternatives:
                lines.append(
                    f"同省招生计划内、最低分在你成绩以上 {SPRINT_SCORE_GAP} 分以内的其他专业："
                )
                for index, (label, score, rank) in enumerate(alternatives, start=1):
                    lines.append(
                        f"{index}. {label}：最低分 {score}"
                        + (f" / 最低位次 {rank}" if rank else "")
                    )
                lines.append("这些专业与你的分数更接近，建议作为冲刺志愿考虑。")
            else:
                lines.append(
                    f"同省同科类暂未找到最低分在你成绩以上 {SPRINT_SCORE_GAP} 分以内的专业，"
                    "可考虑把该专业放在稳妥或保底位置，或关注其他专业。"
                )
    elif candidate_rank and min_rank:
        if candidate_rank <= min_rank:
            lines.append(
                f"根据往年录取判断：你的位次约 {candidate_rank}，比「{major_label}」最低位次 {min_rank} 更靠前，"
                "按往年数据有把握；但今年分数线有概率提高，建议作为稳妥志愿，并预留保底专业。"
            )
        else:
            lines.append(
                f"根据往年录取判断：你的位次约 {candidate_rank}，低于「{major_label}」最低位次 {min_rank}，"
                "按往年数据把握不大，可放在冲刺位置；若分数差距过大，建议改选同省其他专业。"
            )

    lines.append("以上为往年录取数据，仅供参考；最终以当年招生计划、省控线和选科要求为准。")
    return {"answer": "\n".join(lines), "refs": refs}
