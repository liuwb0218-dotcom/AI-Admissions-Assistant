"""导入掌上高考导出的院校 JSON 到分数线表。

- 专业级记录（sp_name 非空）→ admission_scores
- 专业组投档线（sp_name 为空）→ school_admission_scores

示例：
    python -m collector.gaokao_json_importer --dry-run
    python -m collector.gaokao_json_importer --path "D:/.../00142_东北师范大学.json"
"""

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from database.connection import SessionLocal, init_db
from database.models import AdmissionScore, Major, SchoolAdmissionScore

logger = logging.getLogger(__name__)

DEFAULT_JSON_PATH = Path(
    r"D:\大学数据集\gaokao-admission-crawler-main\gaokao_data\00142_东北师范大学.json"
)

# 数据集科类 → 库内科类
TYPE_MAP = {
    "综合": "综合改革",
    "物理类": "物理类",
    "历史类": "历史类",
    "理科": "理工",
    "文科": "文史",
}

PLAN_NORMAL = "一般计划（普通类）"
PLAN_TUITION = "一般计划（公费师范）"
PLAN_YOUSHI = "优师专项计划"

# 数据集招生类型 → 库内计划类型
PLAN_MAP = {
    "普通类": PLAN_NORMAL,
    "中外合作": "中外合作办学",
    "中外合作办学": "中外合作办学",
    "国家公费师范生": PLAN_TUITION,
    "公费师范生": PLAN_TUITION,
    "国家免费师范生": PLAN_TUITION,
    "优师专项": PLAN_YOUSHI,
    "优师专项计划": PLAN_YOUSHI,
    "国家优师专项": PLAN_YOUSHI,
    "国家专项计划": "国家专项计划",
    "国家专项": "国家专项计划",
    "专项计划": "国家专项计划",
    "高校专项计划": "高校专项计划",
    "少数民族预科班": "少数民族预科班",
    "预科": "少数民族预科班",
    "本科预科": "少数民族预科班",
    "预科班": "少数民族预科班",
    "本科预科班": "少数民族预科班",
    "只招收少数民族考生": "少数民族预科班",
    "预科只招收少数民族考生": "少数民族预科班",
}


def normalize(text: str) -> str:
    return re.sub(r"[\s（）()·\-]", "", text or "")


def _plain_name(name: str) -> str:
    return re.split(r"[（(]", name)[0].strip()


class MajorResolver:
    """把 JSON 里的专业名 + 招生类型映射到库内专业，映射不唯一时宁可跳过。"""

    def __init__(self, majors: List[Major]):
        self.by_name: Dict[str, Major] = {}
        self.by_plain: Dict[str, List[Major]] = {}
        for major in majors:
            self.by_name[normalize(major.name)] = major
            self.by_plain.setdefault(normalize(_plain_name(major.name)), []).append(major)

    def resolve(self, sp_name: str, plan_type: str, remark: str) -> Optional[Major]:
        exact = self.by_name.get(normalize(sp_name))
        if exact is not None:
            return exact

        remark = remark or ""
        candidates: List[str] = []
        if "中外合作" in remark:
            candidates.append(f"{sp_name}(中外合作办学)")
        if plan_type == PLAN_TUITION:
            candidates.append(f"{sp_name}(公费师范)")
        if plan_type == PLAN_YOUSHI:
            candidates.append(f"{sp_name}(优师专项)")
        if "试验班" in remark:
            candidates.extend(
                major.name
                for major in self.by_plain.get(normalize(sp_name), [])
                if "试验班" in major.name
            )
        for name in candidates:
            major = self.by_name.get(normalize(name))
            if major is not None:
                return major
        return None


def plan_type_for(major: Major, mapped_plan: str) -> str:
    """专业名自带培养类型时，以专业名为准，避免"中外合作"专业挂普通类计划。"""
    if "中外合作办学" in major.name:
        return "中外合作办学"
    if "公费师范" in major.name:
        return PLAN_TUITION
    if "优师专项" in major.name:
        return PLAN_YOUSHI
    return mapped_plan


def build_records(data: Dict, resolver: MajorResolver) -> Dict[tuple, Dict]:
    records = data.get("records", [])
    province = {
        row["local_province_id"]: row["local_province_name"]
        for row in records
        if row.get("local_province_name")
    }
    type_name = {
        row["local_type_id"]: row["local_type_name"]
        for row in records
        if row.get("local_type_name")
    }

    skipped: Dict[str, int] = {}
    result: Dict[tuple, Dict] = {}
    for row in records:
        sp_name = (row.get("sp_name") or "").strip()
        if not sp_name:  # 专业组投档线，由 build_school_records 单独导入
            continue
        score = str(row.get("min", "")).strip()
        if not score.isdigit():
            continue
        province_name = province.get(row.get("local_province_id"))
        major_type = TYPE_MAP.get(type_name.get(row.get("local_type_id"), ""))
        if not province_name or not major_type:
            skipped[sp_name] = skipped.get(sp_name, 0) + 1
            continue

        mapped_plan = PLAN_MAP.get((row.get("zslx_name") or "").strip(), PLAN_NORMAL)
        major = resolver.resolve(sp_name, mapped_plan, row.get("remark", ""))
        if major is None:
            skipped[sp_name] = skipped.get(sp_name, 0) + 1
            continue

        rank = str(row.get("min_section", "")).strip()
        plan_type = plan_type_for(major, mapped_plan)
        key = (data.get("year", 2025), province_name, plan_type, major_type, major.id)
        candidate = {
            "year": key[0],
            "province": province_name,
            "plan_type": plan_type,
            "major_type": major_type,
            "major_id": major.id,
            "min_score": int(score),
            "min_rank": int(rank) if rank.isdigit() else None,
        }
        current = result.get(key)
        if current is None or candidate["min_score"] < current["min_score"]:
            result[key] = candidate

    return result, skipped


def build_school_records(data: Dict) -> Dict[tuple, Dict]:
    """抽取专业组投档线（sp_name 为空），按唯一键去重并保留最低分。"""
    records = data.get("records", [])
    province = {
        row["local_province_id"]: row["local_province_name"]
        for row in records
        if row.get("local_province_name")
    }
    type_name = {
        row["local_type_id"]: row["local_type_name"]
        for row in records
        if row.get("local_type_name")
    }

    result: Dict[tuple, Dict] = {}
    for row in records:
        if (row.get("sp_name") or "").strip():  # 有专业名的走专业级导入
            continue
        score = str(row.get("min", "")).strip()
        if not score.isdigit():
            continue
        province_name = (
            row.get("local_province_name")
            or province.get(row.get("local_province_id"))
            or ""
        ).strip()
        major_type = TYPE_MAP.get(
            (row.get("local_type_name") or type_name.get(row.get("local_type_id")) or "").strip()
        )
        if not province_name or not major_type:
            continue

        rank = str(row.get("min_section", "")).strip()
        key = (
            row.get("year") or data.get("year", 2025),
            province_name,
            major_type,
            (row.get("local_batch_name") or "").strip(),
            (row.get("sg_name") or "").strip(),
            (row.get("zslx_name") or "").strip() or "普通类",
        )
        candidate = {
            "year": key[0],
            "province": province_name,
            "major_type": major_type,
            "batch": key[3],
            "group_name": key[4],
            "plan_type": key[5],
            "subject_requirement": (row.get("sg_info") or "").strip() or None,
            "min_score": int(score),
            "min_rank": int(rank) if rank.isdigit() else None,
        }
        current = result.get(key)
        if current is None or candidate["min_score"] < current["min_score"]:
            result[key] = candidate

    return result


def import_file(db: Session, path: Path, dry_run: bool = False) -> Dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    resolver = MajorResolver(db.query(Major).all())
    records, skipped = build_records(data, resolver)

    existing = {
        (row.year, row.province, row.plan_type, row.major_type, row.major_id): row
        for row in db.query(AdmissionScore)
        .filter(AdmissionScore.year == data.get("year", 2025))
        .all()
    }

    inserted = updated = unchanged = 0
    for key, record in records.items():
        row = existing.get(key)
        if row is None:
            if not dry_run:
                db.add(AdmissionScore(**record))
            inserted += 1
        elif row.min_score is None or record["min_score"] < row.min_score:
            if not dry_run:
                row.min_score = record["min_score"]
                row.min_rank = record["min_rank"] or row.min_rank
            updated += 1
        else:
            unchanged += 1

    # 专业组投档线：独立表、独立统计
    school_records = build_school_records(data)
    school_years = {record["year"] for record in school_records.values()}
    school_existing: Dict[tuple, SchoolAdmissionScore] = {}
    if school_years:
        school_existing = {
            (
                row.year,
                row.province,
                row.major_type,
                row.batch,
                row.group_name,
                row.plan_type,
            ): row
            for row in db.query(SchoolAdmissionScore)
            .filter(SchoolAdmissionScore.year.in_(school_years))
            .all()
        }

    school_inserted = school_updated = school_unchanged = 0
    for key, record in school_records.items():
        row = school_existing.get(key)
        if row is None:
            if not dry_run:
                db.add(SchoolAdmissionScore(**record))
            school_inserted += 1
        elif row.min_score is None or record["min_score"] < row.min_score:
            if not dry_run:
                row.min_score = record["min_score"]
                row.min_rank = record["min_rank"] or row.min_rank
            school_updated += 1
        else:
            school_unchanged += 1

    if not dry_run:
        db.commit()
    return {
        "year": data.get("year"),
        "records": len(records),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "skipped_names": skipped,
        "dry_run": dry_run,
        "school": {
            "records": len(school_records),
            "inserted": school_inserted,
            "updated": school_updated,
            "unchanged": school_unchanged,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导入掌上高考院校 JSON 到分数线表")
    parser.add_argument("--path", type=Path, default=DEFAULT_JSON_PATH, help="JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.path.exists():
        raise FileNotFoundError(f"文件不存在: {args.path}")

    init_db()
    db = SessionLocal()
    try:
        result = import_file(db, args.path, dry_run=args.dry_run)
    finally:
        db.close()

    print(
        f"年份 {result['year']}: 可入库 {result['records']} 条，"
        f"新增 {result['inserted']}，更新 {result['updated']}，"
        f"已存在 {result['unchanged']}，dry_run={result['dry_run']}"
    )
    school = result["school"]
    print(
        f"专业组投档线: 可入库 {school['records']} 条，"
        f"新增 {school['inserted']}，更新 {school['updated']}，"
        f"已存在 {school['unchanged']}"
    )
    if result["skipped_names"]:
        print("未匹配专业（条数）:", result["skipped_names"])


if __name__ == "__main__":
    main()
