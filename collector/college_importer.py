"""学院简介导入：把"学院简介_最终版"目录下的 txt 清洗、分块后写入知识库。

用法：
    python -m collector.college_importer                 # 默认目录（见 settings.college_intro_dir）
    python -m collector.college_importer --dir <目录>    # 指定目录
    python -m collector.college_importer --dry-run      # 只统计不写库

幂等：以 (category, title) 为唯一键，正文未变化时计入 skipped，可反复执行。
"""

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from config.settings import settings
from database.connection import SessionLocal, init_db
from database.models import KnowledgeDoc

logger = logging.getLogger(__name__)

CATEGORY = "major"
MAX_CHUNK = 600

_BOILERPLATE = re.compile(
    r"^(版权|copyright|all rights reserved|地址|邮编|电话|传真|"
    r"电子信箱|电子邮箱|电邮|师德师风|更新日期|数据统计日期|管理员邮箱|信息管理)",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    """去掉空格与各类括号，用于文件名与官网学院名的对齐。"""
    cleaned = re.sub(r"\s+", "", name)
    return re.sub(r"[()（）•·]", "", cleaned)


def _is_boilerplate(line: str) -> bool:
    lowered = line.lower()
    if _BOILERPLATE.match(line):
        return True
    if "版权所有" in line or "all rights reserved" in lowered:
        return True
    return False


def clean_paragraphs(text: str) -> List[str]:
    paragraphs: List[str] = []
    for raw in text.splitlines():
        line = raw.strip().replace("\u3000", " ").strip()
        if len(line) < 6:
            continue
        if _is_boilerplate(line):
            continue
        paragraphs.append(line)
    return paragraphs


def _split_long(paragraph: str, limit: int) -> List[str]:
    """超长段落先按句号切，仍超长时硬切。"""
    sentences = [part for part in re.split(r"(?<=[。！？；])", paragraph) if part]
    pieces: List[str] = []
    buffer = ""
    for sentence in sentences:
        if len(buffer) + len(sentence) <= limit:
            buffer += sentence
            continue
        if buffer:
            pieces.append(buffer)
        while len(sentence) > limit:
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        buffer = sentence
    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_paragraphs(paragraphs: List[str], limit: int = MAX_CHUNK) -> List[str]:
    chunks: List[str] = []
    buffer = ""
    for paragraph in paragraphs:
        for piece in _split_long(paragraph, limit):
            if not buffer:
                buffer = piece
            elif len(buffer) + 1 + len(piece) <= limit:
                buffer = f"{buffer}\n{piece}"
            else:
                chunks.append(buffer)
                buffer = piece
    if buffer:
        chunks.append(buffer)
    return chunks


def resolve_college_name(filename_stem: str, canonical_names: List[str]) -> str:
    target = normalize_name(filename_stem)
    for name in canonical_names:
        if normalize_name(name) == target:
            return name
    for name in canonical_names:
        candidate = normalize_name(name)
        if candidate.startswith(target) or target.startswith(candidate):
            return name
    # 文件名省略了校名前缀等情况（如"罗格斯大学纽瓦克学院"对应"东北师范大学罗格斯大学•纽瓦克学院"）
    for name in canonical_names:
        candidate = normalize_name(name)
        if target in candidate or candidate in target:
            return name
    return filename_stem


def load_url_map(urls_path: Optional[Path]) -> Dict[str, str]:
    if not urls_path or not urls_path.exists():
        return {}
    try:
        data = json.loads(urls_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("学院链接文件解析失败: %s", urls_path)
        return {}
    return {str(key): str(value) for key, value in data.items() if key and value}


def build_documents(
    directory: Path, canonical_names: List[str]
) -> List[Dict]:
    documents: List[Dict] = []
    for file_path in sorted(directory.glob("*.txt")):
        college = resolve_college_name(file_path.stem, canonical_names)
        try:
            text = file_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            logger.warning("读取失败 %s: %s", file_path.name, exc)
            continue

        chunks = chunk_paragraphs(clean_paragraphs(text))
        if not chunks:
            logger.warning("清洗后无有效正文，已跳过: %s", file_path.name)
            continue

        short_name = re.sub(r"(学院|学部)$", "", college)
        keywords = ",".join(filter(None, [college, short_name, "学院简介"]))
        for index, chunk in enumerate(chunks, start=1):
            title = f"{college}简介" if len(chunks) == 1 else f"{college}简介（{index}/{len(chunks)}）"
            documents.append(
                {
                    "category": CATEGORY,
                    "title": title,
                    "content": chunk,
                    "source_file": file_path.name,
                    "keywords": keywords,
                }
            )
    return documents


def import_colleges(
    db: Session,
    directory: Path,
    urls_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict:
    if not directory.is_dir():
        raise FileNotFoundError(f"学院简介目录不存在: {directory}")

    url_map = load_url_map(urls_path)
    canonical = list(url_map.keys())
    documents = build_documents(directory, canonical)

    existing = {
        doc.title: doc
        for doc in db.query(KnowledgeDoc).filter(KnowledgeDoc.category == CATEGORY).all()
    }

    inserted = updated = skipped = 0
    for item in documents:
        row = existing.get(item["title"])
        if row is None:
            if not dry_run:
                db.add(KnowledgeDoc(**item, source_type="file", sensitivity_level=0))
            inserted += 1
        elif row.content != item["content"] or row.keywords != item["keywords"]:
            if not dry_run:
                row.content = item["content"]
                row.keywords = item["keywords"]
                row.source_file = item["source_file"]
            updated += 1
        else:
            skipped += 1

    if not dry_run and (inserted or updated):
        db.flush()

    # 简介内容变短后，多余的旧分块（如 4/4）要删掉，避免残留过期正文
    processed_files = {item["source_file"] for item in documents}
    new_titles = {item["title"] for item in documents}
    stale = [
        row
        for row in existing.values()
        if row.source_file in processed_files and row.title not in new_titles
    ]
    if not dry_run:
        for row in stale:
            db.delete(row)
        if stale:
            db.flush()

    imported_colleges = {doc["title"].split("简介")[0] for doc in documents}
    missing = [name for name in canonical if name not in imported_colleges]

    return {
        "directory": str(directory),
        "files": len(list(directory.glob("*.txt"))),
        "colleges": len(imported_colleges),
        "chunks": len(documents),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "removed": len(stale),
        "missing": missing,
        "missing_urls": {name: url_map[name] for name in missing},
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导入学院简介到知识库")
    parser.add_argument("--dir", default=settings.college_intro_dir, help="学院简介 txt 目录")
    parser.add_argument("--urls", default=None, help="学院官网链接 json，默认取目录同级 college_urls.json")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args()

    directory = Path(args.dir)
    urls_path = Path(args.urls) if args.urls else directory.parent / "college_urls.json"

    init_db()
    db = SessionLocal()
    try:
        summary = import_colleges(db, directory, urls_path, dry_run=args.dry_run)
        if not args.dry_run:
            db.commit()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
