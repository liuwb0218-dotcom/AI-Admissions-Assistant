"""课程类问句直答：不再被专业目录抢占，查不到时明确说未收录。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import CollegeWebsite, KnowledgeDoc, Major
from processor.catalog_query import build_catalog_answer
from processor.course_query import build_course_answer


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db, with_docs: bool = True):
    db.add_all(
        [
            CollegeWebsite(
                name="生命科学学院", aliases="", url="http://sky.nenu.edu.cn/", is_enabled=1
            ),
            CollegeWebsite(
                name="教育学部", aliases="", url="http://edu.nenu.edu.cn/", is_enabled=1
            ),
        ]
    )
    db.add(Major(name="生物科学", display_name="生物科学（师范）", department="生命科学学院"))
    db.flush()
    if with_docs:
        db.add(
            KnowledgeDoc(
                category="major",
                title="生命科学学院介绍",
                content="学院开设动物学、植物学等专业课程，持续推进一流课程建设。",
                source_file="生命科学学院.txt",
            )
        )
    db.commit()


def test_course_question_is_answered_by_course_builder():
    db = _session()
    _seed(db)

    result = build_course_answer(db, "生命科学专业的课程有哪些")

    assert result["intent"] == "course"
    assert "生命科学学院" in result["answer"]
    assert "动物学" in result["answer"]


def test_course_question_without_data_says_not_collected():
    db = _session()
    _seed(db, with_docs=False)

    answer = build_course_answer(db, "生命科学专业的课程有哪些")["answer"]

    assert "暂未收录" in answer
    assert "生命科学学院" in answer


def test_non_course_question_is_ignored():
    db = _session()
    _seed(db)

    assert build_course_answer(db, "东北师大有哪些专业") is None


def test_catalog_does_not_hijack_course_question():
    db = _session()
    _seed(db, with_docs=False)

    assert build_catalog_answer(db, "生命科学专业的课程有哪些") is None


def test_catalog_does_not_hijack_college_question():
    db = _session()
    _seed(db, with_docs=False)

    assert build_catalog_answer(db, "教育学部有哪些专业") is None
