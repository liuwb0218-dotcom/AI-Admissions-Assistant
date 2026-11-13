"""学院官网链接：资料不足时给出对应学院官网，数据存 college_websites 表。"""

import re
from typing import Dict, List, Set

from sqlalchemy.orm import Session

from database.models import CollegeWebsite

# 没说学院、资料里又查不到时的兜底入口
SCHOOL_WEBSITE = {"name": "东北师范大学本科招生网", "url": "https://zsb.nenu.edu.cn/"}


def _squash(text: str) -> str:
    """去掉间隔号与空白，让“罗格斯大学•纽瓦克学院”也能被自然写法命中。"""
    return re.sub(r"[\s•·・]", "", text or "")


def split_aliases(name: str) -> List[str]:
    """「传媒科学学院(新闻学院)」→ ['传媒科学学院', '新闻学院']。"""
    aliases = []
    for left, right in (("(", ")"), ("（", "）")):
        if left in name:
            short = name.split(left)[0].strip()
            inner = name.split(left, 1)[1].rstrip(right).strip()
            if short:
                aliases.append(short)
            if inner:
                aliases.append(inner)
            break
    if name.startswith("东北师范大学"):
        aliases.append(name[len("东北师范大学") :])
    return aliases


def _name_aliases(name: str) -> Set[str]:
    return {name, *split_aliases(name)}


def match_colleges(db: Session, text: str) -> List[Dict[str, str]]:
    """返回问题里提到的学院及官网，最多 2 个。"""
    question = _squash(text)
    if not question:
        return []
    rows = (
        db.query(CollegeWebsite)
        .filter(CollegeWebsite.is_enabled == 1)
        .order_by(CollegeWebsite.id)
        .all()
    )
    hits = []
    for row in rows:
        aliases = _name_aliases(row.name)
        if row.aliases:
            aliases |= {item.strip() for item in row.aliases.split(",") if item.strip()}
        if any(alias and _squash(alias) in question for alias in aliases):
            hits.append({"name": row.name, "url": row.url})
    return hits[:2]
