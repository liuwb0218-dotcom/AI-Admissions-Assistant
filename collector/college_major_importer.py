"""学院-本科专业归属采集：抓各学院官网“专业设置 / 本科专业”页，生成核对清单。

默认只采集不写库；核对 data/college_majors_draft.json 后用 --apply 写入。

用法：
    python -m collector.college_major_importer
    python -m collector.college_major_importer --college 信息科学与技术学院 --delay 0
    python -m collector.college_major_importer --apply        # 预览写库计划
    python -m collector.college_major_importer --apply --yes  # 写入 majors.department
"""

import argparse
import html as html_lib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from database.connection import SessionLocal
from database.models import CollegeWebsite, Major

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_JSON = DATA_DIR / "college_majors_draft.json"
DEFAULT_MD = DATA_DIR / "college_majors_draft.md"
# 官网专业目录名 → 招生计划专业名
ALIAS_FILE = DATA_DIR / "college_major_alias.json"

# 专业介绍页入口；没有直达入口时先进入“人才培养”类栏目页再找
DIRECT_KEYWORDS = ("专业设置", "本科专业", "专业介绍", "招生专业", "专业方向", "本科招生")
SECTION_KEYWORDS = ("人才培养", "本科生教育", "本科教育", "本科生", "本科教学", "教育教学")
# 专业设置页普遍缺失时，学院简介里通常也会写“设有…本科专业”
INTRO_KEYWORDS = ("学院简介", "学部简介", "学院概况", "学部概况")
MAX_PAGES = 4
# 只有带这些上下文词的句子才参与专业匹配，避免导航标题误命中
CONTEXT_MARKERS = (
    "设有", "开设", "设置", "招生", "招收", "现有", "包括", "包含", "本科专业",
)
# 研究生教育栏目、新闻详情页容易混进来，按 URL 直接排除
EXCLUDE_HREF = ("yjs", "graduate", "yjsjy", "yjszs", "sszs", "bszs")

STATUS_LABELS = {
    "ok": "已匹配",
    "no_page": "未找到专业页",
    "no_major": "页面无匹配专业",
}

# 抽候选专业时过滤掉的通用表述
CANDIDATE_STOP = (
    "本专业", "该专业", "各专业", "本科专业", "招生专业", "特色专业", "优势专业",
    "一流专业", "新增专业", "专业设置", "专业介绍", "专业方向", "专业课程",
    "转专业", "跨专业", "辅修专业", "第二专业", "相关专业", "个专业", "等专业",
    "专业建设", "专业发展", "专业人才", "专业素养", "专业基础", "专业知识",
    "专业技能", "专业学位", "专业硕士", "专业教师", "专业实践", "专业实习",
    "专业竞赛", "专业认证", "专业评估", "专业调整", "专业分流", "专业选择",
    "专业志愿", "专业组", "专业类", "专业代码",
)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0 (compatible; nenu-admission-assistant/1.0)"}
    )
    return session


def _fetch(session: requests.Session, url: str, delay: float) -> str:
    time.sleep(delay)
    try:
        response = session.get(url, timeout=15)
        if response.status_code != 200:
            return ""
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text
    except requests.RequestException as exc:
        logger.warning("抓取失败 %s: %s", url, exc)
        return ""


def _page_text(html: str) -> str:
    """只保留正文段落：导航块短且含大量分隔符，标题行也会被长度过滤掉。"""
    body = re.sub(
        r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I
    )
    blocks = re.split(r"</(?:p|div|li|tr|h\d|td)>|<br\s*/?>", body, flags=re.I)
    texts = []
    for block in blocks:
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", block))
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 20:
            continue
        if text.count(">") + text.count("|") >= 4:
            continue
        texts.append(text)
    return "。".join(texts)


def _candidate_links(
    html: str,
    base_url: str,
    keywords: Tuple[str, ...],
    max_len: int = 16,
    allow_info: bool = False,
    prefix: bool = False,
) -> List[Tuple[str, str]]:
    """返回 [(链接文案, 绝对地址)]，本科、专业设置优先。

    max_len 用于区分导航链接和新闻标题；prefix 要求文案以关键词开头。
    """
    scored: List[Tuple[int, str, str]] = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        text = " ".join(re.sub(r"<[^>]+>", "", text).split())
        if not text or len(text) > max_len:
            continue
        if prefix and not any(text.startswith(keyword) for keyword in keywords):
            continue
        if not prefix and not any(keyword in text for keyword in keywords):
            continue
        if any(word in text for word in ("硕士", "博士", "研究生")):
            continue
        full = urljoin(base_url, href)
        # 学院站的“本科招生”常跳到学校招生网或微信文章，不是专业设置页
        if not full.startswith("http") or urlparse(full).netloc != urlparse(base_url).netloc:
            continue
        if any(part in full for part in EXCLUDE_HREF):
            continue
        if not allow_info and "info/" in full:
            continue
        score = (2 if "本科" in text else 0) + (1 if "专业设置" in text else 0)
        scored.append((score, text, full))

    seen, links = set(), []
    for _, text, full in sorted(scored, key=lambda item: -item[0]):
        if full in seen:
            continue
        seen.add(full)
        links.append((text, full))
    return links


def _base_name(name: str) -> str:
    return re.split(r"[（(]", name)[0].strip()


def _load_alias_config(path: Path = ALIAS_FILE) -> Dict:
    """读取全局别名、按学院覆盖的别名、不写库名单与人工指定归属。"""
    config: Dict = {"aliases": {}, "college_aliases": {}, "exclude": {}, "manual": {}}
    if not path.exists():
        return config
    data = json.loads(path.read_text(encoding="utf-8"))
    config["aliases"] = {
        name: targets
        for name, targets in (data.get("aliases") or {}).items()
        if name and targets
    }
    config["college_aliases"] = {
        college: {name: targets for name, targets in mapping.items() if name and targets}
        for college, mapping in (data.get("college_aliases") or {}).items()
    }
    config["exclude"] = {
        college: list(names) for college, names in (data.get("exclude") or {}).items()
    }
    config["manual"] = {
        name: college
        for name, college in (data.get("manual") or {}).items()
        if name and college
    }
    return config


def _build_index(
    majors: List[Major], aliases: Optional[Dict[str, List[str]]] = None
) -> List[Tuple[str, List[Major], bool]]:
    """按专业本名聚合，长名在前；别名覆盖同名条目，便于把官网口径落到招生计划名。

    返回 [(匹配文本, 对应专业记录, 是否别名)]。
    """
    by_base: Dict[str, List[Major]] = {}
    by_name: Dict[str, Major] = {}
    for major in majors:
        by_base.setdefault(_base_name(major.name), []).append(major)
        by_name[major.name] = major

    index: Dict[str, Tuple[List[Major], bool]] = {
        base: (rows, False) for base, rows in by_base.items()
    }
    for name, targets in (aliases or {}).items():
        rows: List[Major] = []
        for target in targets:
            if target in by_name:
                rows.append(by_name[target])
            elif target in by_base:
                rows.extend(by_base[target])
        if rows:
            index[name] = (rows, True)
    return sorted(
        ((name, rows, via_alias) for name, (rows, via_alias) in index.items()),
        key=lambda item: -len(item[0]),
    )


def _match_sentence(
    sentence: str,
    index: List[Tuple[str, List[Major], bool]],
    bases: List[str],
) -> List[Tuple[str, List[Major], bool]]:
    """按长名优先匹配并对已占用的文本区间跳过，返回命中名称、记录与是否别名。"""
    found: List[Tuple[str, List[Major], bool]] = []
    occupied: List[Tuple[int, int]] = []
    for base, majors, via_alias in index:
        if len(base) < 2:
            continue
        pattern = re.escape(base) + r"(?!学院|学部|系|教研)"
        for match in re.finditer(pattern, sentence):
            start, end = match.span()
            # “历史文化学院”里的“化学”、“美术学院”里的“美术学”都不是专业名
            if sentence[end - 1 : end + 1] in ("学院", "学部"):
                continue
            # “数据科学与大数据技术”这种更长专业名要跳过；“A和B”仍是专业列表
            if sentence[end : end + 1] == "与":
                rest = sentence[end + 1 :]
                if not any(other != base and rest.startswith(other) for other in bases):
                    continue
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            occupied.append((start, end))
            found.append((base, majors, via_alias))
            break
    return found


# “设有 A、B 2 个本科专业”里的 A、B 列表
CANDIDATE_GROUP = re.compile(
    r"(?:专业设置[:：]?|设有|开设|现有|设置|包括|包含|以及)"
    r"([^。；;]{2,120}?)"
    r"(?:等)?\s*(?:\d+|[一两二三四五六七八九十]+)?\s*个?(?:本科)?专业"
)


def _candidates(text: str, matched_names: set) -> List[str]:
    """从“设有 / 开设 … 专业”句式里切出专业候选，补出题库里没有的新专业名。"""
    values = []
    for sentence in re.split(r"[。；;\n]", text):
        for group in CANDIDATE_GROUP.findall(sentence):
            for chunk in re.split(r"[、，,和]", group):
                value = re.sub(r"[（(][^）)]*[）)]", "", chunk).strip(" ：:）)(")
                if not (2 <= len(value) <= 15):
                    continue
                if not re.search(r"[\u4e00-\u9fa5]{2}", value):
                    continue
                if re.search(r"\d", value) or value[0] in "了的和与等获在为对从把被将":
                    continue
                if "专业" in value or "学院" in value or "大学" in value:
                    continue
                if any(stop in value for stop in CANDIDATE_STOP):
                    continue
                if value in matched_names or value in values:
                    continue
                values.append(value)
    return values[:12]


def _collect_college(
    college: CollegeWebsite,
    index: List[Tuple[str, List[Major], bool]],
    bases: List[str],
    delay: float,
    alias_names: Optional[set] = None,
    excluded: Optional[set] = None,
) -> Dict:
    result = {
        "college": college.name,
        "url": college.url,
        "status": "no_page",
        "matched": [],
        "candidates": [],
        "pages": [],
    }
    if not college.url:
        return result

    session = _session()
    home = _fetch(session, college.url, delay)
    if not home:
        return result

    # 首页有“专业设置”直达链接就用；没有则进“人才培养 / 本科教育”栏目再找
    links = _candidate_links(home, college.url, DIRECT_KEYWORDS)[:2]
    if not links:
        sections = _candidate_links(home, college.url, SECTION_KEYWORDS, max_len=12)
        for _, section_url in sections[:3]:
            section = _fetch(session, section_url, delay)
            if not section:
                continue
            links.extend(_candidate_links(section, section_url, DIRECT_KEYWORDS))
            if len(links) >= 2:
                break
        if not links:
            links = sections[:2]
    # 学院简介作为兜底，简介里常写“设有…等 N 个本科专业”；
    # 部分学院首页的“学院概况”只是栏目页，需要再进一层“学院简介”
    for label, url in _candidate_links(
        home, college.url, INTRO_KEYWORDS, max_len=24, prefix=True
    )[:1]:
        intro_html = _fetch(session, url, delay)
        deeper = (
            _candidate_links(
                intro_html,
                url,
                ("学院简介", "学部简介"),
                max_len=24,
                allow_info=True,
            )
            if intro_html
            else []
        )
        deeper = [(text, link) for text, link in deeper if link != url]
        links.append(deeper[0] if deeper else (label, url))
    seen, unique_links = set(), []
    for label, url in links:
        if url not in seen:
            seen.add(url)
            unique_links.append((label, url))
    links = unique_links[:MAX_PAGES]

    matched: Dict[str, Dict] = {}
    candidate_values: List[str] = []
    for label, url in links:
        html = _fetch(session, url, delay)
        if not html:
            continue
        text = _page_text(html)
        result["pages"].append({"title": label, "url": url})
        for sentence in re.split(r"[。；;\n]", text):
            if "专业" not in sentence or len(sentence) < 12:
                continue
            if not any(marker in sentence for marker in CONTEXT_MARKERS):
                continue
            for base, majors, via_alias in _match_sentence(sentence, index, bases):
                entry = matched.setdefault(
                    base,
                    {
                        "records": [major.name for major in majors],
                        "via_alias": via_alias,
                        "evidence": sentence[:160],
                    },
                )
                if not entry["evidence"]:
                    entry["evidence"] = sentence[:160]
        for value in _candidates(text, set(matched)):
            if value not in candidate_values:
                candidate_values.append(value)

    excluded = set(excluded or ())
    result["matched"] = [
        {
            "major": base,
            "records": value["records"],
            "via_alias": value["via_alias"],
            "excluded": base in excluded,
            "evidence": value["evidence"],
        }
        for base, value in matched.items()
    ]
    # 已通过别名归到招生计划名、或明确不写库的官网专业，不再重复提示
    skip_names = set(alias_names or ()) | excluded
    result["candidates"] = [
        value for value in candidate_values if value not in skip_names
    ][:12]
    if matched:
        result["status"] = "ok"
    elif links:
        result["status"] = "no_major"
    return result


def _conflicts(results: List[Dict]) -> Dict[str, List[str]]:
    """同一专业记录被多个学院命中时列出，写库前必须人工定归属。"""
    owners: Dict[str, List[str]] = {}
    for item in results:
        for entry in item["matched"]:
            if entry.get("excluded"):
                continue
            for record in entry["records"]:
                names = owners.setdefault(record, [])
                if item["college"] not in names:
                    names.append(item["college"])
    return {record: names for record, names in owners.items() if len(names) > 1}


def collect(
    college_names: Optional[List[str]] = None, delay: float = 0.3
) -> Dict:
    db = SessionLocal()
    try:
        query = db.query(CollegeWebsite).filter(CollegeWebsite.is_enabled == 1)
        if college_names:
            query = query.filter(CollegeWebsite.name.in_(college_names))
        colleges = query.order_by(CollegeWebsite.id).all()
        config = _load_alias_config()
        majors = db.query(Major).all()
        index = _build_index(majors, config["aliases"])
        jobs = []
        for college in colleges:
            overrides = config["college_aliases"].get(college.name) or {}
            college_index = (
                _build_index(majors, {**config["aliases"], **overrides})
                if overrides
                else index
            )
            jobs.append(
                (
                    college,
                    college_index,
                    [name for name, _, _ in college_index],
                    set(config["aliases"]) | set(overrides),
                    set(config["exclude"].get(college.name) or ()),
                )
            )
    finally:
        db.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda job: _collect_college(
                    job[0], job[1], job[2], delay, job[3], job[4]
                ),
                jobs,
            )
        )

    summary = {
        "colleges": len(results),
        "ok": sum(1 for item in results if item["status"] == "ok"),
        "no_page": sum(1 for item in results if item["status"] == "no_page"),
        "no_major": sum(1 for item in results if item["status"] == "no_major"),
        "majors_matched": len({m["major"] for item in results for m in item["matched"]}),
        "aliases_used": len(
            {
                entry["major"]
                for item in results
                for entry in item["matched"]
                if entry.get("via_alias") and not entry.get("excluded")
            }
        ),
        "skipped": sum(
            1 for item in results for entry in item["matched"] if entry.get("excluded")
        ),
    }
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "aliases": config["aliases"],
        "college_aliases": config["college_aliases"],
        "exclude": config["exclude"],
        "manual": config["manual"],
        "conflicts": _conflicts(results),
        "colleges": results,
    }


def write_markdown(path: Path, data: Dict) -> None:
    summary = data["summary"]
    aliases = data.get("aliases") or {}
    lines = [
        "# 学院-本科专业归属核对清单",
        "",
        f"生成时间：{data['generated_at']}",
        "",
        f"学院 {summary['colleges']} 个：已匹配 {summary['ok']}，"
        f"未找到专业页 {summary['no_page']}，页面无匹配专业 {summary['no_major']}；"
        f"共命中专业 {summary['majors_matched']} 个，"
        f"其中别名归入 {summary.get('aliases_used', 0)} 个，"
        f"跳过不写库 {summary.get('skipped', 0)} 个。",
        "",
        "> 核对方式：确认“命中专业”归属正确；“官网名 → 招生计划名”是别名映射后的结果，"
        "箭头右侧才是写库用的专业名。未找到专业页的学院需要手动补官网链接。",
        "",
    ]
    if aliases:
        lines.extend(["## 别名映射（官网专业名 → 招生计划专业名）", ""])
        for name, targets in sorted(aliases.items()):
            lines.append(f"- {name} → {'、'.join(targets)}")
        for college, mapping in (data.get("college_aliases") or {}).items():
            for name, targets in sorted(mapping.items()):
                lines.append(f"- {name} → {'、'.join(targets)}（{college}）")
        lines.append("")
    excluded = data.get("exclude") or {}
    if excluded:
        lines.extend(["### 不写库", ""])
        for college, names in excluded.items():
            lines.append(f"- {college}：{'、'.join(names)}（跳过，不写库）")
        lines.append("")
    manual = data.get("manual") or {}
    if manual:
        lines.extend(["### 人工指定归属（官网未提及、库里存在的记录）", ""])
        for name, college in manual.items():
            lines.append(f"- {name} → {college}")
        lines.append("")
    conflicts = data.get("conflicts") or {}
    if conflicts:
        lines.extend(["## 归属冲突（同一专业记录被多个学院命中，写库前需定唯一归属）", ""])
        for record, owners in sorted(conflicts.items()):
            lines.append(f"- {record}：{'、'.join(owners)}")
        lines.append("")

    def label(entry: Dict) -> str:
        if entry.get("excluded"):
            return f"{entry['major']}（跳过，不写库）"
        if entry.get("via_alias"):
            return f"{entry['major']} → {'、'.join(entry['records'])}"
        if len(entry["records"]) > 1:
            return f"{entry['major']}（{len(entry['records'])} 条记录）"
        return entry["major"]

    for item in data["colleges"]:
        lines.append(f"## {item['college']}")
        lines.append("")
        lines.append(f"- 状态：{STATUS_LABELS[item['status']]}")
        lines.append(f"- 官网：{item['url']}")
        if item["pages"]:
            pages = "；".join(f"[{page['title']}]({page['url']})" for page in item["pages"])
            lines.append(f"- 采集页：{pages}")
        if item["matched"]:
            names = "、".join(label(entry) for entry in item["matched"])
            lines.append(f"- 命中专业（{len(item['matched'])}）：{names}")
        if item["candidates"]:
            lines.append(f"- 待确认候选：{'、'.join(item['candidates'])}")
        if item["matched"]:
            lines.append("")
            lines.append("<details><summary>证据句</summary>")
            lines.append("")
            for entry in item["matched"]:
                lines.append(f"- {label(entry)}：{entry['evidence']}")
            lines.append("")
            lines.append("</details>")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _build_plan(
    data: Dict, manual: Optional[Dict[str, str]] = None
) -> Dict[str, List[str]]:
    """按清单汇总每个专业记录应归属的学院，跳过标记不写库的条目。"""
    plan: Dict[str, List[str]] = {}
    for item in data["colleges"]:
        for entry in item["matched"]:
            if entry.get("excluded"):
                continue
            for record in entry["records"]:
                colleges = plan.setdefault(record, [])
                if item["college"] not in colleges:
                    colleges.append(item["college"])
    # 官网没提到、但库里存在的大类/合作办学记录，按人工指定补齐
    for name, college in (manual or {}).items():
        colleges = plan.setdefault(name, [])
        if college not in colleges:
            colleges.append(college)
    return plan


def apply_departments(data: Dict, commit: bool = False) -> Dict:
    """把清单写入 majors.department；commit=False 时只预览并回滚。"""
    plan = _build_plan(data, _load_alias_config()["manual"])
    db = SessionLocal()
    changes, unchanged = [], 0
    try:
        rows: Dict[str, List[Major]] = {}
        for major in db.query(Major).filter(Major.name.in_(list(plan))).all():
            rows.setdefault(major.name, []).append(major)

        for name, colleges in plan.items():
            department = "、".join(colleges)
            for major in rows.get(name, []):
                if (major.department or "") == department:
                    unchanged += 1
                    continue
                changes.append(
                    {"id": major.id, "name": name, "old": major.department, "new": department}
                )
                major.department = department

        if commit:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()
    return {
        "planned": len(plan),
        "changes": changes,
        "unchanged": unchanged,
        "missing": sorted(set(plan) - set(rows)),
        "committed": commit,
    }


def _print_apply(stats: Dict) -> None:
    changes = stats["changes"]
    print(
        f"清单专业 {stats['planned']} 个：待更新 {len(changes)} 条，"
        f"已是最新 {stats['unchanged']} 条。"
    )
    if stats["missing"]:
        print(f"库内缺失 {len(stats['missing'])} 个：{'、'.join(stats['missing'])}")
    for change in changes[:10]:
        print(f"  {change['name']}: {change['old'] or 'None'} → {change['new']}")
    if len(changes) > 10:
        print(f"  ... 其余 {len(changes) - 10} 条")
    print("已写入数据库。" if stats["committed"] else "仅预览，未写入；加 --yes 执行写入。")


def main() -> None:
    parser = argparse.ArgumentParser(description="采集学院-本科专业归属，生成核对清单")
    parser.add_argument("--college", nargs="*", help="只采集指定学院")
    parser.add_argument("--delay", type=float, default=0.3, help="每次请求间隔秒数")
    parser.add_argument("--json", default=str(DEFAULT_JSON), help="JSON 清单输出路径")
    parser.add_argument("--md", default=str(DEFAULT_MD), help="Markdown 清单输出路径")
    parser.add_argument(
        "--apply", action="store_true", help="不采集，按已有 JSON 清单写入 majors.department"
    )
    parser.add_argument("--yes", action="store_true", help="配合 --apply 真正写库，不加只预览")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.apply:
        json_path = Path(args.json)
        if not json_path.exists():
            print(f"清单不存在：{json_path}")
            return
        data = json.loads(json_path.read_text(encoding="utf-8"))
        _print_apply(apply_departments(data, commit=args.yes))
        return

    data = collect(args.college, args.delay)

    json_path = Path(args.json)
    md_path = Path(args.md)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_path, data)

    summary = data["summary"]
    print(
        f"学院 {summary['colleges']} 个：已匹配 {summary['ok']}，"
        f"未找到专业页 {summary['no_page']}，页面无匹配专业 {summary['no_major']}；"
        f"共命中专业 {summary['majors_matched']} 个，"
        f"其中别名归入 {summary.get('aliases_used', 0)} 个，"
        f"跳过 {summary.get('skipped', 0)} 个；"
        f"归属冲突 {len(data.get('conflicts') or {})} 个。"
    )
    print(f"清单：{json_path}")
    print(f"清单：{md_path}")


if __name__ == "__main__":
    main()
