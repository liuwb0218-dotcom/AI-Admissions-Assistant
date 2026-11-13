"""学院-系别-专业采集草稿：只读数据库和官网，不写库，供人工核对后再生效。

用法：
    python -m collector.college_department_collector
    python -m collector.college_department_collector --college 文学院 --college 历史文化学院
    python -m collector.college_department_collector --limit 5 --delay 1
"""

import argparse
import html as html_lib
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from sqlalchemy.orm import Session

from database.connection import SessionLocal
from database.models import CollegeWebsite, Major

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_JSON = DATA_DIR / "college_departments_draft.json"
DEFAULT_MD = DATA_DIR / "college_departments_review.md"

# 学院官网里可能承载“系 / 中层单位”设置的栏目
PAGE_KEYWORDS = (
    "学院概况",
    "学部概况",
    "学院简介",
    "学部简介",
    "机构设置",
    "系所设置",
    "系部设置",
    "院系设置",
    "组织机构",
    "师资队伍",
    "本科教育",
    "人才培养",
)

UNIT_SUFFIXES = ("系",)
# “系”不存在时的中层单位后缀（学部/学院常见）
MIDDLE_SUFFIXES = ("学院", "书院", "研究院", "教研室")

# 出现这些词的句子说明的是建制设置，优先级高于一般叙述
SETTING_MARKERS = (
    "下设",
    "设有",
    "开设",
    "现有",
    "拥有",
    "包括",
    "包含",
    "设置",
    "组建",
    "成立",
    "设立",
    "教学机构",
    "教学单位",
    "科研机构",
    "机构设置",
)
# 整句跳过：历史沿革里的旧机构不是现行建制
HISTORY_MARKERS = ("撤消", "撤销", "历经", "沿革", "前身", "追溯")
# 点名里带这些词的候选直接丢弃
NAME_BAD = ("改为", "更名为", "前身", "撤消", "撤销", "合并为")

# 候选名前面常粘着这些动词，循环剥掉
NAME_PREFIXES = (
    "现有",
    "设有",
    "开设",
    "下设",
    "包括",
    "包含",
    "拥有",
    "设置",
    "组建",
    "成立",
    "设立",
    "建立",
    "以及",
    "同时",
    "目前",
    "的",
    "年",
    "月",
    "日",
)

SENTENCE_SPLIT = re.compile(r"[。；;\n]")
# “与/及/和”只在前面是“系”时当分隔符，否则会切断“计算机科学与技术系”
SEGMENT_SPLIT = re.compile(
    r"[、，,：:；;。\s/｜|]+"
    r"|(?<=系)[和及与]"
    r"|下设有|开设有|现有|设有|开设|下设|包括|包含|拥有|设置|组建|成立|设立|建立"
)

DEPT_STOP_SUFFIX = (
    "关系",
    "体系",
    "系列",
    "联系",
    "系统",
    "系数",
    "系部",
    "院系",
    "系所",
    "系里",
    "系内",
    "本系",
    "该系",
    "各系",
    "全系",
    "教学系",
)
DEPT_STOP_WORDS = (
    "专业",
    "研究",
    "中心",
    "办公",
    "实验",
    "年级",
    "班级",
    "主任",
    "老师",
    "学生",
    "课程",
    "学科",
    "单位",
    "机构",
    "设置",
    "各类",
    "书系",
)
# 中层单位额外排除的泛化词
MIDDLE_STOP_WORDS = (
    "大学",
    "学校",
    "招生",
    "考试",
    "附属",
    "办公",
    "年级",
    "班级",
    "学生",
    "专业",
    "职业",
    "高等",
    "研究所",
)
# 机构设置类页面，允许不依赖“下设/设有”等标记直接抽
ORG_PAGE_MARKERS = ("机构", "组织", "系所", "院系", "jgsz", "zzjg", "zzlb", "jgsz")


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0 (compatible; nenu-admission-assistant/1.0)"}
    )
    return session


def _fetch(session: requests.Session, url: str, delay: float, timeout: int) -> str:
    time.sleep(delay)
    try:
        response = session.get(url, timeout=timeout)
        if response.status_code != 200:
            return ""
        response.encoding = response.apparent_encoding or response.encoding or "utf-8"
        return response.text
    except requests.RequestException as exc:
        logger.warning("抓取失败 %s: %s", url, exc)
        return ""


def _clean_text(value: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _page_text(html: str) -> str:
    """保留段落和列表项，短导航文本也保留，便于识别“XX系”。"""
    body = re.sub(
        r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I
    )
    blocks = re.split(r"</(?:p|div|li|tr|h\d|td)>|<br\s*/?>", body, flags=re.I)
    texts = []
    for block in blocks:
        text = _clean_text(block)
        if len(text) < 4:
            continue
        if text.count(">") + text.count("|") >= 4:
            continue
        texts.append(text)
    return "。".join(texts)


def _candidate_links(html: str, base_url: str, max_len: int = 20) -> List[Tuple[str, str]]:
    base_host = urlparse(base_url).netloc
    links: List[Tuple[str, str]] = []
    seen = set()
    for href, raw_text in re.findall(
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I
    ):
        text = _clean_text(raw_text)
        if not text or len(text) > max_len:
            continue
        if not any(keyword in text for keyword in PAGE_KEYWORDS):
            continue
        full = urljoin(base_url, href)
        if not full.startswith("http") or urlparse(full).netloc != base_host:
            continue
        full = full.split("#")[0]
        if full in seen:
            continue
        seen.add(full)
        links.append((text, full))
    return links


def _strip_prefix(value: str) -> str:
    """去掉首尾非汉字和“现有/下设/设立/年”等粘连词。"""
    value = re.sub(r"^[^\u4e00-\u9fa5]+", "", value)
    value = re.sub(r"[^\u4e00-\u9fa5]+$", "", value)
    changed = True
    while changed:
        changed = False
        for prefix in NAME_PREFIXES:
            if value.startswith(prefix) and len(value) - len(prefix) >= 2:
                value = value[len(prefix):]
                changed = True
    return value


def _valid_unit(name: str, suffixes: Tuple[str, ...]) -> bool:
    """判断候选名是否像一个正式的教学单位名。"""
    if not any(name.endswith(suffix) for suffix in suffixes):
        return False
    if not 2 <= len(name) <= 12:
        return False
    if any(bad in name for bad in NAME_BAD):
        return False
    # “国文系等三个系”这类概数表述不是正式单位名
    if re.search(r"等|多|几|[一二三四五六七八九十两]个", name):
        return False
    if any(name.endswith(suffix) for suffix in DEPT_STOP_SUFFIX):
        return False
    if any(word in name for word in DEPT_STOP_WORDS):
        return False
    if name.startswith(("大学", "学校", "学院", "我校")):
        return False
    if ("大学" in name or "学校" in name) and len(name) > 6:
        return False
    if suffixes != UNIT_SUFFIXES:
        if any(word in name for word in MIDDLE_STOP_WORDS):
            return False
    return True


def _valid_department(name: str) -> bool:
    return _valid_unit(name, UNIT_SUFFIXES)


def extract_units(
    text: str, suffixes: Tuple[str, ...] = UNIT_SUFFIXES, require_setting: bool = False
) -> Dict[str, Tuple[str, int]]:
    """返回 {单位名: (证据句, 优先级)}；优先级 1 表示出现在“下设/设有”类句子。"""
    found: Dict[str, Tuple[str, int]] = {}
    for sentence in SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if not sentence or any(marker in sentence for marker in HISTORY_MARKERS):
            continue
        score = 1 if any(marker in sentence for marker in SETTING_MARKERS) else 0
        if require_setting and not score:
            continue
        for segment in SEGMENT_SPLIT.split(sentence):
            name = _strip_prefix(segment)
            if not _valid_unit(name, suffixes):
                continue
            if name not in found or score > found[name][1]:
                found[name] = (sentence[:160], score)
    return found


def extract_departments(text: str) -> Dict[str, str]:
    """返回 {系名: 证据句}，只保留看起来像正式系名的词。"""
    return {name: evidence for name, (evidence, _) in extract_units(text).items()}


def _nav_departments(html: str) -> Dict[str, str]:
    """导航里的“XX系”也收录，证据标记为导航链接。"""
    found: Dict[str, str] = {}
    for raw_text in re.findall(r'<a[^>]+href="[^"]+"[^>]*>(.*?)</a>', html, re.S | re.I):
        name = _strip_prefix(_clean_text(raw_text))
        if _valid_department(name) and name not in found:
            found[name] = "导航链接"
    return found


def _base_major_name(name: str) -> str:
    return re.sub(r"[（(].*?[)）]", "", name).strip()


def _normalize(name: str) -> str:
    return re.sub(r"[（(][^）)]*[）)]", "", name).strip()


def _unit_core(name: str) -> str:
    """去掉单位后缀，得到用于和专业名比对的核心词。"""
    core = _normalize(name)
    for suffix in ("教研室", "研究院", "学院", "书院", "系"):
        if core.endswith(suffix):
            return core[: -len(suffix)]
    return core


def _match_by_name(
    base: str, unit_cores: Dict[str, str]
) -> Optional[Tuple[Tuple[int, int], str]]:
    """专业名与单位核心词直接对应：完全相等优先，其次前缀包含且核心词更长者优先。"""
    best: Optional[Tuple[Tuple[int, int], str]] = None
    for unit, core in unit_cores.items():
        if not core:
            continue
        if base == core:
            score = (3, len(core))
        elif base.startswith(core) and len(core) >= 2:
            score = (2, len(core))
        elif core.startswith(base) and len(base) >= 2:
            score = (2, len(base))
        else:
            continue
        if best is None or score > best[0]:
            best = (score, unit)
    return best


def map_majors(
    major_names: List[str], departments: Dict[str, str], text: str
) -> Dict[str, Dict[str, Optional[str]]]:
    """专业归属：先按名称直接对应，再退化为“同句里专业名之前最近的单位”。"""
    unit_cores = {name: _unit_core(name) for name in departments}
    sentences = [item.strip() for item in text.split("。") if item.strip()]
    result: Dict[str, Dict[str, Optional[str]]] = {}
    for major_name in major_names:
        base = _normalize(_base_major_name(major_name))
        direct = _match_by_name(base, unit_cores)
        if direct:
            unit = direct[1]
            result[major_name] = {
                "department": unit,
                "evidence": departments[unit],
                "match": "名称对应",
            }
            continue

        assigned: Optional[str] = None
        evidence: Optional[str] = None
        for sentence in sentences:
            if base not in sentence:
                continue
            positions = [
                (sentence.rfind(name), name) for name in departments if name in sentence
            ]
            if not positions:
                continue
            major_pos = sentence.find(base)
            before = [item for item in positions if item[0] < major_pos]
            assigned = max(before)[1] if before else min(positions)[1]
            evidence = sentence[:160]
            break
        result[major_name] = {
            "department": assigned,
            "evidence": evidence,
            "match": "同句对应" if assigned else None,
        }
    return result


def _is_org_page(title: str, url: str) -> bool:
    haystack = f"{title}{url}"
    return any(marker in haystack for marker in ORG_PAGE_MARKERS)


def collect_college(
    session: requests.Session,
    db: Session,
    college: CollegeWebsite,
    delay: float,
    timeout: int,
    max_pages: int,
    exclude_names: Optional[set] = None,
) -> Dict[str, object]:
    """抓取单个学院，返回草稿结构；任何异常都落成 failed，不中断整体任务。"""
    pages: List[Dict[str, str]] = []
    visited = set()
    queue: List[Tuple[str, str]] = [(college.name, college.url)]
    html_by_url: Dict[str, str] = {}

    while queue and len(pages) < max_pages:
        title, url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        html = _fetch(session, url, delay, timeout)
        if not html:
            continue
        html_by_url[url] = html
        pages.append({"title": title, "url": url})
        for link_title, link_url in _candidate_links(html, url):
            if link_url not in visited and len(pages) + len(queue) < max_pages * 2:
                queue.append((link_title, link_url))

    majors = [
        major
        for major in db.query(Major).order_by(Major.id).all()
        if college.name in (major.department or "")
    ]
    major_names = [major.display_name for major in majors]

    departments: Dict[str, Dict[str, str]] = {}
    all_text_parts: List[str] = []
    for page in pages:
        html = html_by_url.get(page["url"], "")
        text = _page_text(html)
        all_text_parts.append(text)
        for name, (evidence, _) in extract_units(text).items():
            departments.setdefault(
                name, {"evidence": evidence, "source_url": page["url"]}
            )
        for name, evidence in _nav_departments(html).items():
            departments.setdefault(name, {"evidence": evidence, "source_url": page["url"]})

    # 没有“系”时，学部/学院的中层单位（学院、书院、研究院、教研室）兜底
    if not departments:
        blocked = set(exclude_names or set())
        blocked.add(college.name)
        for page in pages:
            html = html_by_url.get(page["url"], "")
            text = _page_text(html)
            org_page = _is_org_page(page["title"], page["url"])
            for name, (evidence, _) in extract_units(
                text, MIDDLE_SUFFIXES, require_setting=not org_page
            ).items():
                if name in blocked or name in college.name or college.name in name:
                    continue
                if any(name in other for other in blocked):
                    continue
                departments.setdefault(
                    name, {"evidence": evidence, "source_url": page["url"]}
                )

    mapping = map_majors(
        major_names,
        {name: item["evidence"] for name, item in departments.items()},
        "。".join(all_text_parts),
    )
    assigned = sum(1 for item in mapping.values() if item["department"])
    if not pages:
        status = "failed"
    elif not departments:
        status = "no_department"
    elif assigned == 0:
        status = "partial"
    else:
        status = "ok"

    return {
        "college": college.name,
        "url": college.url,
        "status": status,
        "departments": [
            {
                "name": name,
                "source_url": item["source_url"],
                "evidence": item["evidence"],
            }
            for name, item in departments.items()
        ],
        "majors": [
            {
                "name": major_name,
                "department": mapping[major_name]["department"],
                "evidence": mapping[major_name]["evidence"],
                "match": mapping[major_name]["match"],
            }
            for major_name in major_names
        ],
        "pages": pages,
    }


def _write_review_markdown(payload: Dict[str, object], path: Path) -> None:
    lines = [
        "# 学院-系别-专业采集草稿",
        "",
        f"生成时间：{payload['generated_at']}",
        "",
        "> 本文件仅用于人工核对，采集脚本不会写数据库。",
        "",
        "## 汇总",
        "",
        "| 学院 | 单位数 | 已归属专业 | 待确认专业 | 状态 |",
        "|---|---|---|---|---|",
    ]
    for college in payload["colleges"]:
        assigned = sum(1 for major in college["majors"] if major["department"])
        pending = len(college["majors"]) - assigned
        lines.append(
            f"| {college['college']} | {len(college['departments'])} | "
            f"{assigned} | {pending} | {college['status']} |"
        )

    for college in payload["colleges"]:
        lines.extend(["", f"## {college['college']}", "", f"官网：{college['url']}", ""])
        if college["departments"]:
            lines.extend(["| 系 / 中层单位 | 来源 | 证据 |", "|---|---|---|"])
            for department in college["departments"]:
                evidence = (department["evidence"] or "").replace("|", " ")
                lines.append(
                    f"| {department['name']} | {department['source_url']} | {evidence} |"
                )
        else:
            lines.append("未抽取到系名。")

        if college["majors"]:
            lines.extend(["", "### 专业归属", "", "| 专业 | 归属 | 方式 |", "|---|---|---|"])
            for major in college["majors"]:
                lines.append(
                    f"| {major['name']} | {major['department'] or '待确认'} | "
                    f"{major['match'] or '-'} |"
                )

        pending = [major for major in college["majors"] if not major["department"]]
        if pending:
            lines.extend(["", "待确认专业：" + "、".join(major["name"] for major in pending)])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="学院-系别-专业采集（只产草稿）")
    parser.add_argument("--college", action="append", help="只采集指定学院，可重复")
    parser.add_argument("--limit", type=int, help="最多采集多少个学院")
    parser.add_argument("--delay", type=float, default=0.8, help="每次请求间隔秒数")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=6, help="每个学院最多抓取页数")
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--no-review", action="store_true", help="只输出 JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = SessionLocal()
    try:
        query = db.query(CollegeWebsite)
        if not args.include_disabled:
            query = query.filter(CollegeWebsite.is_enabled == 1)
        all_names = {row.name for row in db.query(CollegeWebsite).all()}
        if args.college:
            query = query.filter(CollegeWebsite.name.in_(args.college))
        colleges = query.order_by(CollegeWebsite.id).all()
        if args.limit:
            colleges = colleges[: args.limit]
        if not colleges:
            logger.warning("没有找到要采集的学院")
            return

        session = _session()
        results = []
        for index, college in enumerate(colleges, 1):
            logger.info("[%s/%s] 采集 %s", index, len(colleges), college.name)
            results.append(
                collect_college(
                    session,
                    db,
                    college,
                    delay=args.delay,
                    timeout=args.timeout,
                    max_pages=args.max_pages,
                    exclude_names=all_names,
                )
            )
    finally:
        db.close()

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "colleges": results,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("草稿已写入 %s", args.output_json)
    if not args.no_review:
        _write_review_markdown(payload, args.output_md)
        logger.info("核对表已写入 %s", args.output_md)

    total_majors = sum(len(item["majors"]) for item in results)
    assigned = sum(
        1 for item in results for major in item["majors"] if major["department"]
    )
    logger.info(
        "汇总：%s 个学院，%s 个专业，已归属 %s（%.0f%%）",
        len(results),
        total_majors,
        assigned,
        (assigned / total_majors * 100) if total_majors else 0,
    )


if __name__ == "__main__":
    main()
