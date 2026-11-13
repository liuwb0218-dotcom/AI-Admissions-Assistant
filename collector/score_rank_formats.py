"""一分一段表多格式解析：XLS / HTML 内联表格 / PDF 文本层 / PDF 扫描件与图片 OCR。

统一输出 TableBlock：一组 (score, segment_count, cumulative_count) 行，加上表内科类标签。
不同省份的版面差异较大，这里按“布局族”分别处理：

- three    标准三列：分数 / 人数 / 累计人数
- multi    三列一组的并排版面（天津 6 个表、安徽 5 组、海南 7 组取第一组）
- two      只有分数段 / 累计人数两列（宁夏），本段人数由累计差推导
- labeled  带“科类名称 / 投档分类型”标签列（青海），按标签分组
- shared   一个分数列对应多组人数 / 累计（河北扫描件，物理与历史同行）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

CELL_NOISE = re.compile(r"[^\d,，\s.]")
CJK = re.compile(r"[\u4e00-\u9fff]")
NUMBER = re.compile(r"\d[\d,，]*")
TAIL_MARKS = ("及以下", "以下", "略")
HEAD_MARKS = ("及以上", "以上", "≥")
# 上海艺术类写成“574.0000- 574.9999”的区间，取区间下界作分数段
SCORE_RANGE = re.compile(r"(\d+)(?:\.\d+)?\s*[-–~至]\s*(\d+)(?:\.\d+)?")
ZERO_DECIMAL = re.compile(r"(\d+)\.0+(?!\d)")

Row = Dict[str, int]


@dataclass
class TableBlock:
    label: str = ""
    rows: List[Row] = field(default_factory=list)
    note: str = ""


def _normalize(text: str) -> str:
    """把“574.0000- 574.9999”归一成 574，“575.0000”归一成 575。"""
    text = SCORE_RANGE.sub(lambda match: match.group(1), text or "")
    return ZERO_DECIMAL.sub(lambda match: match.group(1), text)


def _cell(value) -> str:
    return "" if value is None else str(value).replace("\n", " ").strip()


def _numbers(text: str) -> List[int]:
    """去掉汉字水印与标点后再取数，避免“1会6”这类竖排插入字拆散数字。"""
    cleaned = CELL_NOISE.sub(" ", _normalize(text))
    return [int(m.replace(",", "").replace("，", "")) for m in NUMBER.findall(cleaned)]


def _is_tail(text: str) -> bool:
    return any(mark in (text or "") for mark in TAIL_MARKS)


def _is_header(text: str) -> bool:
    return ("分数" in text or "成绩" in text or "总分" in text) and (
        "人数" in text or "累计" in text or "累积" in text
    )


def _make_row(score: int, segment: int, cumulative: int) -> Optional[Row]:
    if not 0 < score <= 750:
        return None
    if segment < 0 or cumulative < 0 or cumulative < segment:
        return None
    return {"score": score, "segment_count": segment, "cumulative_count": cumulative}


def _is_prose(texts: Sequence[str]) -> bool:
    """说明性文字（“文化课成绩录取最低控制分数线是407”）不是数据行。

    数据行里也可能带类别名（“美术与设计类”），所以按数字占比判断而不是汉字占比。
    """
    joined = "".join(_cell(text) for text in texts)
    if not joined:
        return True
    if any(mark in joined for mark in HEAD_MARKS):
        return False
    digits = sum(char.isdigit() for char in joined)
    return digits / max(len(joined), 1) < 0.35


def _dedupe_desc(rows: List[Row]) -> List[Row]:
    seen: Dict[int, Row] = {}
    for row in rows:
        seen.setdefault(row["score"], row)
    ordered = [seen[score] for score in sorted(seen, reverse=True)]
    # 表尾常混入页码、备注等小数字，累计人数回退的行直接丢弃
    trimmed: List[Row] = []
    for row in ordered:
        if trimmed and row["cumulative_count"] < trimmed[-1]["cumulative_count"]:
            continue
        trimmed.append(row)
    return trimmed


def _row_numbers(cells: Sequence[str]) -> List[int]:
    """一行内的数字；“200及以下 略”这类结束行整格丢弃，避免打乱三列分组。"""
    values: List[int] = []
    for cell in cells:
        text = _cell(cell)
        if _is_tail(text):
            continue
        values.extend(_numbers(text))
    return values


def _has_decimal(text: str) -> bool:
    """综合成绩带真小数（423.9）时无法写进整数 score 列，整行跳过。"""
    return bool(re.search(r"\d\.\d", _normalize(text)))


def _parse_three(cells: Sequence[str]) -> Optional[Row]:
    values = _row_numbers(cells)
    if len(values) != 3:
        return None
    return _make_row(values[0], values[1], values[2])


def _parse_multi(cells: Sequence[str], width: int = 3, max_groups: int = 0) -> List[Row]:
    values = _row_numbers(cells)
    if len(values) == 2 and any(mark in " ".join(_cell(c) for c in cells) for mark in HEAD_MARKS):
        # 海南“800分及以上111人”：本段人数就是累计人数
        row = _make_row(values[0], values[1], values[1])
        return [row] if row else []
    groups = [values[i : i + width] for i in range(0, len(values) - width + 1, width)]
    if max_groups:
        groups = groups[:max_groups]
    rows = []
    for group in groups:
        row = _make_row(group[0], group[1], group[2])
        if row:
            rows.append(row)
    return rows


def _parse_two(cells: Sequence[str]) -> Optional[tuple]:
    values = _row_numbers(cells)
    if len(values) != 2:
        return None
    score, cumulative = values
    if not 0 < score <= 750 or cumulative < 0:
        return None
    return score, cumulative


def _two_to_rows(pairs: List[tuple]) -> List[Row]:
    """只有累计人数时，用相邻两行累计差反推本段人数。"""
    rows: List[Row] = []
    for index, (score, cumulative) in enumerate(pairs):
        previous = pairs[index - 1][1] if index else 0
        segment = cumulative - previous
        if segment < 0:
            segment = 0
        rows.append({"score": score, "segment_count": segment, "cumulative_count": cumulative})
    return rows


def _parse_shared_groups(matrix: Sequence[Sequence[str]]) -> List[List[Row]]:
    """河北：一个分数列后跟若干组 (人数, 累计)，同分数的各组要分开保存。"""
    groups: List[List[Row]] = []
    for cells in matrix:
        texts = [_cell(c) for c in cells]
        joined = " ".join(texts)
        if not joined.strip() or _is_header(joined) or _is_prose(texts) or _has_decimal(joined):
            continue
        if _is_tail(joined):
            break
        values = _row_numbers(texts)
        if len(values) < 3:
            continue
        score, rest = values[0], values[1:]
        for index in range(0, len(rest) - 1, 2):
            row = _make_row(score, rest[index], rest[index + 1])
            if not row:
                continue
            while len(groups) <= index // 2:
                groups.append([])
            groups[index // 2].append(row)
    return [_dedupe_desc(rows) for rows in groups]


def parse_matrix(matrix: Sequence[Sequence[str]], layout: str = "three", max_groups: int = 0) -> List[Row]:
    rows: List[Row] = []
    pairs: List[tuple] = []
    for cells in matrix:
        texts = [_cell(c) for c in cells]
        joined = " ".join(texts)
        if not joined.strip() or _is_header(joined) or _is_prose(texts) or _has_decimal(joined):
            continue
        if _is_tail(joined):
            break
        if layout == "two":
            pair = _parse_two(texts)
            if pair:
                pairs.append(pair)
        elif layout == "multi":
            rows.extend(_parse_multi(texts, max_groups=max_groups))
        else:
            row = _parse_three(texts)
            if row:
                rows.append(row)
    if layout == "two" and pairs:
        rows.extend(_two_to_rows(pairs))
    return _dedupe_desc(rows)


def _layout_of(header: Sequence[str]) -> tuple:
    """根据表头判定版面族与列宽。返回 (layout, max_groups)。"""
    text = " ".join(_cell(c) for c in header)
    if "科类名称" in text or "投档分类型" in text:
        return "labeled", 0
    score_cols = text.count("分数") + text.count("成绩") + text.count("总分")
    count_cols = text.count("人数") + text.count("累计") + text.count("累积")
    if score_cols == 1 and count_cols >= 4:
        return "shared", 0
    if "人数" not in text and ("累计" in text or "累积" in text):
        return "two", 0
    return "multi", 0


# ---------- XLS ----------

def parse_xls(path: Path) -> List[TableBlock]:
    import pandas as pd

    frame = pd.read_excel(path, sheet_name=0, header=None)
    matrix = [[_cell(v) for v in record] for record in frame.values.tolist()]
    # 山东的表把夏季 / 春季高考并排放在同一张 sheet，只取前三列
    return [
        TableBlock(
            label="",
            rows=parse_matrix(matrix, "multi", max_groups=1),
            note=f"xls {frame.shape[0]} 行",
        )
    ]


# ---------- HTML ----------

def parse_html(path: Path) -> List[TableBlock]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    best: List[List[str]] = []
    for table in soup.find_all("table"):
        matrix = [
            [_cell(cell.get_text(" ")) for cell in tr.find_all(["td", "th"])]
            for tr in table.find_all("tr")
        ]
        matrix = [row for row in matrix if row]
        if len(matrix) > len(best):
            best = matrix
    if not best:
        return []
    layout, max_groups = _layout_of(best[0])
    return [TableBlock(label="", rows=parse_matrix(best, layout, max_groups), note="html 表格")]


# ---------- PDF 文本层 ----------

def _guess_layout(table: Sequence[Sequence[str]]) -> tuple:
    """无表头的表（天津、宁夏按块切开的表）用首行数字个数猜布局。"""
    text = " ".join(_cell(c) for c in table[0]) if table else ""
    if _is_header(text):
        return _layout_of(table[0])
    counts = [len(_row_numbers(row)) for row in table[:6]]
    counts = [count for count in counts if count]
    if counts and max(counts) == 2:
        return "two", 0
    return "multi", 0


def _page_label(text: str) -> str:
    return " ".join((text or "").split())[:120]


def parse_pdf(path: Path, max_groups: int = 0) -> List[TableBlock]:
    import pdfplumber

    blocks: List[TableBlock] = []
    with pdfplumber.open(str(path)) as pdf:
        for index, page in enumerate(pdf.pages):
            label = _page_label(page.extract_text() or "")
            for table in page.extract_tables() or []:
                if not table:
                    continue
                head = " ".join(_cell(c) for c in table[0])
                if "科类名称" in head or any(
                    "科类名称" in " ".join(_cell(c) for c in row) for row in table[:3]
                ):
                    for block in _parse_labeled(table):
                        blocks.append(
                            TableBlock(
                                label=f"{label}|{block.label}",
                                rows=block.rows,
                                note=f"pdf p{index + 1} labeled",
                            )
                        )
                    continue
                layout, groups = _guess_layout(table)
                if layout == "shared":
                    for order, rows in enumerate(_parse_shared_groups(table), start=1):
                        blocks.append(
                            TableBlock(
                                label=f"{label}|组{order}",
                                rows=rows,
                                note=f"pdf p{index + 1} shared",
                            )
                        )
                    continue
                rows = parse_matrix(table, layout, max_groups or groups)
                if rows:
                    blocks.append(
                        TableBlock(label=label, rows=rows, note=f"pdf p{index + 1} {layout}")
                    )
    return blocks


def _parse_labeled(table: List[List[str]]) -> List[TableBlock]:
    """青海：科类名称 / 投档分类型 / 总分 / 人数 / 累计数，一个文件内混排多个科类。"""
    grouped: Dict[str, List[Row]] = {}
    for cells in table:
        texts = [_cell(c) for c in cells]
        if len(texts) < 5 or _is_header(" ".join(texts)):
            continue
        if _is_tail(" ".join(texts)):
            continue
        numbers = _numbers(texts[2])
        if not numbers:
            continue
        counts = _numbers(texts[3]) + _numbers(texts[4])
        if len(counts) != 2:
            continue
        label = texts[0] or texts[1]
        row = _make_row(numbers[0], counts[0], counts[1])
        if row:
            grouped.setdefault(label, []).append(row)
    return [
        TableBlock(label=label, rows=_dedupe_desc(rows), note="pdf labeled")
        for label, rows in grouped.items()
    ]


def detect_pdf_kind(path: Path) -> str:
    """扫描件判断：前两页几乎没有文本，就走 OCR。"""
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        chars = sum(len(page.extract_text() or "") for page in pdf.pages[:2])
    return "image" if chars < 50 else "text"


# ---------- OCR（扫描件 / 图片） ----------

_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    return _ENGINE


def _ocr_lines(image) -> List[List[str]]:
    """把 OCR 结果按 y 聚成行、按 x 排序成单元格。"""
    result, _ = _engine()(image)
    items = []
    for box, text, score in result or []:
        if float(score) < 0.5:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        items.append((min(ys), sum(ys) / 4, min(xs), text))
    items.sort(key=lambda item: item[1])
    lines: List[List[tuple]] = []
    for item in items:
        if lines and abs(item[1] - lines[-1][0][1]) < 26:
            lines[-1].append(item)
        else:
            lines.append([item])
    return [[cell[3] for cell in sorted(line, key=lambda cell: cell[2])] for line in lines]


def parse_ocr_image(image) -> List[TableBlock]:
    matrix = _ocr_lines(image)
    if not matrix:
        return []
    header_index = 0
    for index, row in enumerate(matrix[:6]):
        if _is_header(" ".join(row)):
            header_index = index
            break
    label = _page_label(" ".join(matrix[0]))
    layout, max_groups = _layout_of(matrix[header_index])
    if layout == "shared":
        return [
            TableBlock(label=f"{label}|组{order}", rows=rows, note="ocr shared")
            for order, rows in enumerate(_parse_shared_groups(matrix[header_index:]), start=1)
        ]
    rows = parse_matrix(matrix[header_index:], layout, max_groups)
    return [TableBlock(label=label, rows=rows, note=f"ocr {layout}")] if rows else []


def parse_ocr_pdf(path: Path, resolution: int = 200) -> List[TableBlock]:
    import pdfplumber

    blocks: List[TableBlock] = []
    with pdfplumber.open(str(path)) as pdf:
        for index, page in enumerate(pdf.pages):
            image = page.to_image(resolution=resolution).original
            for block in parse_ocr_image(image):
                block.note = f"ocr page{index + 1} {block.note}"
                blocks.append(block)
    return blocks


def parse_image(path: Path) -> List[TableBlock]:
    from PIL import Image

    with Image.open(path) as image:
        return parse_ocr_image(image.convert("RGB"))
