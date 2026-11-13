"""招生计划直答：问人数 / 学制时直接查 enrollment_plans。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import EnrollmentPlan, Major
from processor.plan_query import build_plan_answer


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    physics = Major(name="物理学", display_name="物理学（师范）")
    computer = Major(name="计算机科学与技术", display_name="计算机科学与技术")
    db.add_all([physics, computer])
    db.flush()
    db.add_all(
        [
            EnrollmentPlan(
                year=2026,
                province="河南",
                plan_type="一般计划（普通类）",
                major_type="物理类",
                major_id=physics.id,
                plan_count=4,
                edu_len="四年",
            ),
            EnrollmentPlan(
                year=2026,
                province="河南",
                plan_type="国家专项计划",
                major_type="物理类",
                major_id=physics.id,
                plan_count=5,
                edu_len="四年",
            ),
            EnrollmentPlan(
                year=2026,
                province="吉林",
                plan_type="一般计划（普通类）",
                major_type="物理类",
                major_id=computer.id,
                plan_count=30,
                edu_len="四年",
            ),
        ]
    )
    db.commit()


def test_plan_count_for_major_and_province():
    db = _session()
    _seed(db)

    result = build_plan_answer(db, "物理学在河南招多少人")

    assert result["intent"] == "plan"
    answer = result["answer"]
    assert "河南" in answer
    assert "一般计划（普通类） 4 人" in answer
    assert "国家专项计划 5 人" in answer
    assert "合计 9 人" in answer


def test_plan_count_for_province():
    db = _session()
    _seed(db)

    answer = build_plan_answer(db, "吉林省物理类招生计划是多少人")["answer"]

    assert "吉林" in answer
    assert "30 人" in answer


def test_duration_answer():
    db = _session()
    _seed(db)

    answer = build_plan_answer(db, "物理学学制几年")["answer"]

    assert "学制 四年" in answer


def test_plan_change_question_is_deferred():
    db = _session()
    _seed(db)

    # 计划同比变化交给 plan-diff / 检索链路
    assert build_plan_answer(db, "2026年招生计划有什么变化") is None
    # 非计划类问题不接管
    assert build_plan_answer(db, "宿舍条件怎么样") is None
