"""通用文档导入：把 data/raw_docs/<子目录>/*.txt 清洗、分块后写入知识库。

用法：
    python -m collector.doc_importer                # 扫描默认目录 data/raw_docs
    python -m collector.doc_importer --dir <目录>    # 指定目录（可为 raw_docs 根目录或单个子目录）
    python -m collector.doc_importer --dry-run      # 只统计不写库

子目录名映射知识库 category：policy→policy，life→faq。
幂等：同一来源文件反复导入时按 (category, title) 更新，旧的多余分块会被删除。
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from collector.college_importer import chunk_paragraphs
from config.settings import BASE_DIR
from database.connection import SessionLocal, init_db
from database.models import KnowledgeDoc

logger = logging.getLogger(__name__)

RAW_DOCS_DIR = BASE_DIR / "data" / "raw_docs"
CATEGORY_MAP = {"policy": "policy", "life": "faq"}

# txt 文件头三行：来源 / 抓取日期 / 标题
SOURCE_PREFIX = "来源："
DATE_PREFIX = "抓取日期："
TITLE_PREFIX = "标题："


def parse_document(file_path: Path) -> Tuple[str, List[str]]:
    """读取 txt，返回（标题, 正文段落）。文件头的元信息行不进入正文。"""
    text = file_path.read_text(encoding="utf-8-sig")
    title = file_path.stem
    paragraphs: List[str] = []
    for raw in text.splitlines():
        line = raw.strip().replace("\u3000", " ").strip()
        if not line:
            continue
        if line.startswith(SOURCE_PREFIX) or line.startswith(DATE_PREFIX):
            continue
        if line.startswith(TITLE_PREFIX):
            title = line[len(TITLE_PREFIX) :].strip() or title
            continue
        paragraphs.append(line)
    return title, paragraphs


def build_documents(directory: Path, category: str) -> List[Dict]:
    documents: List[Dict] = []
    for file_path in sorted(directory.glob("*.txt")):
        title, paragraphs = parse_document(file_path)
        chunks = chunk_paragraphs(paragraphs)
        if not chunks:
            logger.warning("清洗后无有效正文，已跳过: %s", file_path.name)
            continue

        for index, chunk in enumerate(chunks, start=1):
            chunk_title = title if len(chunks) == 1 else f"{title}（{index}/{len(chunks)}）"
            documents.append(
                {
                    "category": category,
                    "title": chunk_title,
                    "content": chunk,
                    "source_file": file_path.name,
                    "keywords": title,
                }
            )
    return documents


def import_directory(
    db: Session,
    directory: Path,
    category: str,
    dry_run: bool = False,
) -> Dict:
    if not directory.is_dir():
        raise FileNotFoundError(f"文档目录不存在: {directory}")

    documents = build_documents(directory, category)
    existing = {
        doc.title: doc
        for doc in db.query(KnowledgeDoc).filter(KnowledgeDoc.category == category).all()
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

    # 正文变短或分块数减少时，删掉旧版本残留的分块
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

    return {
        "directory": str(directory),
        "category": category,
        "files": len(list(directory.glob("*.txt"))),
        "chunks": len(documents),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "removed": len(stale),
        "dry_run": dry_run,
    }


def discover_directories(root: Path) -> List[Tuple[Path, str]]:
    """--dir 指向 raw_docs 根目录时扫描子目录，指向单个子目录时只处理该目录。"""
    if root.name in CATEGORY_MAP:
        return [(root, CATEGORY_MAP[root.name])]
    return [
        (root / name, category)
        for name, category in CATEGORY_MAP.items()
        if (root / name).is_dir()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="导入政策/生活服务文档到知识库")
    parser.add_argument("--dir", default=str(RAW_DOCS_DIR), help="文档根目录或单个子目录")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args()

    root = Path(args.dir)
    targets = discover_directories(root)
    if not targets:
        raise SystemExit(f"未找到可导入的子目录（policy/life）: {root}")

    init_db()
    db = SessionLocal()
    try:
        summaries = [
            import_directory(db, directory, category, dry_run=args.dry_run)
            for directory, category in targets
        ]
        if not args.dry_run:
            db.commit()
        total = sum(item["chunks"] for item in summaries)
        print(json.dumps({"total_chunks": total, "details": summaries}, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
