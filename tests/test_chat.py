"""问答链路测试：能返回答案与引用，敏感问题走强匹配。"""

from processor.chat_service import _build_messages, _combine_answers
from tests.conftest import login


def test_chat_returns_answer_with_refs(client):
    token = login(client, "pytest_chat_user")
    response = client.post(
        "/api/chat",
        json={"message": "计算机科学与技术的录取分数是多少"},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["code"] == 0
    assert body["data"]["answer"]
    assert body["data"]["refs"]
    assert body["data"]["conversation_id"] > 0


def test_sensitive_question_uses_standard_answer(client):
    token = login(client, "pytest_chat_user")
    response = client.post(
        "/api/chat",
        json={"message": "你们能包录取吗，花钱买个内部指标行不行"},
        headers={"Authorization": f"Bearer {token}"},
    )
    data = response.json()["data"]

    assert data["intent"] == "sensitive"
    assert "从未授权" in data["answer"]


def test_empty_message_rejected(client):
    token = login(client, "pytest_chat_user")
    response = client.post(
        "/api/chat",
        json={"message": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == 4001


def test_resolve_scope_falls_back_to_profile():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from database.connection import Base
    from database.models import StudentProfile, User
    from processor.score_query import _resolve_scope

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(username="scope", password_hash="x")
    db.add(user)
    db.flush()
    db.add(StudentProfile(user_id=user.id, province="河南", subject_combo="物理类"))
    db.commit()

    # 问句没写省份时，用资料里的省份，不再追问
    assert _resolve_scope(db, user, "化学专业需要多少分")[0] == "河南"
    # 问句明确写了别省时，仍以问句为准
    assert _resolve_scope(db, user, "吉林化学专业需要多少分")[0] == "吉林"


def test_llm_messages_include_profile():
    class Profile:
        province = "河南"
        subject_combo = "物理类"
        score = 599
        rank_no = 12000

    messages = _build_messages(
        [],
        "化学专业需要多少分",
        [{"title": "化学学院简介", "category": "学院", "content": "化学专业"}],
        Profile(),
    )
    content = messages[-1]["content"]

    assert "【考生资料】" in content
    assert "省份：河南" in content
    assert "位次：12000" in content


def test_combine_answers_keeps_all_sections():
    combined = _combine_answers(
        [
            ("学院归属", {"answer": "属于化学学院。", "refs": ["r1"]}),
            ("学费", {"answer": "学费 3850 元/年。", "refs": ["r1", "r2"]}),
        ]
    )

    assert combined["intent"] == "composite"
    assert "1. 学院归属" in combined["answer"]
    assert "2. 学费" in combined["answer"]
    assert "属于化学学院" in combined["answer"]
    assert "学费 3850" in combined["answer"]
    assert combined["refs"] == ["r1", "r2"]


def test_combine_answers_single_section_passthrough():
    item = {"answer": "属于化学学院。", "refs": []}
    result = _combine_answers([("学院归属", item)])
    assert result["answer"] == "属于化学学院。"
    assert result["refs"] == []
    assert _combine_answers([]) is None


def test_single_direct_answer_bullets_become_numbered():
    result = _combine_answers(
        [("专业目录", {"answer": "2026 年专业：\n· 计算机\n· 化学", "refs": ["r1"]})]
    )

    assert "1. 计算机" in result["answer"]
    assert "2. 化学" in result["answer"]
    assert "·" not in result["answer"]
    # 数字与正文内容不得改动
    assert "2026 年专业" in result["answer"]
