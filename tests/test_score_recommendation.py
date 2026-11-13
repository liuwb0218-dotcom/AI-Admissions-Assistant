"""按分数/位次推荐专业的测试。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import (
    AdmissionScore,
    Conversation,
    EnrollmentPlan,
    Major,
    Message,
    ScoreRankSegment,
    StudentProfile,
    User,
)
from processor.score_query import (
    SCORE_BEFORE_PATTERN,
    INLINE_SCORE_PATTERN,
    build_score_answer,
    is_score_question,
    _build_recommendation,
    _extract_major_type,
    _extract_province,
    _finalize_major_type,
    _merge_slots,
    _next_missing_slot,
    _parse_slots,
    _slot_question,
)


def _score_session():
    """一个 599 分河南物理类考生 + 化学 622、环境 605 两条录取数据。"""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    chem = Major(name="化学(公费师范)", display_name="化学（公费师范）", major_attr="公费师范")
    low = Major(name="环境科学与工程类", display_name="环境科学与工程类")
    db.add_all([chem, low])
    db.flush()
    db.add_all(
        [
            AdmissionScore(
                year=2026,
                province="河南",
                major_type="物理类",
                plan_type="一般计划",
                major_id=chem.id,
                min_score=622,
                min_rank=15935,
            ),
            AdmissionScore(
                year=2026,
                province="河南",
                major_type="物理类",
                plan_type="一般计划",
                major_id=low.id,
                min_score=605,
                min_rank=30000,
            ),
            EnrollmentPlan(
                year=2026,
                province="河南",
                major_type="物理类",
                plan_type="一般计划",
                major_id=chem.id,
            ),
            EnrollmentPlan(
                year=2026,
                province="河南",
                major_type="物理类",
                plan_type="一般计划",
                major_id=low.id,
            ),
        ]
    )
    user = User(username="score", password_hash="x")
    db.add(user)
    db.flush()
    db.add(StudentProfile(user_id=user.id, province="河南", subject_combo="物理类", score=599))
    db.commit()
    return db, user


def test_gap_over_ten_recommends_other_majors():
    db, user = _score_session()

    answer = build_score_answer(db, user, "化学专业需要多少分")["answer"]

    assert "超出 10 分冲刺区间" in answer
    assert "环境科学与工程类" in answer


def test_gap_within_ten_counts_as_sprint():
    db, user = _score_session()
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    profile.score = 615
    db.commit()

    answer = build_score_answer(db, user, "化学专业需要多少分")["answer"]

    assert "在 10 分冲刺区间内" in answer


def test_bare_score_counts_as_score_question():
    # 只写分数又问专业，也要进入分数匹配链路
    assert is_score_question("599可以报什么专业")


def test_score_flow_collects_slots_step_by_step():
    slots = _parse_slots(["599可以报什么专业"])
    assert slots["score"] == 599
    assert _next_missing_slot(slots) == "province"

    slots = _merge_slots(slots, _parse_slots(["河南"], pending="province"))
    assert _next_missing_slot(slots) == "major_type"

    slots = _merge_slots(slots, _parse_slots(["物理类"], pending="major_type"))
    assert slots["major_type"] == "物理类"
    assert _next_missing_slot(slots) == "rank"

    slots = _merge_slots(slots, _parse_slots(["8000"], pending="rank"))
    assert slots["rank"] == 8000
    assert _next_missing_slot(slots) == "adjust"

    slots = _merge_slots(slots, _parse_slots(["不接受"], pending="adjust"))
    assert slots["adjust"] is False
    assert _next_missing_slot(slots) == ""


def test_bare_subject_and_unknown_reply():
    # 考生只回“物理 / 历史”也要识别成科类
    assert _extract_major_type("物理") == "物理类"
    assert _extract_major_type("历史") == "历史类"
    # “不知道”表示没主意：位次跳过，不能当成“不接受调剂”
    assert _parse_slots(["不知道"], pending="rank")["rank_unknown"]
    assert _parse_slots(["不知道"], pending="adjust")["adjust"] is None


def test_score_before_pattern():
    assert SCORE_BEFORE_PATTERN.search("我考了598能报哪些专业")
    assert SCORE_BEFORE_PATTERN.search("最低分数在598之下")
    assert INLINE_SCORE_PATTERN.search("我 598 分能报哪些专业")


def test_is_score_question_with_score_before():
    assert is_score_question("我考了598，能报哪些专业")
    assert is_score_question("最低分数在598之下，有哪些专业可以报")


def test_extract_province_and_major_type():
    assert _extract_province("我是河南考生") == "河南"
    assert _extract_province("东北师范大学在黑龙江的分数线") == "黑龙江"
    assert _extract_province("我598分能报哪些专业") == ""
    assert _extract_major_type("河南 物理类") == "物理类"
    assert _extract_major_type("文科生") == "历史类"
    assert _finalize_major_type("上海", "") == "综合改革"
    assert _finalize_major_type("新疆", "物理类") == "理工"


def test_recommendation_asks_province_first():
    # 画像里没有省份时，先问省份
    class FakeProfile:
        province = None
        subject_combo = None
        score = 598
        rank_no = None
        adjust_accepted = None

    class FakeUser:
        id = 1

    class FakeSession:
        def query(self, model):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return FakeProfile()

    result = _build_recommendation(FakeSession(), FakeUser(), "我598分能报哪些专业")
    assert result and "哪个省份" in result["answer"]


def test_recommendation_uses_profile_slots():
    # 画像里五项齐全时不再追问，直接按画像匹配
    class FakeProfile:
        province = "河南"
        subject_combo = "物理类"
        score = 599
        rank_no = 8000
        adjust_accepted = 1

    class FakeUser:
        id = 1

    class FakeSession:
        def query(self, model):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return FakeProfile()

        def scalar(self):
            return None

    result = _build_recommendation(FakeSession(), FakeUser(), "能报哪些专业")
    answer = result["answer"]

    assert "哪个省份" not in answer
    assert "你的位次是多少" not in answer
    assert "是否接受专业调剂" not in answer
    assert "河南 · 物理类" in answer


def test_recommendation_requires_profile():
    # 无画像返回拦截文案
    class EmptySession:
        def query(self, model):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class FakeUser:
        id = 1

    result = _build_recommendation(EmptySession(), FakeUser(), "我598分能报哪些专业")
    assert result and "省份" in result["answer"]


def test_recommendation_requires_score():
    # 有省份科类但没分数
    class FakeProfile:
        province = "吉林"
        subject_combo = "物理类"
        score = None
        rank_no = None
        adjust_accepted = None

    class FakeUser:
        id = 1

    class FakeSession:
        def query(self, model):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return FakeProfile()

    result = _build_recommendation(FakeSession(), FakeUser(), "吉林能报哪些专业")
    assert result and "分数" in result["answer"]


def _flow_conversation(db, user):
    """构造“追问位次”的上一轮，用于验证续接。"""
    conversation = Conversation(user_id=user.id)
    db.add(conversation)
    db.flush()
    db.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role="user",
                content="我考了599分，能报哪些专业",
            ),
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="【志愿匹配】已记录：河南 · 物理类。你的位次是多少？",
                intent="score_flow",
            ),
        ]
    )
    db.commit()
    return conversation


def _accept_adjust(db, user):
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    profile.adjust_accepted = 1
    db.commit()


def test_combined_answer_keeps_flow_intent():
    from processor.chat_service import _combine_answers

    result = _combine_answers(
        [
            ("分数与位次", {"answer": "追问位次", "intent": "score_flow"}),
            ("专业目录", {"answer": "专业列表", "intent": "catalog"}),
        ]
    )
    assert result["intent"] == "score_flow"


def test_rank_slot_question_does_not_promise_estimation():
    text = _slot_question("rank", {"province": "吉林", "major_type": "历史类", "score": 600})
    assert "跳过位次" in text
    assert "估算" not in text


def test_unknown_rank_reply_continues_flow():
    db, user = _score_session()
    _accept_adjust(db, user)
    conversation = _flow_conversation(db, user)

    result = build_score_answer(db, user, "不清楚排名", conversation_id=conversation.id)

    assert result is not None
    assert result["intent"] != "score_flow"
    assert "河南" in result["answer"]
    assert "未提供位次" in result["answer"]
    # 不能落到检索链路的大模型矛盾回答
    assert "资料中未查到" not in result["answer"]


def test_unknown_rank_reply_uses_segment_table():
    db, user = _score_session()
    _accept_adjust(db, user)
    db.add_all(
        [
            ScoreRankSegment(
                province="河南",
                year=2026,
                major_type="物理类",
                score=600,
                segment_count=100,
                cumulative_count=20500,
            ),
            ScoreRankSegment(
                province="河南",
                year=2026,
                major_type="物理类",
                score=599,
                segment_count=100,
                cumulative_count=21000,
            ),
        ]
    )
    db.commit()
    conversation = _flow_conversation(db, user)

    result = build_score_answer(db, user, "不清楚排名", conversation_id=conversation.id)

    assert "位次约 21000" in result["answer"]
    assert "一分一段表估算" in result["answer"]
