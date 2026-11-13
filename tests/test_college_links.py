"""学院官网链接匹配：问题里提到学院时给出官网（数据来自 college_websites 表）。"""

import pytest

from database.connection import SessionLocal
from database.models import CollegeWebsite
from processor.college_links import match_colleges
from processor.chat_service import (
    NO_EVIDENCE_ANSWER,
    _merge_links,
    _needs_school_link,
)


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def test_match_college_by_full_name(db):
    hits = match_colleges(db, "历史文化学院师资力量怎么样")
    assert hits and hits[0]["name"] == "历史文化学院"
    assert hits[0]["url"].startswith("http")


def test_match_college_alias_in_brackets(db):
    assert match_colleges(db, "新闻学院有哪些专业")[0]["name"] == "传媒科学学院(新闻学院)"
    assert match_colleges(db, "体育学院有冰雪专业吗")[0]["name"] == "体育学院(冰雪学院)"


def test_no_college_mentioned_returns_empty(db):
    assert match_colleges(db, "计算机科学与技术专业学什么") == []
    assert match_colleges(db, "") == []


def test_match_college_ignores_separator(db):
    # 官网名里带“•”，考生一般不会输入
    hits = match_colleges(db, "罗格斯大学纽瓦克学院有哪些专业")
    assert hits and hits[0]["name"].endswith("纽瓦克学院")


def test_merge_links_dedupes_by_url():
    school = {"name": "招生网", "url": "https://zsb.nenu.edu.cn/"}
    chem = {"name": "化学学院", "url": "http://chem.nenu.edu.cn/"}
    assert _merge_links([chem], [chem, school]) == [chem, school]
    assert _merge_links([], []) == []


def test_disabled_college_is_skipped(db):
    row = db.query(CollegeWebsite).filter(CollegeWebsite.name == "心理学院").first()
    row.is_enabled = 0
    db.commit()
    try:
        assert match_colleges(db, "心理学院怎么样") == []
    finally:
        row.is_enabled = 1
        db.commit()


def test_school_link_when_answer_is_insufficient():
    # 模型回答没给出结论时补招生网入口
    assert _needs_school_link("资料中未查到各专业逐一对应的学费标准", "local")
    # 规则兜底文案（未检索到资料）一定附链接
    assert _needs_school_link(NO_EVIDENCE_ANSWER, "rule")
    # 正常回答不额外加链接
    assert not _needs_school_link("历史学院现有专任教师 60 余人。", "local")
