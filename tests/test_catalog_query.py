"""专业 / 学院清单直答。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import CollegeWebsite, EnrollmentPlan, Major, StudentProfile, User
from processor.catalog_query import build_catalog_answer


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    physics = Major(name="物理学", display_name="物理学（师范）")
    history = Major(name="历史学", display_name="历史学")
    cs = Major(name="计算机科学与技术", display_name="计算机科学与技术")
    coop = Major(name="会计学（中外合作办学）", display_name="会计学（中外合作办学）")
    db.add_all([physics, history, cs, coop])
    db.add_all(
        [
            CollegeWebsite(name="教育学部", aliases="", url="http://edu.nenu.edu.cn/", is_enabled=1),
            CollegeWebsite(name="心理学院", aliases="", url="http://psy.nenu.edu.cn/", is_enabled=1),
            CollegeWebsite(name="停用学院", aliases="", url="http://x.nenu.edu.cn/", is_enabled=0),
        ]
    )
    db.flush()
    db.add_all(
        [
            EnrollmentPlan(year=2026, province="吉林", plan_type="一般计划（普通类）",
                           major_type="物理类", major_id=physics.id, plan_count=10),
            EnrollmentPlan(year=2026, province="吉林", plan_type="一般计划（普通类）",
                           major_type="历史类", major_id=history.id, plan_count=8),
            EnrollmentPlan(year=2026, province="吉林", plan_type="一般计划（普通类）",
                           major_type="物理类", major_id=cs.id, plan_count=20),
            EnrollmentPlan(year=2026, province="吉林", plan_type="中外合作办学",
                           major_type="物理类", major_id=coop.id, plan_count=15),
        ]
    )
    db.commit()


def test_college_list():
    db = _session()
    _seed(db)

    result = build_catalog_answer(db, "东北师范大学有几个学院")

    assert result["intent"] == "major"
    assert "2 个学院" in result["answer"]
    assert "教育学部" in result["answer"]
    assert "停用学院" not in result["answer"]


def test_major_list():
    db = _session()
    _seed(db)

    answer = build_catalog_answer(db, "东北师大有哪些专业")["answer"]

    assert "共 4 个" in answer
    assert "物理学（师范）" in answer


def test_cooperation_major_list():
    db = _session()
    _seed(db)

    answer = build_catalog_answer(db, "中外合作办学有哪些专业")["answer"]

    assert "共 1 个" in answer
    assert "会计学（中外合作办学）" in answer


def test_major_type_list_with_old_wording():
    db = _session()
    _seed(db)

    answer = build_catalog_answer(db, "哪些专业招收文科生")["answer"]

    assert "历史类" in answer
    assert "历史学" in answer
    assert "计算机科学与技术" not in answer


def test_specific_major_question_is_not_hijacked():
    db = _session()
    _seed(db)

    assert build_catalog_answer(db, "计算机科学与技术有哪些课程") is None
    assert build_catalog_answer(db, "宿舍条件怎么样") is None


def test_major_list_uses_profile_scope():
    db = _session()
    _seed(db)
    user = User(username="stu", password_hash="x")
    db.add(user)
    db.flush()
    db.add(StudentProfile(user_id=user.id, province="吉林", subject_combo="物化生"))
    db.commit()

    answer = build_catalog_answer(db, "2026年有哪些招生专业", user)["answer"]

    assert "按你的资料（吉林 · 物理类）" in answer
    assert "计算机科学与技术" in answer
    assert "历史学" not in answer


def test_major_list_without_profile_keeps_full_catalog():
    db = _session()
    _seed(db)

    answer = build_catalog_answer(db, "东北师大有哪些专业")["answer"]

    assert "共 4 个" in answer
    assert "你的资料" not in answer
