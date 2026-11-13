"""学费直答：按招生计划里的学费字段回答，不再落到“资料中未查到”。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import EnrollmentPlan, Major
from processor.tuition_query import build_tuition_answer


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
                province="吉林",
                plan_type="一般计划（普通类）",
                major_type="物理类",
                major_id=physics.id,
                tuition=5000,
            ),
            EnrollmentPlan(
                year=2026,
                province="吉林",
                plan_type="一般计划（公费师范）",
                major_type="物理类",
                major_id=physics.id,
                tuition=0,
            ),
            EnrollmentPlan(
                year=2026,
                province="吉林",
                plan_type="中外合作办学",
                major_type="物理类",
                major_id=computer.id,
                tuition=37000,
            ),
        ]
    )
    db.commit()


def test_tuition_answer_groups_plan_types():
    db = _session()
    _seed(db)

    result = build_tuition_answer(db, "每个专业的学费都是多少")

    assert result is not None
    assert result["intent"] == "plan"
    answer = result["answer"]
    assert "2026" in answer
    assert "免学费" in answer
    assert "37000 元/学年" in answer


def test_tuition_answer_for_named_major():
    db = _session()
    _seed(db)

    result = build_tuition_answer(db, "物理学的学费是多少")

    answer = result["answer"]
    assert "物理学" in answer
    assert "5000 元/学年" in answer
    assert "免学费" in answer


def test_non_tuition_question_is_ignored():
    db = _session()
    _seed(db)

    assert build_tuition_answer(db, "宿舍条件怎么样") is None


def test_tuition_followup_reuses_previous_major():
    db = _session()
    _seed(db)

    result = build_tuition_answer(db, "学费呢", "计算机科学与技术的学费是多少")

    answer = result["answer"]
    assert "承接上一条问题中的专业" in answer
    assert "计算机科学与技术" in answer
    assert "37000 元/学年" in answer
    assert "各计划类型的学费标准" not in answer
