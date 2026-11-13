"""学院官网链接入库：从 data/college_urls.json 幂等 upsert 到 college_websites。"""

import json
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy.orm import Session

from database.models import CollegeWebsite
from processor.college_links import split_aliases

URL_FILE = Path(__file__).resolve().parent.parent / "data" / "college_urls.json"


def import_college_websites(db: Session, path: Optional[Path] = None) -> Dict[str, int]:
    """按学院名幂等写入；已存在只更新 url，不改动后台设置的启用状态。"""
    file_path = Path(path) if path else URL_FILE
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    if not file_path.exists():
        return stats

    urls = json.loads(file_path.read_text(encoding="utf-8"))
    existing = {row.name: row for row in db.query(CollegeWebsite).all()}
    for name, url in urls.items():
        row = existing.get(name)
        if row is None:
            db.add(
                CollegeWebsite(
                    name=name, url=url, aliases=",".join(split_aliases(name)) or None
                )
            )
            stats["inserted"] += 1
        elif row.url != url:
            row.url = url
            stats["updated"] += 1
        else:
            stats["skipped"] += 1
    db.flush()
    return stats
