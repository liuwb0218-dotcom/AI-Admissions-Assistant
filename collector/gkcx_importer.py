"""从东北师大高考查询系统（gkcx.nenu.edu.cn）在线抓取招生计划与分专业分数。

接口（2026-11 实测）：
    GET  /api/user/param?which=plan|score&year=YYYY
         → {省份: [{major_type, type, year}, ...]}
    POST /api/user/queryPlan   {province, year(字符串), major_type, type}
         → {plans: [{major, major_attr, number, edu_len, edu_cost, ...}]}
    POST /api/user/queryScore  {province, year, major_type, type}
         → {scores: [{major, min_admission_score, min_rank, max_admission_score, ...}]}

抓到的数据先落成与 nenu_importer 相同表头的 CSV，再复用其清洗 / 专业维表 / 幂等 upsert，
避免出现第二套入库逻辑。

用法：
    python -m collector.gkcx_importer --years 2025 2026 --what plan --dry-run
    python -m collector.gkcx_importer --years 2025 2026 --what plan score
"""

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import requests

from collector.nenu_importer import import_neu_data

BASE = "https://gkcx.nenu.edu.cn"
PLAN_FIELDS = [
    "major",
    "province",
    "type",
    "major_type",
    "year",
    "major_attr",
    "number",
    "edu_len",
    "edu_cost",
]
SCORE_FIELDS = [
    "major",
    "province",
    "type",
    "major_type",
    "year",
    "major_attr",
    "min_admission_score",
    "min_rank",
    "baseline",
    "min_culture_score",
    "min_major_score",
    "max_culture_score",
    "max_major_score",
    "max_admission_score",
    "max_rank",
    "avg_culture_score",
    "avg_major_score",
    "avg_admission_score",
]
ENDPOINTS = {"plan": "/api/user/queryPlan", "score": "/api/user/queryScore"}


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; nenu-admission-assistant/1.0)",
            "Referer": f"{BASE}/",
        }
    )
    return session


def _request(session: requests.Session, method: str, path: str, delay: float, **kwargs):
    time.sleep(delay)
    url = BASE + path
    for attempt in range(3):
        try:
            response = session.request(method, url, timeout=30, **kwargs)
            if response.status_code == 200:
                return response.json()
            if response.status_code < 500:
                return None
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (attempt + 1))
    return None


def fetch_param(session: requests.Session, which: str, year: int, delay: float) -> List[Tuple]:
    """返回该年份下所有 (province, major_type, type) 组合。"""
    data = _request(session, "GET", f"/api/user/param?which={which}&year={year}", delay)
    if not isinstance(data, dict):
        return []
    combos = []
    for province, items in data.items():
        seen = set()
        for item in items or []:
            key = (province, item.get("major_type"), item.get("type"))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            combos.append(key)
    return combos


def crawl(years: List[int], kinds: List[str], delay: float) -> Dict[str, List[dict]]:
    session = _make_session()
    result = {"plan": [], "score": []}
    for year in years:
        for kind in kinds:
            combos = fetch_param(session, kind, year, delay)
            print(f"[param] {kind} {year}: {len(combos)} 个省份+计划类型组合")
            for index, (province, major_type, plan_type) in enumerate(combos, 1):
                payload = {
                    "province": province,
                    "year": str(year),
                    "major_type": major_type,
                    "type": plan_type,
                }
                data = _request(session, "POST", ENDPOINTS[kind], delay, json=payload)
                rows = (data or {}).get("plans" if kind == "plan" else "scores") or []
                for row in rows:
                    row["year"] = year
                    result[kind].append(row)
                if index % 20 == 0:
                    print(f"  {kind} {year} {index}/{len(combos)}，累计 {len(result[kind])} 行")
    return result


def write_csv(path: Path, rows: List[dict], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="抓取 gkcx.nenu.edu.cn 招生计划 / 分专业分数")
    parser.add_argument("--years", nargs="+", type=int, default=[2025, 2026])
    parser.add_argument("--what", nargs="+", choices=["plan", "score"], default=["plan", "score"])
    parser.add_argument("--delay", type=float, default=1.0, help="每次请求间隔秒数")
    parser.add_argument("--outdir", default="data/gkcx")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并统计，不入库")
    args = parser.parse_args(argv)

    data = crawl(args.years, args.what, args.delay)
    outdir = Path(args.outdir)
    paths = {}
    if "plan" in args.what:
        paths["plan"] = outdir / f"plan_{'_'.join(map(str, args.years))}.csv"
        write_csv(paths["plan"], data["plan"], PLAN_FIELDS)
        print(f"[csv] {paths['plan']}: {len(data['plan'])} 行")
    if "score" in args.what:
        paths["score"] = outdir / f"score_{'_'.join(map(str, args.years))}.csv"
        write_csv(paths["score"], data["score"], SCORE_FIELDS)
        print(f"[csv] {paths['score']}: {len(data['score'])} 行")

    if args.dry_run:
        print("[dry-run] 未写库")
        return 0

    from database.connection import SessionLocal

    db = SessionLocal()
    try:
        stats = import_neu_data(
            db,
            score_csv=paths.get("score"),
            plan_csv=paths.get("plan"),
        )
        db.commit()
    finally:
        db.close()
    for table, detail in stats.items():
        print(f"[db] {table}: {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
