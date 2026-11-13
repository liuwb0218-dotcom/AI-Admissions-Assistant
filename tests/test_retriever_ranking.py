"""检索排序回归：低频政策词（调剂）不能被通用词（专业）挤出结果。

用独立的少量文档构造语料，避免依赖真实数据库里是否已导入招生章程。
"""

import pytest

from database.connection import SessionLocal
from database.models import KnowledgeDoc
from processor.retriever import retrieve


@pytest.fixture()
def sample_docs():
    db = SessionLocal()
    docs = [
        KnowledgeDoc(
            category="policy",
            title="招生章程（测试-调剂）",
            content=(
                "第十五条 对于所报专业志愿无法满足并且服从专业调剂的考生，"
                "按照投档成绩由高至低顺序向其他专业进行调剂录取，额满为止。"
                "调剂范围为同一批次、同一科类的所有专业。"
            ),
        ),
        KnowledgeDoc(
            category="policy",
            title="转专业实施办法（测试）",
            content=(
                "第一条 为规范本科生转专业工作，学生可在大一学年结束后申请转专业，"
                "各专业须制定专业准入细则，并对申请学生进行考核。"
            ),
        ),
        KnowledgeDoc(
            category="major",
            title="计算机科学与技术专业介绍（测试）",
            content="本专业学制四年，培养计算机系统与软件开发人才，核心课程包括数据结构与算法。",
        ),
    ]
    db.add_all(docs)
    db.commit()
    try:
        yield [doc.id for doc in docs]
    finally:
        db.query(KnowledgeDoc).filter(KnowledgeDoc.id.in_([doc.id for doc in docs])).delete(
            synchronize_session=False
        )
        db.commit()
        db.close()


def _retrieve_titles(query: str, top_k: int = 3):
    db = SessionLocal()
    try:
        return [hit["title"] for hit in retrieve(db, query, top_k)]
    finally:
        db.close()


def test_adjustment_clause_ranks_first(sample_docs):
    titles = _retrieve_titles("服从专业调剂会不会被调到很冷门的专业")
    assert "招生章程（测试-调剂）" in titles[:2]


def test_transfer_major_ranks_first(sample_docs):
    titles = _retrieve_titles("转专业什么时候可以申请，有什么条件")
    assert any("转专业" in title for title in titles[:2])


def test_major_question_still_hits_major_intro(sample_docs):
    titles = _retrieve_titles("计算机科学与技术专业学什么")
    assert "计算机科学与技术专业介绍（测试）" in titles[:2]
