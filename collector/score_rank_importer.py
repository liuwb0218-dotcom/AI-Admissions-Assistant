"""一分一段表入库：目前支持山东省 2026 年官方 XLS。

用法：
    python -m collector.score_rank_importer
    python -m collector.score_rank_importer --file data/score_rank/2026/shandong.xls
"""

import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy.orm import Session

from database.connection import SessionLocal, init_db
from database.models import ScoreRankSegment

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_FILE = DATA_DIR / "score_rank" / "2026" / "shandong.xls"
DEFAULT_URL = "https://www.sdzk.cn/Floadup/file/20260625/6391799529165570082629463.xls"

# 山东 2026 官方校验点：一段线 442，最低分 150 的累计人数
CHECK_SCORES = (442, 150)


def rows_from_frame(frame: pd.DataFrame) -> List[Dict[str, int]]:
    """从“分数段 / 本段人数 / 累计人数”三列中提取有效行，跳过标题和表头。"""
    rows: List[Dict[str, int]] = []
    for _, raw in frame.iterrows():
        score = pd.to_numeric(raw.iloc[0], errors="coerce")
        segment = pd.to_numeric(raw.iloc[1], errors="coerce")
        cumulative = pd.to_numeric(raw.iloc[2], errors="coerce")
        if pd.isna(score) or pd.isna(segment) or pd.isna(cumulative):
            continue
        score_value = int(score)
        if not 0 <= score_value <= 750:
            continue
        rows.append(
            {
                "score": score_value,
                "segment_count": int(segment),
                "cumulative_count": int(cumulative),
            }
        )
    return rows


def validate_rows(rows: List[Dict[str, int]]) -> None:
    """分数连续递减、累计人数单调不减，且各段人数能与累计人数对上。

    官方表通常从高分段开始列，最高分之上还有少量考生未单独成行，因此
    “本段人数之和”允许比末行累计人数少，差额应恰好等于首行之前的未列出人数
    （首行累计人数 - 首行本段人数）。
    """
    if not rows:
        raise ValueError("一分一段表为空")
    scores = [row["score"] for row in rows]
    for previous, current in zip(scores, scores[1:]):
        if previous - current != 1:
            raise ValueError(f"分数不连续：{previous} → {current}")

    previous_cumulative = -1
    for row in rows:
        cumulative = row["cumulative_count"]
        if cumulative < previous_cumulative:
            raise ValueError(f"累计人数回退：{row['score']} 分 {cumulative}")
        if cumulative < 0 or row["segment_count"] < 0:
            raise ValueError(f"人数为负：{row['score']} 分")
        previous_cumulative = cumulative

    omitted = rows[0]["cumulative_count"] - rows[0]["segment_count"]
    if omitted < 0:
        raise ValueError(f"首行累计人数小于本段人数：{rows[0]['score']} 分")
    if sum(row["segment_count"] for row in rows) + omitted != rows[-1]["cumulative_count"]:
        raise ValueError("本段人数之和与末行累计人数不一致")


def parse_shandong_xls(path: Optional[Path] = None) -> List[Dict[str, int]]:
    """读取山东官方 XLS 的第一个工作表，前三列即全体考生的分数段数据。"""
    file_path = Path(path) if path else DEFAULT_FILE
    if not file_path.exists():
        raise FileNotFoundError(f"找不到文件：{file_path}")
    frame = pd.read_excel(file_path, sheet_name=0, header=None)
    rows = rows_from_frame(frame)
    validate_rows(rows)
    return rows


def import_score_rank(
    db: Session,
    province: str,
    year: int,
    major_type: str,
    rows: List[Dict[str, int]],
    source_url: Optional[str] = None,
    prune: bool = False,
) -> Dict[str, int]:
    """按省份+年份+科类+分数幂等写入；prune=True 时删除本次未出现的旧分数行。"""
    existing = {
        row.score: row
        for row in db.query(ScoreRankSegment)
        .filter(
            ScoreRankSegment.province == province,
            ScoreRankSegment.year == year,
            ScoreRankSegment.major_type == major_type,
        )
        .all()
    }
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "deleted": 0}
    seen = set()

    for item in rows:
        score = item["score"]
        seen.add(score)
        row = existing.get(score)
        if row is None:
            db.add(
                ScoreRankSegment(
                    province=province,
                    year=year,
                    major_type=major_type,
                    score=score,
                    segment_count=item["segment_count"],
                    cumulative_count=item["cumulative_count"],
                    source_url=source_url,
                )
            )
            stats["inserted"] += 1
            continue
        if (
            row.segment_count != item["segment_count"]
            or row.cumulative_count != item["cumulative_count"]
            or (source_url and row.source_url != source_url)
        ):
            row.segment_count = item["segment_count"]
            row.cumulative_count = item["cumulative_count"]
            if source_url:
                row.source_url = source_url
            stats["updated"] += 1
        else:
            stats["skipped"] += 1

    if prune:
        for score, row in existing.items():
            if score not in seen:
                db.delete(row)
                stats["deleted"] += 1

    db.flush()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="一分一段表入库")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="官方 XLS 路径")
    parser.add_argument("--province", default="山东")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--major-type", default="综合改革")
    parser.add_argument("--source-url", default=DEFAULT_URL)
    parser.add_argument("--prune", action="store_true", help="删除本次文件中不存在的旧分数行")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = parse_shandong_xls(args.file)
    logger.info("解析成功：%s 行，分数 %s → %s", len(rows), rows[0]["score"], rows[-1]["score"])

    init_db()
    db = SessionLocal()
    try:
        stats = import_score_rank(
            db,
            province=args.province,
            year=args.year,
            major_type=args.major_type,
            rows=rows,
            source_url=args.source_url,
            prune=args.prune,
        )
        db.commit()
    finally:
        db.close()

    logger.info(
        "入库完成：新增 %s，更新 %s，跳过 %s，删除 %s",
        stats["inserted"],
        stats["updated"],
        stats["skipped"],
        stats["deleted"],
    )
    by_score = {row["score"]: row["cumulative_count"] for row in rows}
    for score in CHECK_SCORES:
        if score in by_score:
            logger.info("校验：%s 分累计人数 %s", score, by_score[score])


if __name__ == "__main__":
    main()
