"""关键词召回。

阶段 2 的实现：中文按单字 + 二元组切分，英文数字按词切分，再做字面重叠打分。
打分用 IDF 加权：通用词（专业、学校、的）权重低，低频词（调剂、退档）权重高，
避免"转专业"文档把"专业调剂"条款挤出结果。
阶段 4 换成 FAISS 时，只需保持 retrieve() / match_sensitive() 的签名不变。
"""

import math
import re
from collections import Counter
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from database.models import Announcement, KnowledgeDoc

_CJK = re.compile(r"[\u4e00-\u9fa5]")
_WORD = re.compile(r"[a-zA-Z0-9]+")


def _tokens(text: str) -> set:
    if not text:
        return set()
    lowered = text.lower()
    tokens = set(_WORD.findall(lowered))
    cjk = "".join(_CJK.findall(lowered))
    tokens.update(cjk)
    tokens.update(cjk[i : i + 2] for i in range(len(cjk) - 1))
    return tokens


def _doc_tokens(doc: KnowledgeDoc) -> set:
    return _tokens(f"{doc.title or ''} {doc.keywords or ''} {doc.content or ''}")


def _build_idf(documents: List[KnowledgeDoc]) -> Dict[str, float]:
    """文档频率的倒数取对数；没在语料出现过的词给最大权重。"""
    df = Counter()
    for doc in documents:
        df.update(_doc_tokens(doc))
    total = len(documents) or 1
    return {token: math.log((total + 1) / (count + 0.5)) for token, count in df.items()}


def _score(
    query_tokens: set,
    doc: KnowledgeDoc,
    idf: Dict[str, float],
    max_weight: float,
) -> float:
    def weighted(doc_token_set: set) -> float:
        return sum(idf.get(token, max_weight) for token in query_tokens & doc_token_set)

    title_hit = weighted(_tokens(doc.title or ""))
    keyword_hit = weighted(_tokens(doc.keywords or ""))
    content_hit = weighted(_tokens(doc.content or ""))
    return title_hit * 3 + keyword_hit * 4 + content_hit


def _published_map(db: Session) -> Dict[int, int]:
    rows = db.query(Announcement.id, Announcement.is_published).all()
    return {row[0]: row[1] for row in rows}


def retrieve(db: Session, query: str, top_k: int = 5) -> List[Dict]:
    """返回 [{id,title,content,category,source_file,score}]，按相关度倒序。"""
    query_tokens = _tokens(query)
    if not query_tokens:
        return []

    documents = db.query(KnowledgeDoc).all()
    idf = _build_idf(documents)
    max_weight = math.log((len(documents) + 1) / 0.5)
    published = _published_map(db)
    scored: List[Dict] = []
    for doc in documents:
        # 一致性兜底：公告回灌的条目，公告已下架/删除则不进检索结果（见需求 5.5.3）
        if doc.source_announcement_id is not None:
            if not published.get(doc.source_announcement_id):
                continue
        score = _score(query_tokens, doc, idf, max_weight)
        if score <= 0:
            continue
        scored.append(
            {
                "id": doc.id,
                "title": doc.title,
                "content": doc.content,
                "category": doc.category,
                "source_file": doc.source_file,
                "score": round(score, 3),
            }
        )

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]


def match_sensitive(db: Session, text: str) -> Optional[KnowledgeDoc]:
    """敏感问题强匹配：任意关键词出现在用户输入里即命中。"""
    if not text:
        return None
    for doc in (
        db.query(KnowledgeDoc).filter(KnowledgeDoc.sensitivity_level == 1).all()
    ):
        for keyword in (doc.keywords or "").split(","):
            keyword = keyword.strip()
            if keyword and keyword in text:
                return doc
    return None
