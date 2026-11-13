"""转专业直答测试：先追问意向专业，再给条件。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import Major, Message
from processor.catalog_query import build_catalog_answer
from processor.transfer_query import TRANSFER_INTENT, build_transfer_answer


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    db.add(Major(name="化学", display_name="化学", department="化学学院"))
    db.commit()


def test_ask_intended_major_first():
    db = _session()
    _seed(db)

    result = build_transfer_answer(db, "转专业有什么条件", conversation_id=1)

    assert result["intent"] == TRANSFER_INTENT
    assert "你想转入哪个专业" in result["answer"]


def test_requirements_when_major_in_question():
    db = _session()
    _seed(db)

    result = build_transfer_answer(db, "转专业到化学有什么要求", conversation_id=1)

    assert result["intent"] == "transfer"
    assert "化学学院" in result["answer"]
    assert "春季学期" in result["answer"]


def test_follow_up_answers_with_requirements():
    db = _session()
    _seed(db)
    db.add(
        Message(
            conversation_id=1,
            role="assistant",
            content="你想转入哪个专业？",
            intent=TRANSFER_INTENT,
        )
    )
    db.commit()

    result = build_transfer_answer(db, "化学", conversation_id=1)

    assert result["intent"] == "transfer"
    assert "化学学院" in result["answer"]


def test_unrelated_question_not_hijacked():
    db = _session()
    _seed(db)

    assert build_transfer_answer(db, "化学专业多少分") is None


def test_catalog_ignores_transfer_question():
    db = _session()

    assert build_catalog_answer(db, "转专业有什么条件") is None
