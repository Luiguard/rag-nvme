#!/usr/bin/env python3
"""Indiziert Stack Exchange Posts.xml (aus 7z-Dumps) in LanceDB."""
import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RAG_PREFER_LOCAL", "1")

from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_core.config import DATA_DIR, INDEX_BATCH_SIZE
from rag_core.embeddings import embed_documents
from rag_core.indexing import open_prime_table
from rag_core.quality import is_indexable_content

TAG_RE = re.compile(r"<[^>]+>")


def strip_html(raw: str) -> str:
    t = html.unescape(raw or "")
    t = TAG_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def ingest_posts_xml(xml_path: Path, site: str, table, splitter) -> int:
    total = 0
    raw_batch: list[dict] = []
    min_score = 3  # nur Posts mit etwas Qualität

    def flush_raw_batch():
        nonlocal total
        if not raw_batch:
            return
        texts = [item["text"] for item in raw_batch]
        vectors = embed_documents(texts)
        if vectors and len(vectors) == len(texts):
            db_batch = []
            for i, item in enumerate(raw_batch):
                db_batch.append({
                    "vector": vectors[i],
                    "text": item["text"],
                    "source": item["source"],
                    "timestamp": item["timestamp"],
                })
            table.add(db_batch)
            total += len(db_batch)
        else:
            # Fallback
            db_batch = []
            for item in raw_batch:
                v = embed_documents([item["text"]])
                if v and len(v) == 1:
                    db_batch.append({
                        "vector": v[0],
                        "text": item["text"],
                        "source": item["source"],
                        "timestamp": item["timestamp"],
                    })
            if db_batch:
                table.add(db_batch)
                total += len(db_batch)
        raw_batch.clear()

    for event, elem in ET.iterparse(str(xml_path), events=("end",)):
        if elem.tag.endswith("row"):
            attrs = elem.attrib
            if attrs.get("PostTypeId") != "1":  # nur Fragen
                elem.clear()
                continue
            score = int(attrs.get("Score", "0") or 0)
            if score < min_score:
                elem.clear()
                continue
            title = attrs.get("Title", "")
            body = strip_html(attrs.get("Body", ""))
            tags = (attrs.get("Tags", "") or "").replace("<", "").replace(">", " ")
            text = f"# {title}\n\nTags: {tags}\n\n{body}"
            if len(text) < 120 or not is_indexable_content(text, f"stackexchange:{site}"):
                elem.clear()
                continue
            chunks = splitter.split_text(text)
            if chunks:
                ts = datetime.now().isoformat()
                src = f"stackexchange:{site}:{attrs.get('Id', '?')}"
                for chunk in chunks:
                    raw_batch.append({
                        "text": chunk,
                        "source": src,
                        "timestamp": ts,
                    })
                if len(raw_batch) >= INDEX_BATCH_SIZE:
                    flush_raw_batch()
        elem.clear()

    flush_raw_batch()
    return total


def main():
    se_root = DATA_DIR / "stackexchange"
    if not se_root.exists():
        print("❌ Kein data/stackexchange – zuerst Bulk-Download ausführen.")
        return 1

    table = open_prime_table(recreate=False)
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    grand = 0

    if len(sys.argv) > 1:
        target_site = sys.argv[1]
        site_dirs = [se_root / target_site]
    else:
        site_dirs = sorted(se_root.iterdir())

    for site_dir in site_dirs:
        if not site_dir.exists() or not site_dir.is_dir():
            continue
        xml = site_dir / "Posts.xml"
        if not xml.exists():
            for f in site_dir.rglob("Posts.xml"):
                xml = f
                break
        if not xml.exists():
            continue
        print(f"⚡ {site_dir.name} …")
        n = ingest_posts_xml(xml, site_dir.name, table, splitter)
        print(f"   → {n} Chunks")
        grand += n

    print(f"\n✨ Gesamt: {grand} Chunks | Tabelle: {table.count_rows():,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
