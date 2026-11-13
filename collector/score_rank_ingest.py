"""一分一段表批量入库：扫描 2026 采集文件 → 解析 → 校验 → 报告 → 写库。

用法：
    python -m collector.score_rank_ingest --dry-run
    python -m collector.score_rank_ingest --dry-run --provinces 天津 上海
    python -m collector.score_rank_ingest --commit
    python -m collector.score_rank_ingest --commit --provinces 山东

普通类写入 物理类 / 历史类 / 综合改革；艺术、体育、三校生等用独立 major_type（如
“艺术-美术与设计”）隔离，避免污染分数查位次的估算口径。
"""

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from collector.score_rank_formats import (
    TableBlock,
    detect_pdf_kind,
    parse_html,
    parse_ocr_pdf,
    parse_pdf,
    parse_xls,
)
from collector.score_rank_importer import import_score_rank

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "score_rank"
YEAR = 2026
REPORT_PATH = DATA_DIR / str(YEAR) / "_ingest_report.md"
OCR_CACHE = DATA_DIR / str(YEAR) / "_ocr_cache"

# 文件名不在 manifest files 里、单独放在年份根目录的扫描件
EXTRA_FILES = {
    "hebei.pdf": "河北",
    "henan_history.pdf": "河南",
    "henan_physics.pdf": "河南",
}

# 只入普通类与艺术体育，专项 / 春考 / 民族语言等口径不同的跳过
SKIP_REASONS = {
    "山东": [("春季", "春季高考（中职口径）")],
    "海南": [
        ("专项", "专项计划"),
        # 艺术类综合成绩带小数（423.9），整数 score 列存不下，整表不入库
        ("综合成绩", "艺术类综合成绩含小数，暂不入库"),
    ],
    "青海": [("专项", "专项计划"), ("民族", "民族班 / 民族语言"), ("预科", "民族预科")],
    "陕西": [("体育类", "体育专业课分数段，非高考总分")],
}


def skip_reason(province: str, name: str) -> str:
    for keyword, reason in SKIP_REASONS.get(province, []):
        if keyword in name:
            return reason
    return "按规则跳过"


@dataclass
class Task:
    province: str
    path: Path
    major_type: str = ""
    page_major: bool = False
    group_index: int = 0
    max_groups: int = 0
    note: str = ""


@dataclass
class Result:
    province: str
    major_type: str
    rows: List[dict] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    note: str = ""

    @property
    def status(self) -> str:
        return "ok" if self.rows and not self.issues else "reject"


def _name(path: Path) -> str:
    return path.name


def build_task(province: str, path: Path) -> Optional[Task]:
    name = _name(path)
    suffix = path.suffix.lower()
    if suffix not in (".xls", ".xlsx", ".pdf", ".html"):
        return None
    if name.startswith("_") or "_xls.xls" in name:
        return None
    for keyword, _reason in SKIP_REASONS.get(province, []):
        if keyword in name:
            return None

    def task(major_type: str, **kwargs) -> Task:
        return Task(province, path, major_type, **kwargs)

    if province == "山东":
        return task("综合改革")
    if province == "天津":
        return task("综合改革")

    if province == "山西":
        for keyword, major in (
            ("物理科目组合", "物理类"),
            ("历史科目组合", "历史类"),
            ("美术与设计", "艺术-美术与设计"),
            ("音乐教育类", "艺术-音乐教育"),
            ("音乐表演类_器乐", "艺术-音乐表演器乐"),
            ("音乐表演类_声乐", "艺术-音乐表演声乐"),
        ):
            if keyword in name:
                return task(major)
        return None

    if province == "上海":
        for keyword, major in (
            ("本科录取控制分数线", "综合改革"),
            ("美术与设计", "艺术-美术与设计"),
            ("戏剧影视表演", "艺术-戏剧影视表演"),
            ("戏剧影视导演", "艺术-戏剧影视导演"),
            ("服装表演", "艺术-服装表演"),
            ("播音与主持", "艺术-播音与主持"),
            ("音乐教育类", "艺术-音乐教育"),
            ("音乐表演类_声乐", "艺术-音乐表演声乐"),
            ("音乐表演类_器乐", "艺术-音乐表演器乐"),
            ("书法", "艺术-书法"),
            ("舞蹈", "艺术-舞蹈"),
            ("体育类", "体育类"),
        ):
            if keyword in name:
                return task(major)
        return None

    if province == "辽宁":
        if "物理学" in name:
            return task("物理类")
        if "历史学" in name:
            return task("历史类")
        if "体育" in name or "艺术" in name:
            return task("", page_major=True)
        return None

    if province == "安徽":
        return task("", page_major=True)

    if province == "江西":
        if "三校生" in name:
            return task("三校生")
        if "历史类" in name:
            return task("历史类")
        if "物理类" in name:
            return task("物理类")
        return None

    if province == "海南":
        if "普通类考生" in name:
            # 15 列并排：第一组是全体考生，其余是各选考科目
            return task("综合改革", max_groups=1)
        for keyword, major in (
            ("体育", "体育类"),
            ("美术与设计", "艺术-美术与设计"),
            ("舞蹈", "艺术-舞蹈"),
            ("播音与主持", "艺术-播音与主持"),
            ("书法", "艺术-书法"),
            ("音乐教育", "艺术-音乐教育"),
            ("音乐表演器乐", "艺术-音乐表演器乐"),
            ("音乐表演声乐", "艺术-音乐表演声乐"),
            ("戏剧影视表演", "艺术-戏剧影视表演"),
            ("戏剧影视导演", "艺术-戏剧影视导演"),
            ("服装表演", "艺术-服装表演"),
        ):
            if keyword in name:
                return task(major)
        return None

    if province == "宁夏":
        if "体育综合分" in name:
            return task("体育-历史" if "历史组" in name else "体育-物理")
        if "艺术综合分" in name:
            # 细分方向写在 PDF 首页标题括号里，按页标签取
            return task("", page_major=True)
        if "历史组" in name:
            return task("历史类")
        if "物理组" in name:
            return task("物理类")
        return None

    if province == "青海":
        if "1_" in name:
            return task("", page_major=True)
        if "2_" in name:
            return task("", page_major=True)
        if "3_" in name:
            return task("", page_major=True)
        return None

    if province == "陕西":
        if "普通物理" in name:
            return task("物理类")
        if "普通历史" in name:
            return task("历史类")
        return None

    if province == "河北":
        return task("", page_major=True)

    if province == "河南":
        if "history" in name:
            return task("历史类")
        if "physics" in name:
            return task("物理类")
        return None

    return None


def page_major(province: str, label: str) -> str:
    """按页标签判定科类；label 由解析层拼成“页首文字|表内分组”。"""
    tail = label.rsplit("|", 1)[-1]

    if province == "安徽":
        if "历史科目组合" in label:
            return "历史类"
        if "物理科目组合" in label:
            return "物理类"
        return ""

    if province == "辽宁":
        kind = "体育" if "体育" in label else "艺术" if "艺术" in label else ""
        if not kind:
            return ""
        return f"{kind}-{'物理' if '物理' in label else '历史'}"

    if province == "宁夏":
        match = re.search(r"[（(](历史组|物理组)\s*[-—]\s*([^)）]+)[)）]", label)
        if match:
            return "艺术-" + re.sub(r"[\s_]", "", match.group(2))
        return ""

    if province == "青海":
        if "历史组" in tail:
            return "历史类" if "普通类" in label else ("艺术-历史组" if "艺考" in label or "艺术" in label else "体育-历史")
        if "物理组" in tail:
            return "物理类" if "普通类" in label else ("艺术-物理组" if "艺考" in label or "艺术" in label else "体育-物理")
        return ""

    if province == "河北":
        if tail == "组1":
            return "物理类"
        if tail == "组2":
            return "历史类"
        return ""

    return ""


def _parse_blocks(task: Task) -> List[TableBlock]:
    suffix = task.path.suffix.lower()
    if suffix in (".xls", ".xlsx"):
        return parse_xls(task.path)
    if suffix == ".html":
        return parse_html(task.path)
    if detect_pdf_kind(task.path) == "image":
        return _parse_ocr(task.path)
    return parse_pdf(task.path, max_groups=task.max_groups)


def _parse_ocr(path: Path) -> List[TableBlock]:
    """扫描件的 OCR 结果落盘缓存，调参和重跑不用再等。"""
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    cache = OCR_CACHE / f"{path.stem}.json"
    if cache.exists():
        payload = json.loads(cache.read_text(encoding="utf-8"))
        return [TableBlock(label=b["label"], rows=b["rows"], note=b["note"]) for b in payload]
    logger.info("OCR 识别 %s（较慢，结果会缓存）", path.name)
    blocks = parse_ocr_pdf(path)
    cache.write_text(
        json.dumps(
            [{"label": b.label, "rows": b.rows, "note": b.note} for b in blocks],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return blocks


def _merge(blocks: List[TableBlock]) -> List[dict]:
    seen: Dict[int, dict] = {}
    for block in blocks:
        for row in block.rows:
            seen.setdefault(row["score"], row)
    return [seen[score] for score in sorted(seen, reverse=True)]


def check_rows(rows: List[dict]) -> List[str]:
    """分数严格递减、累计单调不减、人数与累计能对上（跳号表按累计差核对）。"""
    issues: List[str] = []
    if len(rows) < 5:
        return [f"有效行数过少（{len(rows)}）"]

    for previous, current in zip(rows, rows[1:]):
        if current["score"] >= previous["score"]:
            issues.append(f"分数未严格递减：{previous['score']} → {current['score']}")
            break

    for previous, current in zip(rows, rows[1:]):
        if current["cumulative_count"] < previous["cumulative_count"]:
            issues.append(
                f"累计人数回退：{previous['score']} 分 {previous['cumulative_count']} → "
                f"{current['score']} 分 {current['cumulative_count']}"
            )
            break

    gaps = [previous["score"] - current["score"] for previous, current in zip(rows, rows[1:])]
    if all(gap == 1 for gap in gaps):
        omitted = rows[0]["cumulative_count"] - rows[0]["segment_count"]
        total = sum(row["segment_count"] for row in rows) + omitted
        if total != rows[-1]["cumulative_count"]:
            issues.append(
                f"人数对不上：本段合计 + 首行之上 = {total}，末行累计 = {rows[-1]['cumulative_count']}"
            )
        return issues

    bad = [
        current["score"]
        for previous, current in zip(rows, rows[1:])
        if current["cumulative_count"] - previous["cumulative_count"] != current["segment_count"]
    ]
    if len(bad) > max(3, len(rows) // 20):
        issues.append(f"跳号表中 {len(bad)} 行累计差与本段人数不符（如 {bad[:3]}）")
    return issues


def collect(only: Optional[List[str]] = None) -> tuple:
    """返回 (results, skipped)。only 用于只跑指定省份。"""
    manifest_path = DATA_DIR / str(YEAR) / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    tasks: List[Task] = []
    skipped: List[tuple] = []
    for entry in manifest["entries"]:
        province = entry["province"]
        if only and province not in only:
            continue
        paths = [ROOT / item["path"] for item in entry.get("files") or []]
        paths += sorted((DATA_DIR / str(YEAR) / province / "pages").glob("*.html"))
        # manifest 的 files 里也可能记了页面 HTML，和 pages 目录重复
        unique, seen = [], set()
        for path in paths:
            key = str(path).lower()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        paths = unique
        for path in paths:
            if not path.exists():
                skipped.append((province, path.name, "文件缺失"))
                continue
            task = build_task(province, path)
            if task is None:
                skipped.append((province, path.name, skip_reason(province, path.name)))
                continue
            tasks.append(task)

    for name, province in EXTRA_FILES.items():
        if only and province not in only:
            continue
        path = DATA_DIR / str(YEAR) / name
        if path.exists():
            tasks.append(Task(province, path, "", page_major=True))

    grouped: Dict[tuple, Result] = {}
    for task in tasks:
        label = f"解析 {task.path.name}"
        logger.info("%s", label)
        try:
            blocks = _parse_blocks(task)
        except Exception as error:  # 单个文件失败不影响其他省
            skipped.append((task.province, task.path.name, f"解析失败：{error}"))
            continue
        if not blocks:
            reason = "未识别到表格"
            if province == "海南" and "综合成绩" in task.path.name:
                reason = "艺术类综合成绩含小数，score 字段为整数，暂不入库"
            skipped.append((task.province, task.path.name, reason))
            continue

        buckets: Dict[str, List[TableBlock]] = {}
        if task.page_major:
            for block in blocks:
                major = page_major(task.province, block.label)
                if not major:
                    continue
                buckets.setdefault(major, []).append(block)
        else:
            buckets[task.major_type] = blocks

        for major, items in buckets.items():
            result = grouped.setdefault((task.province, major), Result(task.province, major))
            result.files.append(task.path.name)
            result.rows = _merge([TableBlock(rows=result.rows)] + items)

    return list(grouped.values()), skipped


def _range(rows: List[dict]) -> str:
    return f"{rows[0]['score']} → {rows[-1]['score']}" if rows else "-"


def write_report(results: List[Result], skipped: List[tuple], committed: bool) -> None:
    lines = [
        f"# {YEAR} 年一分一段表入库报告",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 模式：{'已写库' if committed else 'dry-run（未写库）'}",
        f"- 数据源：`data/score_rank/{YEAR}/_manifest.json`",
        "",
        "## 可入库数据",
        "",
        "| 省份 | 科类 | 行数 | 分数区间 | 末行累计 | 校验 | 文件 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in sorted(results, key=lambda item: (item.province, item.major_type)):
        status = "通过" if result.status == "ok" else "；".join(result.issues)
        lines.append(
            f"| {result.province} | {result.major_type} | {len(result.rows)} | {_range(result.rows)} | "
            f"{result.rows[-1]['cumulative_count'] if result.rows else '-'} | {status} | "
            f"{'、'.join(result.files)} |"
        )

    lines += ["", "## 未入库文件", "", "| 省份 | 文件 | 原因 |", "| --- | --- | --- |"]
    for province, name, reason in skipped:
        lines.append(f"| {province} | {name} | {reason} |")

    total = sum(len(result.rows) for result in results if result.status == "ok")
    ok_count = sum(1 for result in results if result.status == "ok")
    lines += ["", f"合计：{ok_count} 个省份科类通过校验，{total} 行。", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="一分一段表批量入库")
    parser.add_argument("--dry-run", action="store_true", help="只解析并出报告，不写库")
    parser.add_argument("--commit", action="store_true", help="校验通过的数据写入数据库")
    parser.add_argument("--provinces", nargs="*", default=None, help="只处理指定省份")
    parser.add_argument("--prune", action="store_true", help="删除本次文件中不存在的旧分数行")
    args = parser.parse_args()
    if not args.dry_run and not args.commit:
        parser.error("请指定 --dry-run 或 --commit")

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    results, skipped = collect(args.provinces)

    committed = False
    if args.commit:
        from database.connection import SessionLocal, init_db

        init_db()
        db = SessionLocal()
        try:
            for result in results:
                if result.status != "ok":
                    logger.warning("跳过 %s %s：%s", result.province, result.major_type, result.issues)
                    continue
                stats = import_score_rank(
                    db,
                    province=result.province,
                    year=YEAR,
                    major_type=result.major_type,
                    rows=result.rows,
                    source_url="、".join(result.files)[:500],
                    prune=args.prune,
                )
                logger.info(
                    "%s %s：新增 %s，更新 %s，跳过 %s，删除 %s",
                    result.province,
                    result.major_type,
                    stats["inserted"],
                    stats["updated"],
                    stats["skipped"],
                    stats["deleted"],
                )
            db.commit()
        finally:
            db.close()
        committed = True

    write_report(results, skipped, committed)
    for result in results:
        logger.info(
            "%s %s：%s 行，%s",
            result.province,
            result.major_type,
            len(result.rows),
            "通过" if result.status == "ok" else result.issues,
        )
    logger.info("报告：%s", REPORT_PATH)


if __name__ == "__main__":
    main()
