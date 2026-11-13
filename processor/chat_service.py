"""问答链路：鉴权 → 敏感强匹配 → 意图识别 → 检索 → 生成 → 引用 → 落库。"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from config.errors import bad_request, forbidden, not_found
from config.settings import settings
from database.models import Conversation, Message, StudentProfile, User
from processor.catalog_query import build_catalog_answer
from processor.college_links import SCHOOL_WEBSITE, match_colleges
from processor.course_query import build_course_answer
from processor.department_query import build_department_answer
from processor.llm_client import LLMClient, LLMError
from processor.model_config import get_llm_config
from processor.plan_query import build_plan_answer, build_plan_change_answer
from processor.retriever import match_sensitive, retrieve
from processor.score_query import SLOT_INTENT, build_score_answer
from processor.transfer_query import build_transfer_answer
from processor.tuition_query import build_tuition_answer

logger = logging.getLogger(__name__)

NO_EVIDENCE_ANSWER = (
    "抱歉，招生资料库中暂未检索到与您问题直接相关的内容。"
    "建议您查阅学校招生网，或通过“向学校提问”提交问题，招生办会统一答复。"
)

# 回答里出现这类措辞说明没给出具体结论，需要补一个可点击的入口
INSUFFICIENT_MARKERS = ("未查到", "未检索到", "暂未收录", "没有找到", "暂无相关")
# 回答提到招生入口时，直接附学校招生网
SCHOOL_SITE_MARKERS = ("招生网", "本科招生", "学校官网", "招生官网")


def _merge_links(*groups: Optional[List[Dict]]) -> List[Dict]:
    """合并问题与回答里命中的官网，按 url 去重。"""
    merged, seen = [], set()
    for group in groups:
        for link in group or []:
            url = link.get("url") if link else None
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(link)
    return merged[:3]


# 直答统一版式：去掉模板里残留的 Markdown 符号（数字与措辞不动）
def _clean_markdown(text: str) -> str:
    return "\n".join(
        line.replace("**", "").replace("##", "").strip()
        for line in (text or "").splitlines()
        if line.strip()
    )


def _normalize_direct_answer(answer: str) -> str:
    """单段直答按提示词要求改成分点：· 列表 → 1. 2. 3.，正文内容保持原样。"""
    lines, index = [], 0
    for line in _clean_markdown(answer).splitlines():
        if line.startswith(("·", "•", "- ", "* ")):
            index += 1
            lines.append(f"{index}. {line.lstrip('·•-* ').strip()}")
        else:
            index = 0
            lines.append(line)
    return "\n".join(lines)


def _combine_answers(sections: List[tuple]) -> Optional[Dict]:
    """一个问题包含多个子问题时，按 归属 → 分数 → 学费 → 计划 的顺序依次回答。"""
    if not sections:
        return None
    if len(sections) == 1:
        item = sections[0][1]
        return {**item, "answer": _normalize_direct_answer(item["answer"])}
    lines, refs = [], []
    for index, (label, item) in enumerate(sections, start=1):
        lines.append(f"{index}. {label}")
        # 多段回答已有 1./2. 分段，段内保留 · 列表，避免编号互相冲突
        lines.append(_clean_markdown(item["answer"]))
        refs.extend(item.get("refs") or [])
    # 组合回答里含未完成的志愿追问时保留追问状态，下一轮“不知道”才能续上
    intent = (
        SLOT_INTENT
        if any(item.get("intent") == SLOT_INTENT for _, item in sections)
        else "composite"
    )
    return {
        "answer": "\n".join(lines),
        "refs": list(dict.fromkeys(refs)),
        "intent": intent,
    }


# 会话标题取首问摘要，先去掉寒暄前缀，避免标题变成“你好请问…”
_TITLE_PREFIXES = (
    "老师您好",
    "老师你好",
    "麻烦问一下",
    "我想问一下",
    "请问一下",
    "咨询一下",
    "想问一下",
    "你好",
    "您好",
    "老师好",
    "请问",
    "我想问",
)
_TITLE_TRIM = " \t\r\n，,。.！!？?；;：:、"


def _make_title(question: str) -> str:
    text = " ".join((question or "").split())
    changed = True
    while text and changed:
        changed = False
        text = text.lstrip(_TITLE_TRIM)
        for prefix in _TITLE_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):]
                changed = True
                break
    text = text.strip(_TITLE_TRIM)
    return (text or question or "新对话")[:30]


def _needs_school_link(answer: str, provider: str) -> bool:
    """provider 为 rule 表示直接给了“未检索到”兜底文案；模型回答则看措辞。"""
    if provider == "rule":
        return True
    return any(marker in answer for marker in INSUFFICIENT_MARKERS)


SYSTEM_PROMPT = (
    "你是东北师范大学招生办的AI助手，只依据【参考资料】回答考生和家长的问题。\n"
    "【回答规则】\n"
    "1. 事实只能来自【参考资料】：分数、位次、招生人数、学费、政策条款必须与资料一致，"
    "不得推测、换算或用常识补全。\n"
    "2. 给出结论时在句末标注资料编号（如[1]）；资料没覆盖的部分，直接写"
    "“资料中未查到这部分信息”，再建议查看学院官网或通过“向学校提问”咨询。\n"
    "3. 政策类问题（调剂、退档、转专业、奖助、推免、学费）按资料原文表述，"
    "并点明依据的文件名称，不得改写条款含义。\n"
    "4. 考生一次问了多个问题时，按提问顺序逐条回答，不得只答其中一个。\n"
    "5. 已提供【考生资料】时直接采用其中的省份、选科、分数、位次，不要再重复询问。\n"
    "6. 不得编造网址；需要看官网时只写学院或部门名称，链接由系统统一附加。\n"
    "7. 不承诺录取，不回应“内部指标”“花钱能上”等说法。\n"
    "【输出格式】简体中文；用“1. 2. 3.”分点；不超过 200 字；不使用 **、#、- 等 Markdown 符号。"
)

# 放在用户消息末尾，强化“按资料作答、标注编号”的要求（小模型对末位指令更敏感）
ANSWER_INSTRUCTION = "请严格依据【参考资料】作答，结论后标注资料编号；资料不足时说明未查到。"

# 顺序即优先级，先命中先生效
INTENT_RULES = [
    ("score", ["分数", "多少分", "位次", "排名", "录取线", "分数线", "能上", "能不能上", "最低分"]),
    ("major", ["专业", "学什么", "课程", "就业", "培养", "学院", "方向"]),
    ("policy", ["政策", "招生计划", "学费", "住宿", "报名", "批次", "调剂", "简章"]),
]


def detect_intent(text: str) -> str:
    for intent, keywords in INTENT_RULES:
        if any(keyword in text for keyword in keywords):
            return intent
    return "other"


def _ref_label(doc: Dict) -> str:
    return f"{doc.get('source_file') or doc.get('category')}#{doc.get('title')}"


def _history_messages(db: Session, conversation_id: int) -> List[Dict]:
    limit = settings.history_rounds * 2
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    return [{"role": row.role, "content": row.content} for row in reversed(rows)]


def _previous_user_question(db: Session, conversation_id: int) -> str:
    """取上一条用户提问，供“学费呢”这类省略主语的追问复用专业等实体。"""
    row = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.id.desc())
        .first()
    )
    return row.content if row else ""


def _profile_text(profile: Optional[StudentProfile]) -> str:
    """把考生资料整理成一行，供大模型直接采用，不再重复追问。"""
    if profile is None:
        return ""
    parts = [
        f"省份：{profile.province}" if profile.province else "",
        f"选科/科类：{profile.subject_combo}" if profile.subject_combo else "",
        f"高考分数：{profile.score}" if profile.score else "",
        f"位次：{profile.rank_no}" if profile.rank_no else "",
    ]
    return "；".join(part for part in parts if part)


def _build_messages(
    history: List[Dict],
    question: str,
    docs: List[Dict],
    profile: Optional[StudentProfile] = None,
) -> List[Dict]:
    context = "\n".join(
        f"[{index + 1}] {doc['title']}（来源：{doc.get('source_file') or doc['category']}）\n{doc['content']}"
        for index, doc in enumerate(docs)
    )
    profile_text = _profile_text(profile)
    blocks = [f"【参考资料】\n{context}"]
    if profile_text:
        blocks.insert(0, f"【考生资料】\n{profile_text}")
    blocks.append(f"【考生问题】\n{question}\n\n{ANSWER_INSTRUCTION}")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": "\n\n".join(blocks)})
    return messages


def _build_mock_answer(docs: List[Dict]) -> str:
    lines = ["根据学校公开资料："]
    for index, doc in enumerate(docs[:3], start=1):
        summary = doc["content"].replace("\n", " ").strip()
        if len(summary) > 90:
            summary = summary[:90] + "…"
        lines.append(f"{index}. {doc['title']}：{summary}")
    lines.append("（当前为离线兜底回答，配置模型后端后可获得生成式答复）")
    return "\n".join(lines)


def _get_or_create_conversation(
    db: Session, user: User, question: str, conversation_id: Optional[int]
) -> Conversation:
    if conversation_id is None:
        conversation = Conversation(
            user_id=user.id,
            title=_make_title(question),
            created_at=datetime.now(),
            last_active_at=datetime.now(),
        )
        db.add(conversation)
        db.flush()
        return conversation

    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise not_found("会话不存在")
    if conversation.user_id != user.id:
        raise forbidden("无权访问他人会话")
    return conversation


def handle_chat(
    db: Session, user: User, message: str, conversation_id: Optional[int] = None
) -> Dict:
    question = (message or "").strip()
    if not question:
        raise bad_request("问题不能为空")
    if len(question) > 500:
        raise bad_request("问题长度不能超过 500 字")

    conversation = _get_or_create_conversation(db, user, question, conversation_id)
    previous_question = _previous_user_question(db, conversation.id)
    # 每次对话都先读一次考生资料，供直答与生成兜底
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()

    # 问题里提到具体学院时，回答附上学院官网，资料不足也能让考生自行核对
    links = match_colleges(db, question)

    # 2. 敏感问题强匹配：命中即直出标准答案，跳过检索与生成
    sensitive_doc = match_sensitive(db, question)
    score_answer = None
    if sensitive_doc is None:
        # 一个问题里同时问了归属、分数、学费、计划时，逐项回答，不能只答其中一个
        sections: List[tuple] = []
        department_answer = build_department_answer(db, question, user)
        if department_answer is not None:
            sections.append(("学院归属", department_answer))
        score_answer = build_score_answer(db, user, question, conversation.id)
        if score_answer is not None:
            sections.append(("最低分与位次", score_answer))
        tuition_answer = build_tuition_answer(db, question, previous_question)
        if tuition_answer is not None:
            sections.append(("学费", tuition_answer))
        plan_answer = build_plan_change_answer(
            db, question, profile
        ) or build_plan_answer(db, question, previous_question, profile)
        if plan_answer is not None:
            sections.append(("招生计划", plan_answer))
        course_answer = build_course_answer(db, question)
        if course_answer is not None:
            sections.append(("课程", course_answer))
        catalog_answer = build_catalog_answer(db, question, user)
        if catalog_answer is not None:
            sections.append(("专业目录", catalog_answer))
        transfer_answer = build_transfer_answer(db, question, conversation.id, user)
        if transfer_answer is not None:
            sections.append(("转专业", transfer_answer))
        score_answer = _combine_answers(sections)
    if sensitive_doc is not None:
        answer = sensitive_doc.content
        refs = [f"{sensitive_doc.source_file or sensitive_doc.category}#{sensitive_doc.title}"]
        intent = "sensitive"
        provider = "rule"
    elif score_answer is not None:
        # 分数类问题直接查录取分数表，避免模型拿不到具体数值时泛泛而谈
        answer = score_answer["answer"]
        refs = score_answer["refs"]
        intent = score_answer.get("intent", "score")
        provider = "score"
    else:
        intent = detect_intent(question)
        docs = retrieve(db, question, top_k=settings.retrieval_top_k)
        if not docs:
            answer = NO_EVIDENCE_ANSWER
            if links:
                answer = (
                    "抱歉，招生资料库中暂未检索到与您问题直接相关的内容。"
                    f"可以先查看{links[0]['name']}官网了解详情，"
                    "或通过“向学校提问”提交问题，招生办会统一答复。"
                )
            refs = []
            provider = "rule"
        else:
            history = _history_messages(db, conversation.id)
            messages = _build_messages(history, question, docs, profile)
            llm_config = get_llm_config(db)
            client = LLMClient(
                provider=llm_config["provider"],
                model=llm_config["model"],
                provider_config=llm_config.get("custom_providers"),
            )
            try:
                answer = client.chat(messages, mock_answer=_build_mock_answer(docs))
                provider = client.last_provider or "unknown"
            except LLMError as exc:
                logger.error("生成失败，降级为固定答复: %s", exc)
                answer = NO_EVIDENCE_ANSWER
                provider = "error"
            refs = [_ref_label(doc) for doc in docs]

    # 回答里点到的学院也算，问题没提学院时也能直接点进官网
    answer_links = match_colleges(db, answer) if answer else []
    if not answer_links and answer and any(m in answer for m in SCHOOL_SITE_MARKERS):
        answer_links = [SCHOOL_WEBSITE]
    links = _merge_links(links, answer_links)

    # 回答没给出具体结论时，附招生网入口，让考生能自己点进去核对
    if not links and _needs_school_link(answer, provider):
        links = [SCHOOL_WEBSITE]

    # 7. 落库：用户消息 + 助手消息
    db.add(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=question,
            intent=intent,
            created_at=datetime.now(),
        )
    )
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            intent=intent,
            refs=",".join(refs)[:500] if refs else None,
            links=json.dumps(links, ensure_ascii=False)[:500] if links else None,
            created_at=datetime.now(),
        )
    )
    conversation.last_active_at = datetime.now()
    db.flush()

    return {
        "conversation_id": conversation.id,
        "answer": answer,
        "refs": refs,
        "links": links,
        "intent": intent,
        "provider": provider,
    }
