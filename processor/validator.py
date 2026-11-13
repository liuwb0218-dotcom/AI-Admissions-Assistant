"""数据校验：采集层输出的每一行都要过这里，不合法条目丢弃并计数。"""

from typing import Dict, List, Optional, Tuple

KNOWLEDGE_CATEGORIES = {"policy", "major", "faq", "sensitive"}
QUESTION_CATEGORIES = {"policy", "major", "score", "other"}


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int_or_none(value) -> Optional[int]:
    raw = _text(value)
    if not raw:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def validate_knowledge_row(row: Dict) -> Tuple[Optional[Dict], Optional[str]]:
    category = _text(row.get("category")).lower()
    title = _text(row.get("title"))
    content = _text(row.get("content"))
    if category not in KNOWLEDGE_CATEGORIES:
        return None, f"分类不在白名单内: {category or '空'}"
    if not title:
        return None, "标题缺失"
    if len(title) > 200:
        return None, "标题过长"
    if len(content) < 10:
        return None, "正文过短（需 ≥ 10 字）"
    sensitivity = _int_or_none(row.get("sensitivity_level")) or 0
    return (
        {
            "category": category,
            "title": title,
            "content": content,
            "source_file": _text(row.get("source_file")) or None,
            "source_type": "file",
            "sensitivity_level": 1 if sensitivity == 1 else 0,
            "keywords": _text(row.get("keywords")) or None,
        },
        None,
    )


def validate_many(validator, rows: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """返回 (合法行, 非法行[{row, reason}])。"""
    valid: List[Dict] = []
    invalid: List[Dict] = []
    for row in rows:
        item, reason = validator(row)
        if item is None:
            invalid.append({"row": row, "reason": reason})
        else:
            valid.append(item)
    return valid, invalid
