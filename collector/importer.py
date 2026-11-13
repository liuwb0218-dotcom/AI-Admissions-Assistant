"""采集层：CSV / JSON 读取与知识库导入；真实招生数据见 nenu_importer。"""

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from collector.college_website_importer import import_college_websites
from collector.nenu_importer import ImportStats, import_neu_data
from config.settings import DATA_DIR
from database.models import KnowledgeDoc
from processor.validator import validate_knowledge_row, validate_many


def load_csv(path: Path) -> List[Dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get("items", [])
    return data


def _result_from_invalid(invalid: List[Dict]) -> ImportStats:
    return ImportStats(
        invalid=len(invalid),
        errors=[f"{item['reason']}" for item in invalid],
    )


def import_knowledge(db: Session, rows: List[Dict]) -> ImportStats:
    valid, invalid = validate_many(validate_knowledge_row, rows)
    result = _result_from_invalid(invalid)

    existing = {
        (category, title)
        for category, title in db.query(KnowledgeDoc.category, KnowledgeDoc.title).all()
    }
    for item in valid:
        key = (item["category"], item["title"])
        if key in existing:
            result.skipped += 1
            continue
        db.add(KnowledgeDoc(**item))
        existing.add(key)
        result.inserted += 1
    db.flush()
    return result


def import_all(
    db: Session, data_dir: Optional[Path] = None, include_neu: bool = True
) -> Dict[str, Dict]:
    """导入顺序：专业 → 分数线/招生计划（真实 CSV）→ 学院官网 → 知识库 faq.json。

    `include_neu=False` 供测试使用：只导入工作区内的 FAQ，不依赖桌面真实文件。
    """
    base = Path(data_dir) if data_dir else DATA_DIR
    summary: Dict[str, Dict] = {}

    if include_neu:
        summary.update(import_neu_data(db))

    summary["college_websites"] = import_college_websites(db)

    faq_path = base / "faq.json"
    if faq_path.exists():
        summary["knowledge_docs"] = import_knowledge(db, load_json(faq_path)).as_dict()
    else:
        summary["knowledge_docs"] = ImportStats(
            errors=[f"文件不存在: {faq_path}"]
        ).as_dict()
    return summary
