"""学院-系别-专业三级入库：把采集草稿写入 college_departments 并回填 majors.department_id。

默认读取 data/college_departments_draft.json，可重复执行（按学院 + 系名幂等）。

用法：
    python -m collector.college_department_importer
    python -m collector.college_department_importer --dry-run
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy.orm import Session

from database.connection import SessionLocal, init_db
from database.models import CollegeDepartment, CollegeWebsite, Major

logger = logging.getLogger(__name__)

DATA_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "college_departments_draft.json"
)

# 采集兜底时会混进外校名，按“办学类型词”过滤掉；学院自身名字里有的词不算噪声
INSTITUTION_WORDS = ("理工", "科技", "医科", "农业", "财经", "交通", "航空", "工业")


def _clean_name(name: str) -> str:
    """修掉“有中国史系”这类剥离不干净的系名。"""
    name = name.strip()
    if name.startswith("有") and name.endswith("系") and len(name) >= 4:
        return name[1:]
    return name


def _is_noise(name: str, college: str) -> bool:
    if not name or name == college:
        return True
    if name.endswith("系"):
        return False
    return any(word in name and word not in college for word in INSTITUTION_WORDS)


def _sync_departments(
    db: Session, site: CollegeWebsite, departments: list, stats: Dict[str, int]
) -> Dict[str, CollegeDepartment]:
    existing = {
        row.name: row
        for row in db.query(CollegeDepartment)
        .filter(CollegeDepartment.college_website_id == site.id)
        .all()
    }
    seen: Dict[str, CollegeDepartment] = {}
    for item in departments:
        name = _clean_name(item.get("name") or "")
        if _is_noise(name, site.name) or name in seen:
            stats["department_skipped"] += 1
            continue
        row = existing.get(name)
        if row is None:
            row = CollegeDepartment(
                college_website_id=site.id,
                college=site.name,
                name=name,
                evidence=item.get("evidence"),
                source_url=item.get("source_url"),
            )
            db.add(row)
            stats["department_inserted"] += 1
        elif row.evidence != item.get("evidence") or row.source_url != item.get(
            "source_url"
        ):
            row.evidence = item.get("evidence")
            row.source_url = item.get("source_url")
            stats["department_updated"] += 1
        else:
            stats["department_skipped"] += 1
        seen[name] = row
    db.flush()
    return seen


def _link_majors(
    db: Session,
    site: CollegeWebsite,
    majors: list,
    departments: Dict[str, CollegeDepartment],
    stats: Dict[str, int],
) -> None:
    for item in majors:
        dept_name = _clean_name(item.get("department") or "")
        if not dept_name:
            continue
        department = departments.get(dept_name)
        if department is None:
            stats["major_unmatched"] += 1
            continue
        rows = (
            db.query(Major)
            .filter(
                Major.display_name == item.get("name"),
                Major.department.contains(site.name),
            )
            .all()
        )
        if len(rows) != 1:
            stats["major_unmatched"] += 1
            continue
        major = rows[0]
        if major.department_id == department.id:
            stats["major_skipped"] += 1
            continue
        major.department_id = department.id
        stats["major_linked"] += 1


def import_college_departments(db: Session, path: Optional[Path] = None) -> Dict:
    """幂等写入系级数据并回填专业归属，返回统计。"""
    file_path = Path(path) if path else DATA_FILE
    stats = {
        "college_missing": 0,
        "department_inserted": 0,
        "department_updated": 0,
        "department_skipped": 0,
        "major_linked": 0,
        "major_skipped": 0,
        "major_unmatched": 0,
    }
    if not file_path.exists():
        raise FileNotFoundError(f"找不到采集草稿：{file_path}")

    payload = json.loads(file_path.read_text(encoding="utf-8"))
    sites = {row.name: row for row in db.query(CollegeWebsite).all()}
    for college in payload.get("colleges", []):
        site = sites.get(college.get("college"))
        if site is None:
            stats["college_missing"] += 1
            continue
        departments = _sync_departments(db, site, college.get("departments", []), stats)
        _link_majors(db, site, college.get("majors", []), departments, stats)
    db.flush()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="学院-系别-专业三级入库")
    parser.add_argument("--file", type=Path, default=DATA_FILE)
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不提交")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()
    db = SessionLocal()
    try:
        stats = import_college_departments(db, args.file)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
        logger.info(
            "系：新增 %s，更新 %s，跳过 %s；专业：关联 %s，已是最新 %s，未匹配 %s；缺学院 %s",
            stats["department_inserted"],
            stats["department_updated"],
            stats["department_skipped"],
            stats["major_linked"],
            stats["major_skipped"],
            stats["major_unmatched"],
            stats["college_missing"],
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
