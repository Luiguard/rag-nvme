#!/usr/bin/env python3
"""Streaming-Ingestion einzelner Dumps/PDFs in LanceDB."""
import os
import sys
import time
import bz2
import gzip
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NUM_THREADS = str(max(1, (os.cpu_count() or 4) - 2))
os.environ["OMP_NUM_THREADS"] = NUM_THREADS
os.environ["MKL_NUM_THREADS"] = NUM_THREADS
os.environ["NUMEXPR_NUM_THREADS"] = NUM_THREADS

try:
    import mwparserfromhell
except ImportError:
    mwparserfromhell = None

try:
    import fitz
except ImportError:
    fitz = None

from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_core.config import DELETE_SOURCE_AFTER_INDEX, INDEX_BATCH_SIZE
from rag_core.embeddings import embed_documents
from rag_core.indexing import maybe_delete_source, open_table
from rag_core.quality import is_indexable_content


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def wiki_to_text(wikitext):
    if mwparserfromhell:
        try:
            return mwparserfromhell.parse(wikitext).strip_code()
        except Exception:
            pass
    return wikitext


os.environ.setdefault("RAG_PREFER_LOCAL", "1")


def process_file_stream(file_path: Path):
    log(f"⚡ Ingestion: {file_path.name}")
    table = open_table()
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    raw_batch: list[dict] = []
    total_chunks = 0
    t0 = time.time()

    def flush_raw_batch():
        nonlocal raw_batch, total_chunks
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
            total_chunks += len(db_batch)
            elapsed = time.time() - t0
            cps = total_chunks / elapsed if elapsed else 0
            log(f"   ✓ {total_chunks} Chunks ({cps:.0f}/s)")
        else:
            log(f"⚠️ Ingestion: Embedding-Fehler bei Batch von {len(texts)} Chunks, versuche einzeln...")
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
                total_chunks += len(db_batch)
                elapsed = time.time() - t0
                cps = total_chunks / elapsed if elapsed else 0
                log(f"   ✓ {total_chunks} Chunks ({cps:.0f}/s)")
        raw_batch.clear()

    def add_chunks(chunks, source_label):
        if not chunks:
            return
        ts = datetime.now().isoformat()
        for chunk in chunks:
            raw_batch.append({
                "text": chunk,
                "source": source_label,
                "timestamp": ts,
            })

    if str(file_path).endswith((".xml.bz2", ".xml.gz")):
        opener = bz2.open if str(file_path).endswith(".bz2") else gzip.open
        with opener(file_path, "rt", encoding="utf-8", errors="replace") as f:
            in_page = False
            title, text = "", ""
            root = None
            for event, elem in ET.iterparse(f, events=("start", "end")):
                if root is None and event == "start":
                    root = elem
                tag = elem.tag.split("}")[-1]
                if event == "start" and tag == "page":
                    in_page, title, text = True, "", ""
                elif event == "end":
                    if tag == "title" and in_page:
                        title = elem.text or ""
                    elif tag == "text" and in_page:
                        text = elem.text or ""
                    elif tag == "page":
                        in_page = False
                        if text and len(text) > 100 and not text.startswith("#REDIRECT"):
                            clean = wiki_to_text(text)
                            src = f"wiki:{title}"
                            if is_indexable_content(clean, src):
                                chunks = splitter.split_text(f"# {title}\n\n{clean}")
                                add_chunks(chunks, src)
                                if len(raw_batch) >= INDEX_BATCH_SIZE:
                                    flush_raw_batch()
                        
                        # Aggressive Memory Cleanup
                        elem.clear()
                        if root is not None:
                            root.clear()
        flush_raw_batch()

    elif str(file_path).endswith(".pdf") and fitz:
        try:
            doc = fitz.open(str(file_path))
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
            src = str(file_path)
            if is_indexable_content(text, src):
                chunks = splitter.split_text(f"# {file_path.stem}\n\n{text}")
                add_chunks(chunks, src)
                flush_raw_batch()
        except Exception as e:
            log(f"⚠️ PDF: {e}")

    elif file_path.suffix.lower() in (".txt", ".md", ".rst"):
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            src = str(file_path)
            if is_indexable_content(text, src):
                chunks = splitter.split_text(text)
                add_chunks(chunks, src)
                flush_raw_batch()
        except Exception as e:
            log(f"⚠️ Text: {e}")

    log(f"✅ Fertig: {total_chunks} Chunks")
    if DELETE_SOURCE_AFTER_INDEX:
        maybe_delete_source(file_path)
        log(f"🗑️ Rohdatei entfernt: {file_path.name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ingest_single.py <file_path>")
        sys.exit(1)
    target = Path(sys.argv[1]).resolve()
    if target.exists():
        process_file_stream(target)
    else:
        print(f"Nicht gefunden: {target}")
        sys.exit(1)
