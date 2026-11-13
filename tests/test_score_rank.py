"""一分一段表解析与幂等导入测试。"""

import pandas as pd
import pytest

from collector.score_rank_importer import import_score_rank, rows_from_frame, validate_rows
from database.connection import SessionLocal
from database.models import ScoreRankSegment


def test_rows_from_frame_skips_header():
    frame = pd.DataFrame(
        [
            ["分数段", "全体", None],
            [697, 7, 56],
            [696, 14, 70],
        ]
    )
    assert rows_from_frame(frame) == [
        {"score": 697, "segment_count": 7, "cumulative_count": 56},
        {"score": 696, "segment_count": 14, "cumulative_count": 70},
    ]


def test_validate_rows_rejects_gap():
    rows = [
        {"score": 700, "segment_count": 10, "cumulative_count": 10},
        {"score": 698, "segment_count": 5, "cumulative_count": 15},
    ]
    with pytest.raises(ValueError):
        validate_rows(rows)


def test_validate_rows_rejects_wrong_total():
    rows = [
        {"score": 700, "segment_count": 10, "cumulative_count": 10},
        {"score": 699, "segment_count": 5, "cumulative_count": 99},
    ]
    with pytest.raises(ValueError):
        validate_rows(rows)


def test_import_score_rank_is_idempotent():
    db = SessionLocal()
    scope = {"province": "测试省", "year": 2026, "major_type": "综合改革"}
    rows = [
        {"score": 700, "segment_count": 10, "cumulative_count": 10},
        {"score": 699, "segment_count": 15, "cumulative_count": 25},
    ]
    try:
        first = import_score_rank(db, **scope, rows=rows, source_url="test")
        db.commit()
        second = import_score_rank(db, **scope, rows=rows, source_url="test")
        db.commit()

        assert first["inserted"] == 2
        assert second["skipped"] == 2
        assert db.query(ScoreRankSegment).filter_by(**scope).count() == 2

        rows[1]["cumulative_count"] = 30
        third = import_score_rank(db, **scope, rows=rows, source_url="test")
        db.commit()
        assert third["updated"] == 1
    finally:
        db.query(ScoreRankSegment).filter_by(**scope).delete()
        db.commit()
        db.close()
