"""阳光高考「2026 年各省一分一段表」汇总页采集器。

流程：
1. 用真实浏览器打开汇总页（该站有 JS 反爬，纯 requests 会返回 412）；
2. 解析「省市 | 相关文件」表格，得到每个省的公告页链接（一个省可能有多条，如山东夏季/春季、福建物理/历史）；
3. 打开每个公告页，判断表在哪：
   - 页面直接挂附件（xls / pdf / doc / zip）→ 下载；
   - 正文只有大图（扫描件）→ 下载图片，标记 image_only（后续需要 OCR）；
   - 正文是 HTML 表格 → 保存渲染后的 HTML；
   - 页面里还有「一分一段 / 分数段 / 成绩分布」等二级链接 → 继续跟进（最多 --max-depth 层）；
4. 结果存到 data/score_rank/{year}/，并生成 _manifest.json 记录每个省的来源与文件。

依赖（只装 Python 包，不下载浏览器，直接复用本机 Chrome）：
    pip install playwright

用法：
    # 全量
    python -m collector.score_rank_chsi_collector

    # 只采几个省，先看效果
    python -m collector.score_rank_chsi_collector --province 山东 --province 吉林

    # 只列清单不下载
    python -m collector.score_rank_chsi_collector --dry-run

    # 遇到验证码时显示浏览器，手动点过去
    python -m collector.score_rank_chsi_collector --headed
"""

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    raise SystemExit("缺少 playwright，请先执行：pip install playwright")

INDEX_URL = "https://gaokao.chsi.com.cn/gkxx/ss/202606/20260624/2293845984.html"
DEFAULT_YEAR = 2026
ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = ROOT / "data" / "score_rank"

PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "广西", "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西",
    "甘肃", "青海", "宁夏", "新疆",
]

# 附件扩展名：直接下载
FILE_EXTS = (".pdf", ".xls", ".xlsx", ".doc", ".docx", ".zip", ".rar", ".csv", ".txt")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp")

# 判断“这一页是不是还有更细的一分一段入口”
SUB_LINK_KEYWORDS = (
    "一分一段", "一分一档", "分数段", "成绩分布", "分数分布", "逐分段",
    "分段统计", "成绩统计表", "位次", "分数统计",
)
# 导航、页脚里的链接不跟进
NAV_WORDS = (
    "高考资讯", "阳光选科", "阳光志愿", "高招咨询", "招生动态", "院校库",
    "专业库", "院校满意度", "专业满意度", "专业推荐", "登录", "注册",
    "首页", "微信", "微博", "学信网", "帮助中心", "网站地图", "版权声明",
    "联系我们", "中心简介", "高考全知道",
)

CONTENT_TYPE_EXT = {
    "application/pdf": ".pdf",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/zip": ".zip",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


def _slug(text: str, limit: int = 40) -> str:
    """标题转文件名，只保留中文、字母、数字。"""
    cleaned = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", "_", text).strip("_")
    return cleaned[:limit] or "page"


def _ext_of(url: str) -> str:
    path = unquote(urlparse(url).path).lower()
    for ext in FILE_EXTS + IMG_EXTS:
        if path.endswith(ext):
            return ext
    return ""


def _province_of(cell: str, title: str) -> str:
    """优先用表格第一列，其次用标题前缀，例如「山东：2026年…」。"""
    for name in PROVINCES:
        if name in cell:
            return name
    for name in PROVINCES:
        if title.startswith(name) or title.startswith(f"{name}："):
            return name
    head = re.split(r"[：:，,]", title)[0]
    for name in PROVINCES:
        if name in head:
            return name
    return ""


def _render(page, url: str, timeout_ms: int) -> str:
    """打开页面并等反爬脚本执行完：内容长度稳定超过 5000 字节才算加载好。"""
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    deadline = time.time() + timeout_ms / 1000
    html = ""
    while time.time() < deadline:
        page.wait_for_timeout(800)
        html = page.content()
        if len(html) > 5000:
            break
    page.wait_for_timeout(1200)  # 留给懒加载图片
    return page.content()


def _scan(page) -> Dict:
    """一次性取回页面里的链接、大图、数字表格。"""
    return page.evaluate(
        """() => {
            const links = [];
            document.querySelectorAll('a').forEach(a => {
                const text = (a.innerText || '').trim();
                const href = a.href || '';
                if (!text || !href.startsWith('http')) return;
                const tr = a.closest('tr');
                const cell = tr && tr.cells[0] ? tr.cells[0].innerText.trim() : '';
                links.push({text, href, cell});
            });
            const images = [];
            document.querySelectorAll('img').forEach(img => {
                if (img.naturalWidth >= 500 && img.naturalHeight >= 200) {
                    images.push({src: img.src, w: img.naturalWidth, h: img.naturalHeight, alt: img.alt || ''});
                }
            });
            const tables = [];
            document.querySelectorAll('table').forEach(t => {
                const rows = t.rows ? t.rows.length : 0;
                if (rows >= 30) {
                    tables.push({rows, head: (t.innerText || '').slice(0, 120)});
                }
            });
            return {links, images, tables};
        }"""
    )


def _pick_attachments(links: List[Dict]) -> List[Dict]:
    picked, seen = [], set()
    for link in links:
        href = link["href"]
        ext = _ext_of(href)
        if not ext and "getfile" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        picked.append({"url": href, "text": link["text"], "ext": ext})
    return picked


def _pick_sub_links(links: List[Dict], current_url: str) -> List[Dict]:
    """正文里若还有“一分一段/分数段”等入口，说明真正的表在下一层。"""
    picked, seen = [], {current_url}
    for link in links:
        text = link["text"]
        href = link["href"]
        if href in seen or len(text) > 40:
            continue
        if not any(key in text for key in SUB_LINK_KEYWORDS):
            continue
        if any(word in text for word in NAV_WORDS):
            continue
        # 已经是附件就不算二级页面
        if _ext_of(href) in FILE_EXTS + IMG_EXTS:
            continue
        seen.add(href)
        picked.append({"url": href, "text": text})
    return picked[:6]


class Collector:
    def __init__(self, context, out_dir: Path, timeout_ms: int, delay: float,
                 max_depth: int, max_pages: int, dry_run: bool):
        self.context = context
        self.out_dir = out_dir
        self.timeout_ms = timeout_ms
        self.delay = delay
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.dry_run = dry_run
        self.downloaded = set()

    def _download(self, url: str, dest: Path) -> Optional[Path]:
        if self.dry_run or url in self.downloaded:
            return None
        response = self.context.request.get(url, timeout=60000)
        if response.status != 200:
            return None
        body = response.body()
        if not body:
            return None
        ext = _ext_of(url) or CONTENT_TYPE_EXT.get(
            (response.headers.get("content-type") or "").split(";")[0].strip(), ""
        )
        path = dest.with_suffix(ext or ".bin")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        self.downloaded.add(url)
        return path

    def _collect_page(self, page, url: str, province: str, title: str, dest: Path,
                      depth: int, visited: set) -> Dict:
        """采集单个页面；返回本页发现的文件与下一层链接。"""
        visited.add(url)
        _render(page, url, self.timeout_ms)
        scanned = _scan(page)

        html_path = dest / "pages" / f"{_slug(title)}{'.html'}"
        if not self.dry_run:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(page.content(), encoding="utf-8")

        files = []
        for index, item in enumerate(_pick_attachments(scanned["links"]), start=1):
            # 链接文案常带“点击下载:xxx.xls”，去掉后缀避免文件名出现 _xls.xls
            label = re.sub(
                r"\.(xls|xlsx|pdf|docx?|zip|rar|csv)$", "", item["text"] or title, flags=re.I
            )
            name = f"{_slug(province)}_{index:02d}_{_slug(label, 30)}"
            saved = self._download(item["url"], dest / name)
            files.append({
                "url": item["url"],
                "kind": "attachment",
                "path": str(saved.relative_to(ROOT)) if saved else "",
            })

        for index, image in enumerate(scanned["images"], start=1):
            name = f"{_slug(province)}_{index:02d}_图片"
            saved = self._download(image["src"], dest / name)
            if saved:
                files.append({
                    "url": image["src"],
                    "kind": "image",
                    "path": str(saved.relative_to(ROOT)),
                })

        sub_links = _pick_sub_links(scanned["links"], url) if depth < self.max_depth else []
        return {
            "page": url,
            "title": title,
            "html": str(html_path.relative_to(ROOT)) if not self.dry_run else "",
            "files": files,
            "sub_links": sub_links,
            "has_numeric_table": bool(scanned["tables"]),
        }

    def collect_entry(self, page, entry: Dict) -> Dict:
        province, title, url = entry["province"], entry["title"], entry["url"]
        dest = self.out_dir / province
        result = {
            "province": province,
            "title": title,
            "url": url,
            "status": "no_file",
            "files": [],
            "pages": [],
        }

        visited = set()
        current = [{"url": url, "title": title, "depth": 0}]
        pages_seen = 0
        while current and pages_seen < self.max_pages:
            node = current.pop(0)
            pages_seen += 1
            info = self._collect_page(
                page, node["url"], province, node["title"], dest, node["depth"], visited
            )
            result["pages"].append({"url": info["page"], "title": info["title"], "html": info["html"]})
            result["files"].extend(info["files"])
            for sub in info["sub_links"]:
                if sub["url"] not in visited:
                    current.append({"url": sub["url"], "title": sub["text"], "depth": node["depth"] + 1})
            if info["has_numeric_table"]:
                result["status"] = "html_table"
            time.sleep(self.delay)

        if result["files"]:
            kinds = {item["kind"] for item in result["files"]}
            if kinds == {"image"} and result["status"] != "html_table":
                result["status"] = "image_only"
            elif result["status"] == "no_file":
                result["status"] = "ok"
        elif result["status"] == "no_file" and len(result["pages"]) > 1:
            result["status"] = "no_file_deep"
        return result


def parse_index(page, index_url: str, timeout_ms: int) -> List[Dict]:
    _render(page, index_url, timeout_ms)
    links = page.evaluate(
        """() => Array.from(document.querySelectorAll('a')).map(a => {
            const tr = a.closest('tr');
            return {
                text: (a.innerText || '').trim(),
                href: a.href || '',
                cell: tr && tr.cells[0] ? tr.cells[0].innerText.trim() : ''
            };
        })"""
    )

    entries, seen = [], set()
    for link in links:
        text, href = link["text"], link["href"]
        if not href.startswith("http") or "/gkxx/" not in href or len(text) < 6:
            continue
        if not re.search(r"2026|分数|分段|成绩|统计|一分", text):
            continue
        province = _province_of(link["cell"], text)
        if not province or href in seen:
            continue
        seen.add(href)
        entries.append({"province": province, "title": text, "url": href})
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="采集阳光高考 2026 各省一分一段表")
    parser.add_argument("--index-url", default=INDEX_URL, help="汇总页地址")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR, help="数据年份")
    parser.add_argument("--province", action="append", default=None, help="只采指定省份，可重复")
    parser.add_argument("--out-dir", default=None, help="输出目录，默认 data/score_rank/{year}")
    parser.add_argument("--max-depth", type=int, default=1, help="二级链接跟进层数")
    parser.add_argument("--max-pages", type=int, default=8, help="每个省最多打开页面数")
    parser.add_argument("--delay", type=float, default=1.0, help="页面间间隔秒")
    parser.add_argument("--timeout", type=int, default=60000, help="单页超时毫秒")
    parser.add_argument("--headed", action="store_true", help="显示浏览器，便于手动过验证码")
    parser.add_argument("--dry-run", action="store_true", help="只列清单，不下载")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else OUT_ROOT / str(args.year)
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                channel="chrome",
                headless=not args.headed,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()

        print("读取汇总页…")
        entries = parse_index(page, args.index_url, args.timeout)
        if args.province:
            wanted = set(args.province)
            entries = [item for item in entries if item["province"] in wanted]

        print(f"共 {len(entries)} 条公告，覆盖 {len({e['province'] for e in entries})} 个省份")
        for entry in entries:
            print(f"  {entry['province']}\t{entry['title']}")

        if args.dry_run:
            print("\n--dry-run 结束，未下载文件")
            browser.close()
            return

        collector = Collector(
            context, out_dir, args.timeout, args.delay,
            args.max_depth, args.max_pages, args.dry_run,
        )
        results = []
        for index, entry in enumerate(entries, start=1):
            print(f"[{index}/{len(entries)}] {entry['province']} · {entry['title']}")
            try:
                result = collector.collect_entry(page, entry)
            except Exception as exc:  # 单条失败不影响整批
                result = {
                    "province": entry["province"],
                    "title": entry["title"],
                    "url": entry["url"],
                    "status": f"error: {exc}",
                    "files": [],
                    "pages": [],
                }
            results.append(result)
            print(f"    -> {result['status']}，文件 {len(result['files'])} 个")

        published = {item["province"] for item in results}
        manifest = {
            "year": args.year,
            "index_url": args.index_url,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entries": results,
            "missing_provinces": [name for name in PROVINCES if name not in published],
            "summary": {
                "entries": len(results),
                "provinces": len(published),
                "files": sum(len(item["files"]) for item in results),
                "image_only": sum(1 for item in results if item["status"] == "image_only"),
                "no_file": sum(1 for item in results if item["status"].startswith("no_file")),
            },
        }
        manifest_path = out_dir / "_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n完成：{manifest['summary']}")
        print(f"清单：{manifest_path}")
        if manifest["missing_provinces"]:
            print("尚未发布：" + "、".join(manifest["missing_provinces"]))
        browser.close()


if __name__ == "__main__":
    main()
