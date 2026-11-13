"""东北师大真实招生数据导入：两份 CSV → 专业维表 + 分数线/招生计划事实表。

清洗规则（依据数据体检结论）：
1. `utf-8-sig` 读入，去掉 BOM；
2. 专业名全角括号/逗号转半角并合并空白，使分数线表与计划表命中同一个专业；
3. 按业务键去重（源文件约 91% 为完全重复行）；
4. 从两表联合收集 (专业名, 专业属性) 建专业维表；
5. 学费「免费」解析为 0，原文保留在 `tuition_raw`；
6. upsert 为「查已有 → 逐字段比对 → 有变化才更新」，重复执行不产生脏数据。
"""

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from config.settings import settings
from database.models import AdmissionScore, EnrollmentPlan, Major, Recommendation

_FLUSH_EVERY = 500
_TRANSLATE = str.maketrans({"（": "(", "）": ")", "，": ",", "　": " "})

# 源数据偶尔把同一专业同时标成“普通类”和“师范类”，统一归到师范类身份，避免一个专业裂成两条
_ATTR_CANONICAL = {
    "心理学(公费师范)": "师范类",
    "教育学类": "师范类",
    "美术学(师范)": "师范类",
}


def canonical_major_attr(name: str, attr: str) -> str:
    """专业属性归一：已知重复身份强制使用唯一属性。"""
    return _ATTR_CANONICAL.get(name, attr)

# 目标字段 -> 分数线 CSV 列名
SCORE_NUMBER_FIELDS = {
    "min_score": "min_admission_score",
    "min_rank": "min_rank",
    "baseline": "baseline",
    "min_culture_score": "min_culture_score",
    "min_major_score": "min_major_score",
    "max_culture_score": "max_culture_score",
    "max_major_score": "max_major_score",
    "max_score": "max_admission_score",
    "max_rank": "max_rank",
    "avg_culture_score": "avg_culture_score",
    "avg_major_score": "avg_major_score",
    "avg_score": "avg_admission_score",
}

PLAN_NUMBER_FIELDS = {"plan_count": "number"}


@dataclass
class ImportStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    invalid: int = 0
    duplicate: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "invalid": self.invalid,
            "duplicate": self.duplicate,
            "errors": self.errors[:5],
        }


def normalize_major(name) -> str:
    """全角括号/逗号转半角并合并空白，作为专业维表身份。"""
    text = str(name or "").translate(_TRANSLATE)
    return " ".join(text.split())


def read_rows(path) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _text(value) -> str:
    return str(value or "").strip()


def _int(value) -> Optional[int]:
    raw = _text(value)
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _base_key(raw: Dict) -> Optional[Dict]:
    """抽取两表共有的业务维度；缺任一维度即视为非法行。"""
    name = normalize_major(raw.get("major"))
    province = _text(raw.get("province"))
    plan_type = _text(raw.get("type"))
    major_type = _text(raw.get("major_type"))
    year = _int(raw.get("year"))
    if not (name and province and plan_type and major_type) or year is None:
        return None
    major_attr = canonical_major_attr(name, _text(raw.get("major_attr")) or "普通类")
    return {
        "major": name,
        "display_name": _text(raw.get("major")),
        "major_attr": major_attr,
        "year": year,
        "province": province,
        "plan_type": plan_type,
        "major_type": major_type,
    }


def _business_key(row: Dict) -> Tuple:
    return (
        row["year"],
        row["province"],
        row["plan_type"],
        row["major_type"],
        row["major"],
        row["major_attr"],
    )


def dedupe_rows(rows: List[Dict]) -> List[Dict]:
    seen, unique = set(), []
    for row in rows:
        key = _business_key(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def clean_score_rows(raw_rows: List[Dict]) -> Tuple[List[Dict], int]:
    rows, invalid = [], 0
    for raw in raw_rows:
        base = _base_key(raw)
        if base is None:
            invalid += 1
            continue
        values = {target: _int(raw.get(col)) for target, col in SCORE_NUMBER_FIELDS.items()}
        # 源接口用 0 表示“位次未公布”，不能当作真实位次入库
        if values["min_rank"] is not None and values["min_rank"] <= 0:
            values["min_rank"] = None
        rows.append({**base, **values})
    return rows, invalid


def _tuition(raw) -> Tuple[Optional[int], Optional[str]]:
    text = _text(raw)
    if not text:
        return None, None
    if text == "免费":
        return 0, text
    return _int(text), text


def clean_plan_rows(raw_rows: List[Dict]) -> Tuple[List[Dict], int]:
    rows, invalid = [], 0
    for raw in raw_rows:
        base = _base_key(raw)
        if base is None:
            invalid += 1
            continue
        tuition, tuition_raw = _tuition(raw.get("edu_cost"))
        rows.append(
            {
                **base,
                **{target: _int(raw.get(col)) for target, col in PLAN_NUMBER_FIELDS.items()},
                "edu_len": _text(raw.get("edu_len")) or None,
                "tuition": tuition,
                "tuition_raw": tuition_raw,
            }
        )
    return rows, invalid


def _register_majors(
    db: Session, score_rows: List[Dict], plan_rows: List[Dict]
) -> Tuple[Dict[Tuple[str, str], int], int]:
    """补齐专业维表，返回 (专业名, 专业属性) -> major_id。"""
    wanted: Dict[Tuple[str, str], str] = {}
    for row in score_rows + plan_rows:
        wanted.setdefault((row["major"], row["major_attr"]), row["display_name"])

    existing = {(m.name, m.major_attr): m.id for m in db.query(Major).all()}
    created = 0
    for (name, attr), display_name in wanted.items():
        if (name, attr) in existing:
            continue
        major = Major(name=name, display_name=display_name or name, major_attr=attr)
        db.add(major)
        db.flush()
        existing[(name, attr)] = major.id
        created += 1
    return existing, created


def merge_duplicate_majors(db: Session) -> Dict[str, int]:
    """把历史脏数据里“普通类”壳专业合并回同名“师范类”专业。

    同一年份/省份/计划类型/科类已存在目标记录时，只补空字段，不覆盖已有值。
    """
    stats = {"merged": 0, "scores_moved": 0, "scores_dropped": 0, "plans_moved": 0, "plans_dropped": 0}
    for name, attr in _ATTR_CANONICAL.items():
        target = (
            db.query(Major).filter(Major.name == name, Major.major_attr == attr).first()
        )
        if target is None:
            continue
        sources = (
            db.query(Major)
            .filter(Major.name == name, Major.major_attr != attr)
            .all()
        )
        for source in sources:
            target_scores = {
                (row.year, row.province, row.plan_type, row.major_type): row
                for row in db.query(AdmissionScore).filter(
                    AdmissionScore.major_id == target.id
                )
            }
            for row in db.query(AdmissionScore).filter(
                AdmissionScore.major_id == source.id
            ).all():
                key = (row.year, row.province, row.plan_type, row.major_type)
                existing = target_scores.get(key)
                if existing is None:
                    row.major_id = target.id
                    target_scores[key] = row
                    stats["scores_moved"] += 1
                    continue
                for field in SCORE_NUMBER_FIELDS:
                    if getattr(existing, field) is None and getattr(row, field) is not None:
                        setattr(existing, field, getattr(row, field))
                db.delete(row)
                stats["scores_dropped"] += 1

            target_plans = {
                (row.year, row.province, row.plan_type, row.major_type): row
                for row in db.query(EnrollmentPlan).filter(
                    EnrollmentPlan.major_id == target.id
                )
            }
            for row in db.query(EnrollmentPlan).filter(
                EnrollmentPlan.major_id == source.id
            ).all():
                key = (row.year, row.province, row.plan_type, row.major_type)
                existing = target_plans.get(key)
                if existing is None:
                    row.major_id = target.id
                    target_plans[key] = row
                    stats["plans_moved"] += 1
                    continue
                for field in ("plan_count", "edu_len", "tuition", "tuition_raw"):
                    if getattr(existing, field) is None and getattr(row, field) is not None:
                        setattr(existing, field, getattr(row, field))
                db.delete(row)
                stats["plans_dropped"] += 1

            db.query(Recommendation).filter(Recommendation.major_id == source.id).update(
                {Recommendation.major_id: target.id}
            )
            db.delete(source)
            stats["merged"] += 1
            db.flush()
    return stats


def _upsert_scores(
    db: Session, rows: List[Dict], major_ids: Dict[Tuple[str, str], int]
) -> ImportStats:
    stats = ImportStats()
    existing = {
        (s.year, s.province, s.plan_type, s.major_type, s.major_id): s
        for s in db.query(AdmissionScore).all()
    }
    for index, row in enumerate(rows, 1):
        major_id = major_ids[(row["major"], row["major_attr"])]
        key = (row["year"], row["province"], row["plan_type"], row["major_type"], major_id)
        values = {name: row[name] for name in SCORE_NUMBER_FIELDS}
        obj = existing.get(key)
        if obj is None:
            obj = AdmissionScore(
                year=row["year"],
                province=row["province"],
                plan_type=row["plan_type"],
                major_type=row["major_type"],
                major_id=major_id,
                **values,
            )
            db.add(obj)
            existing[key] = obj
            stats.inserted += 1
        else:
            changed = {name: value for name, value in values.items() if getattr(obj, name) != value}
            if changed:
                for name, value in changed.items():
                    setattr(obj, name, value)
                stats.updated += 1
            else:
                stats.skipped += 1
        if index % _FLUSH_EVERY == 0:
            db.flush()
    db.flush()
    return stats


def _upsert_plans(
    db: Session, rows: List[Dict], major_ids: Dict[Tuple[str, str], int]
) -> ImportStats:
    stats = ImportStats()
    existing = {
        (p.year, p.province, p.plan_type, p.major_type, p.major_id): p
        for p in db.query(EnrollmentPlan).all()
    }
    for index, row in enumerate(rows, 1):
        major_id = major_ids[(row["major"], row["major_attr"])]
        key = (row["year"], row["province"], row["plan_type"], row["major_type"], major_id)
        values = {
            "plan_count": row["plan_count"],
            "edu_len": row["edu_len"],
            "tuition": row["tuition"],
            "tuition_raw": row["tuition_raw"],
        }
        obj = existing.get(key)
        if obj is None:
            obj = EnrollmentPlan(
                year=row["year"],
                province=row["province"],
                plan_type=row["plan_type"],
                major_type=row["major_type"],
                major_id=major_id,
                **values,
            )
            db.add(obj)
            existing[key] = obj
            stats.inserted += 1
        else:
            changed = {name: value for name, value in values.items() if getattr(obj, name) != value}
            if changed:
                for name, value in changed.items():
                    setattr(obj, name, value)
                stats.updated += 1
            else:
                stats.skipped += 1
        if index % _FLUSH_EVERY == 0:
            db.flush()
    db.flush()
    return stats


def import_neu_data(
    db: Session, score_csv=None, plan_csv=None
) -> Dict[str, Dict]:
    """导入两份真实 CSV，返回各表统计。任一文件缺失只记错误、不中断另一份。"""
    score_path = Path(score_csv or settings.neu_score_csv)
    plan_path = Path(plan_csv or settings.neu_plan_csv)

    score_rows: List[Dict] = []
    plan_rows: List[Dict] = []
    score_file_stats = ImportStats()
    plan_file_stats = ImportStats()

    if score_path.exists():
        score_rows, score_file_stats.invalid = clean_score_rows(read_rows(score_path))
        before = len(score_rows)
        score_rows = dedupe_rows(score_rows)
        score_file_stats.duplicate = before - len(score_rows)
    else:
        score_file_stats.errors.append(f"文件不存在: {score_path}")

    if plan_path.exists():
        plan_rows, plan_file_stats.invalid = clean_plan_rows(read_rows(plan_path))
        before = len(plan_rows)
        plan_rows = dedupe_rows(plan_rows)
        plan_file_stats.duplicate = before - len(plan_rows)
    else:
        plan_file_stats.errors.append(f"文件不存在: {plan_path}")

    merge_duplicate_majors(db)
    major_ids, majors_created = _register_majors(db, score_rows, plan_rows)

    score_stats = _upsert_scores(db, score_rows, major_ids)
    score_stats.invalid = score_file_stats.invalid
    score_stats.duplicate = score_file_stats.duplicate
    score_stats.errors = score_file_stats.errors

    plan_stats = _upsert_plans(db, plan_rows, major_ids)
    plan_stats.invalid = plan_file_stats.invalid
    plan_stats.duplicate = plan_file_stats.duplicate
    plan_stats.errors = plan_file_stats.errors

    return {
        "majors": ImportStats(inserted=majors_created).as_dict(),
        "admission_scores": score_stats.as_dict(),
        "enrollment_plans": plan_stats.as_dict(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="东北师大分数/计划数据导入与专业身份修复")
    parser.add_argument("--merge-majors", action="store_true", help="只合并重复专业身份")
    parser.add_argument("--score-csv", help="分数线 CSV 路径")
    parser.add_argument("--plan-csv", help="招生计划 CSV 路径")
    args = parser.parse_args(argv)

    from database.connection import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        if args.merge_majors:
            print(f"[db] merge_majors: {merge_duplicate_majors(db)}")
        else:
            stats = import_neu_data(db, args.score_csv, args.plan_csv)
            for table, detail in stats.items():
                print(f"[db] {table}: {detail}")
        db.commit()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
