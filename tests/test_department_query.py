"""专业归属直答测试：专业 -> 学院，数据来自 majors.department。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import Major
from processor.department_query import build_department_answer


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    db.add_all(
        [
            Major(name="化学", display_name="化学", department="化学学院"),
            Major(
                name="化学(公费师范)",
                display_name="化学（公费师范）",
                major_attr="公费师范",
                department="化学学院",
            ),
            Major(
                name="思想政治教育(公费师范)",
                display_name="思想政治教育（公费师范）",
                major_attr="公费师范",
                department="政法学院、马克思主义学部",
            ),
            Major(name="数据科学", display_name="数据科学", department=None),
        ]
    )
    db.commit()


def test_single_department_answer():
    db = _session()
    _seed(db)

    result = build_department_answer(db, "化学专业属于哪个学院")

    assert result["intent"] == "department"
    assert "属于化学学院" in result["answer"]


def test_shared_department_answer():
    db = _session()
    _seed(db)

    answer = build_department_answer(db, "思想政治教育（公费师范）是哪个学院的")["answer"]

    assert "共同开设" in answer
    assert "政法学院、马克思主义学部" in answer


def test_missing_department_gives_tip():
    db = _session()
    _seed(db)

    answer = build_department_answer(db, "数据科学属于哪个学院")["answer"]

    assert "暂未收录" in answer
    assert "招生网" in answer


def test_department_with_division_question_adds_tip():
    db = _session()
    _seed(db)

    answer = build_department_answer(db, "化学专业属于哪个学院哪个系")["answer"]

    assert "属于化学学院" in answer
    assert "系一级的划分暂未收录" in answer


def test_other_questions_not_hijacked():
    db = _session()
    _seed(db)

    assert build_department_answer(db, "化学专业需要多少分") is None
    assert build_department_answer(db, "化学学院有哪些专业") is None
