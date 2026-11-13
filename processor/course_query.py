"""课程 / 培养方案直答：问「某专业或学院有哪些课程」时查知识库，查不到就明确说未收录。

专业目录直答只看「专业 + 有哪些」，很容易把「生命科学专业的课程有哪些」误判成
专业清单问题，这里用 COURSE_MARKERS 做前置识别，命中后由本模块接管。
"""

import re
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from database.models import CollegeWebsite, KnowledgeDoc
from processor.college_links import match_colleges, split_aliases
from processor.retriever import retrieve
from processor.score_query import match_majors

# 命中这些词说明问的是课程本身，专业目录直答必须让路
COURSE_MARKERS = (
    "课程", "学什么", "上什么课", "什么课", "培养方案", "主干课", "核心课", "课表",
)

_EXCERPT_LIMIT = 220


def is_course_question(question: str) -> bool:
    return bool(question) and any(marker in question for marker in COURSE_MARKERS)


def _squash(text: str) -> str:
    return re.sub(r"[\s•·・]", "", text or "")


def _stem(name: str) -> str:
    """「生命科学学院」→「生命科学」，用于识别省略“学院”的写法。"""
    for suffix in ("学院", "学部"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _college_hits(db: Session, question: str) -> List[Dict[str, str]]:
    """匹配学院；「生命科学专业」这类省略后缀的写法去掉“学院/学部”再匹配一次。"""
    hits = match_colleges(db, question)
    if hits:
        return hits
    text = _squash(question)
    if not text:
        return []
    result: List[Dict[str, str]] = []
    rows = (
        db.query(CollegeWebsite)
        .filter(CollegeWebsite.is_enabled == 1)
        .order_by(CollegeWebsite.id)
        .all()
    )
    for row in rows:
        for alias in {row.name, *split_aliases(row.name)}:
            stem = _stem(alias)
            # 单字简称（如「文学院」→「文」）误伤太大，只认两个字以上
            if len(stem) >= 2 and stem in text:
                result.append({"name": row.name, "url": row.url})
                break
    return result[:2]


def _targets(db: Session, question: str) -> tuple:
    colleges = _college_hits(db, question)
    majors = match_majors(db, question)
    names = [item["name"] for item in colleges]
    for major in majors:
        names.extend(
            value for value in (major.display_name, major.name, major.department) if value
        )
    return colleges, majors, list(dict.fromkeys(names))


def _related_docs(db: Session, question: str, names: List[str]) -> List[dict]:
    docs: List[dict] = []
    if names:
        for doc in db.query(KnowledgeDoc).order_by(KnowledgeDoc.id).all():
            label = f"{doc.source_file or ''} {doc.title or ''}"
            if not any(name in label for name in names):
                continue
            if not any(marker in (doc.content or "") for marker in COURSE_MARKERS):
                continue
            docs.append(
                {
                    "title": doc.title,
                    "content": doc.content,
                    "source_file": doc.source_file,
                    "category": doc.category,
                }
            )
            if len(docs) == 3:
                break
    if docs:
        return docs
    # 名称对不上时退回相关度检索，只保留真正提到课程的分块
    return [
        item
        for item in retrieve(db, question, top_k=5)
        if any(marker in item["content"] for marker in COURSE_MARKERS)
    ][:3]


def _excerpt(text: str) -> str:
    plain = " ".join((text or "").split())
    return plain if len(plain) <= _EXCERPT_LIMIT else plain[:_EXCERPT_LIMIT].rstrip() + "…"


def build_course_answer(db: Session, question: str) -> Optional[dict]:
    if not is_course_question(question):
        return None

    colleges, _, names = _targets(db, question)
    # 优先用问句里原样出现的名称，避免列出「计算机科学与技术（中外合作办学）」
    target = next((name for name in names if name in question), None)
    target = target or (names[0] if names else "该专业")
    docs = _related_docs(db, question, names)

    if docs:
        lines = [f"关于{target}的课程，资料库中的相关表述如下："]
        lines.extend(f"· {_excerpt(item['content'])}" for item in docs)
        lines.append("以上为公开资料中的概述，具体课程以学院官网公布的最新培养方案为准。")
        refs = [
            f"{item.get('source_file') or item.get('category')}#{item.get('title')}"
            for item in docs
        ]
    else:
        lines = [f"资料库暂未收录{target}的具体课程信息。"]
        if colleges:
            lines.append("可以先查看学院官网，或通过“向学校提问”让招生办补充说明。")
        else:
            lines.append("建议查阅东北师范大学本科招生网，或通过“向学校提问”提交问题。")
        refs = ["knowledge_docs#课程信息"]

    return {"answer": "\n".join(lines), "refs": refs, "intent": "course"}
