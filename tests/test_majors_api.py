"""专业接口：最低分 / 位次 / 参考回退 / 近三年历史。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.majors import get_major, list_majors
from database.connection import Base
from database.models import AdmissionScore, EnrollmentPlan, Major, StudentProfile, User


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    user = User(username="major-user", password_hash="x", role="user")
    db.add(user)
    db.flush()
    db.add(StudentProfile(user_id=user.id, province="河南", subject_combo="物理类"))

    physics = Major(name="物理学", display_name="物理学（师范）", major_attr="师范类")
    history = Major(name="历史学", display_name="历史学")
    special = Major(name="生态学", display_name="生态学")
    empty = Major(name="数据科学", display_name="数据科学")
    db.add_all([physics, history, special, empty])
    db.flush()
    db.add_all(
        [
            AdmissionScore(
                year=2026,
                province="河南",
                plan_type="一般计划（普通类）",
                major_type="物理类",
                major_id=physics.id,
                min_score=600,
                min_rank=7000,
            ),
            AdmissionScore(
                year=2026,
                province="吉林",
                plan_type="一般计划（普通类）",
                major_type="物理类",
                major_id=physics.id,
                min_score=580,
                min_rank=8000,
            ),
            AdmissionScore(
                year=2025,
                province="吉林",
                plan_type="一般计划（普通类）",
                major_type="物理类",
                major_id=physics.id,
                min_score=575,
                min_rank=9000,
            ),
            AdmissionScore(
                year=2026,
                province="吉林",
                plan_type="一般计划（普通类）",
                major_type="物理类",
                major_id=history.id,
                min_score=560,
                min_rank=10000,
            ),
            EnrollmentPlan(
                year=2026,
                province="河南",
                plan_type="高校专项计划",
                major_type="物理类",
                major_id=special.id,
                plan_count=2,
            ),
        ]
    )
    db.commit()
    return user


def test_list_uses_profile_scope_and_fills_every_major():
    db = _session()
    user = _seed(db)
    try:
        rows = list_majors(
            keyword=None, year=2026, province=None, major_type=None, db=db, user=user
        )["data"]
    finally:
        db.close()

    by_name = {row["display_name"]: row for row in rows}
    assert len(by_name) == 4

    physics = by_name["物理学（师范）"]
    assert physics["score"]["province"] == "河南"
    assert physics["score"]["min_score"] == 600
    assert physics["score"]["is_reference"] is False

    # 河南没有历史学数据，回退到吉林同年并标注参考
    history = by_name["历史学"]
    assert history["score"]["province"] == "吉林"
    assert history["score"]["is_reference"] is True
    assert "参考" in history["score"]["note"]

    # 只有不公布分数的专项计划
    special = by_name["生态学"]
    assert special["score"] is None
    assert special["score_status"] == "unpublished_special"

    # 完全没有招生计划
    empty = by_name["数据科学"]
    assert empty["score"] is None
    assert empty["score_status"] == "not_planned"


def test_score_history_is_returned_for_detail():
    db = _session()
    user = _seed(db)
    try:
        physics_id = db.query(Major).filter(Major.name == "物理学").one().id
        data = get_major(
            physics_id,
            year=2026,
            province="吉林",
            major_type="物理类",
            db=db,
            user=user,
        )["data"]
    finally:
        db.close()

    assert data["score"]["min_score"] == 580
    assert [item["year"] for item in data["score_history"]] == [2026, 2025]
