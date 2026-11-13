"""招生计划同比直答。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import EnrollmentPlan, Major
from processor.plan_query import build_plan_change_answer


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    physics = Major(name="物理学", display_name="物理学（师范）")
    computer = Major(name="计算机科学与技术", display_name="计算机科学与技术")
    history = Major(name="历史学", display_name="历史学")
    db.add_all([physics, computer, history])
    db.flush()
    db.add_all(
        [
            EnrollmentPlan(year=2025, province="河南", plan_type="一般计划（普通类）",
                           major_type="物理类", major_id=physics.id, plan_count=10),
            EnrollmentPlan(year=2025, province="河南", plan_type="一般计划（普通类）",
                           major_type="物理类", major_id=computer.id, plan_count=20),
            EnrollmentPlan(year=2026, province="河南", plan_type="一般计划（普通类）",
                           major_type="物理类", major_id=physics.id, plan_count=15),
            EnrollmentPlan(year=2026, province="河南", plan_type="一般计划（普通类）",
                           major_type="历史类", major_id=history.id, plan_count=5),
        ]
    )
    db.commit()


def test_province_plan_change_summary():
    db = _session()
    _seed(db)

    result = build_plan_change_answer(db, "河南2026年招生计划比去年有什么变化")

    assert result["intent"] == "plan"
    answer = result["answer"]
    assert "30 人 → 20 人" in answer
    assert "新增专业 1 个：历史学 5 人" in answer
    assert "停招专业 1 个：计算机科学与技术 20 人" in answer
    assert "计划调整 1 个：物理学（师范） 10→15（+5）" in answer


def test_major_plan_change():
    db = _session()
    _seed(db)

    answer = build_plan_change_answer(db, "物理学在河南招生计划比去年增加了吗")["answer"]

    assert "10 人 → 15 人（+5）" in answer


def test_non_change_question_not_hijacked():
    db = _session()
    _seed(db)

    assert build_plan_change_answer(db, "河南招生计划是多少人") is None
    assert build_plan_change_answer(db, "转专业政策会调整吗") is None
