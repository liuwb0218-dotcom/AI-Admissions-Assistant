"""学院系级采集的规则测试，不访问网络。"""

from collector.college_department_collector import (
    _base_major_name,
    _nav_departments,
    _valid_department,
    extract_departments,
    map_majors,
)


def test_extract_departments_keeps_real_names():
    text = "学院下设中国语言文学系、历史系和哲学系，各系均招收本科生。"
    names = set(extract_departments(text))
    assert {"中国语言文学系", "历史系", "哲学系"} <= names


def test_extract_departments_filters_generic_words():
    text = "这体现了院系之间的关系、课程体系和系统联系，各系都很重视。"
    assert extract_departments(text) == {}


def test_extract_departments_filters_approximate_count():
    text = "文学院下设国文系等三个系，历史悠久。"
    assert extract_departments(text) == {}


def test_nav_departments_reads_anchor_text():
    html = '<a href="/a.htm">历史系</a><a href="/b.htm">学科建设</a>'
    assert _nav_departments(html) == {"历史系": "导航链接"}


def test_valid_department_rules():
    assert _valid_department("数学系")
    assert not _valid_department("关系")
    assert not _valid_department("系所设置")
    assert not _valid_department("专业")


def test_extract_departments_keeps_full_name_with_conjunction():
    text = "学院下设计算机科学与技术系、自然地理与资源环境系和政治学与行政学系。"
    names = set(extract_departments(text))
    assert {"计算机科学与技术系", "自然地理与资源环境系", "政治学与行政学系"} <= names


def test_extract_departments_splits_conjunction_after_unit():
    text = "学院下设历史系和哲学系。"
    assert {"历史系", "哲学系"} <= set(extract_departments(text))


def test_extract_departments_strips_year_and_setting_prefix():
    text = "1994 年设立心理学系，2017 年设为独立建制的学院。"
    assert set(extract_departments(text)) == {"心理学系"}


def test_extract_departments_skips_history_sentence():
    text = "此后历经政治经济系、政治系等发展阶段。2004年1月，撤消数学系，成立数学与统计学院。"
    assert extract_departments(text) == {}


def test_map_majors_prefers_name_match():
    departments = {
        "会计学系": "学院设有会计学系",
        "企业管理系": "学院设有企业管理系",
    }
    mapping = map_majors(
        ["会计学（中外合作办学）"], departments, "学院设有企业管理系、会计学系。"
    )
    assert mapping["会计学（中外合作办学）"]["department"] == "会计学系"


def test_map_majors_picks_matching_unit_among_many():
    departments = {
        "英语系": "五个系：英语系 俄语系 商务英语系 大学外语系 日德语系",
        "俄语系": "五个系：英语系 俄语系 商务英语系 大学外语系 日德语系",
        "商务英语系": "五个系：英语系 俄语系 商务英语系 大学外语系 日德语系",
    }
    mapping = map_majors(
        ["英语（公费师范）", "商务英语", "俄语"],
        departments,
        "五个系：英语系 俄语系 商务英语系 大学外语系 日德语系",
    )
    assert mapping["英语（公费师范）"]["department"] == "英语系"
    assert mapping["商务英语"]["department"] == "商务英语系"
    assert mapping["俄语"]["department"] == "俄语系"


def test_map_majors_uses_same_sentence():
    departments = {"中国语言文学系": "中国语言文学系设有汉语言文学专业"}
    mapping = map_majors(
        ["汉语言文学（公费师范）"],
        departments,
        "中国语言文学系设有汉语言文学专业。",
    )
    assert mapping["汉语言文学（公费师范）"]["department"] == "中国语言文学系"


def test_map_majors_leaves_unmentioned_major_empty():
    mapping = map_majors(
        ["数据科学"], {"数学系": "数学系"}, "数学系设有数学与应用数学专业。"
    )
    assert mapping["数据科学"]["department"] is None


def test_base_major_name_strips_parentheses():
    assert _base_major_name("汉语言文学（公费师范）") == "汉语言文学"
