"""学院简介清洗与分块的单元测试。"""

from collector.college_importer import (
    MAX_CHUNK,
    build_documents,
    chunk_paragraphs,
    clean_paragraphs,
    resolve_college_name,
)


def test_clean_paragraphs_drops_boilerplate():
    text = (
        "东北师范大学心理学院的前身是1951年成立的教育系心理学教研室。\n"
        "地址：吉林省长春市人民大街5268号　邮编：130024\n"
        "版权所有©东北师范大学心理学院\n"
        "师德师风监督举报电话、邮箱：85098084\n"
        "学院现具有心理学一级学科博士学位授予权。\n"
    )
    assert clean_paragraphs(text) == [
        "东北师范大学心理学院的前身是1951年成立的教育系心理学教研室。",
        "学院现具有心理学一级学科博士学位授予权。",
    ]


def test_chunk_paragraphs_keeps_limit_and_content():
    paragraphs = ["甲" * 400, "乙" * 400, "丙" * 300]
    chunks = chunk_paragraphs(paragraphs, limit=MAX_CHUNK)
    assert all(len(chunk) <= MAX_CHUNK for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == "".join(paragraphs)


def test_resolve_college_name_handles_parenthesis_variants():
    canonical = ["体育学院(冰雪学院)", "国际汉学院（海外教育学院）"]
    assert resolve_college_name("体育学院冰雪学院", canonical) == "体育学院(冰雪学院)"
    assert (
        resolve_college_name("国际汉学院（海外教育学院）", canonical)
        == "国际汉学院（海外教育学院）"
    )


def test_build_documents_uses_canonical_name_and_chunks(tmp_path):
    (tmp_path / "体育学院冰雪学院.txt").write_text(
        "学院创建于1948年6月，是新中国成立前夕最早建立的体育系科。" * 20,
        encoding="utf-8-sig",
    )
    documents = build_documents(tmp_path, ["体育学院(冰雪学院)"])

    assert documents
    assert documents[0]["category"] == "major"
    assert documents[0]["title"].startswith("体育学院(冰雪学院)简介")
    assert all(len(item["content"]) <= MAX_CHUNK for item in documents)
