"""Gemeinsame Indizierungslogik."""
from __future__ import annotations

import gc

from datetime import datetime
from pathlib import Path

import lancedb
import pyarrow as pa
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .collector_plan import PRIME_EXCLUDE_PREFIXES, PRIME_SOURCE_ROOTS
from .config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CODE_EXTENSIONS,
    DATA_DIR,
    DELETE_SOURCE_AFTER_INDEX,
    INDEX_BATCH_SIZE,
    INDEX_FILE_LIMIT,
    LANCE_DB_PATH,
    MAX_USER_FILE_BYTES,
    SKIP_DIRS,
    TABLE_NAME,
    TABLE_NAME_PRIME,
    TEXT_EXTENSIONS,
    USER_SKIP_DIRS,
    USER_WORKSPACE_ROOTS,
    VECTOR_DIM,
)
from .embeddings import embed_documents
from .gui_resources import wait_for_ram, dynamic_batch_size
from .quality import is_indexable_content  # noqa: F401 — used by file_to_records


def get_schema() -> pa.Schema:
    return pa.schema([
        pa.field("vector", pa.list_(pa.float32(), VECTOR_DIM)),
        pa.field("text", pa.string()),
        pa.field("source", pa.string()),
        pa.field("timestamp", pa.string()),
    ])


def open_table(create: bool = True):
    db = lancedb.connect(str(LANCE_DB_PATH))
    if not create:
        raw = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
        tables = raw.tables if hasattr(raw, "tables") else raw
        if TABLE_NAME not in tables:
            return None
        return db.open_table(TABLE_NAME)
    
    try:
        return db.create_table(TABLE_NAME, schema=get_schema(), exist_ok=True)
    except Exception as e:
        if "Invalid range" in str(e) or "lance error" in str(e).lower():
            import shutil
            print(f"⚠️ Warnung: Tabelle '{TABLE_NAME}' beschädigt, wird neu erstellt (Fehler: {e})")
            try:
                db.drop_table(TABLE_NAME)
            except Exception:
                pass
            
            # Falls db.drop_table fehlschlägt, löschen wir das Verzeichnis manuell
            table_dir = LANCE_DB_PATH / f"{TABLE_NAME}.lance"
            if table_dir.exists():
                shutil.rmtree(table_dir, ignore_errors=True)
                
            return db.create_table(TABLE_NAME, schema=get_schema(), exist_ok=True)
        raise


def make_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def iter_indexable_files(data_dir: Path | None = None, *, priority_only: bool = False):
    root = data_dir or DATA_DIR
    count = 0
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        if suffix not in TEXT_EXTENSIONS and suffix != ".pdf":
            continue
        if any(skip in f.parts for skip in SKIP_DIRS):
            continue
        rel = f.relative_to(root).as_posix()
        if any(rel.startswith(ex) for ex in PRIME_EXCLUDE_PREFIXES):
            continue
        if priority_only:
            if not any(rel.startswith(pr.strip()) for pr in PRIME_SOURCE_ROOTS if pr.strip()):
                continue
        yield f
        count += 1
        if INDEX_FILE_LIMIT and count >= INDEX_FILE_LIMIT:
            break


def open_prime_table(recreate: bool = False):
    db = lancedb.connect(str(LANCE_DB_PATH))
    tables = db.list_tables()
    names = tables.tables if hasattr(tables, "tables") else tables
    
    try:
        if recreate and TABLE_NAME in names:
            try:
                db.drop_table(TABLE_NAME)
            except Exception:
                pass
        return db.create_table(TABLE_NAME, schema=get_schema(), exist_ok=True)
    except Exception as e:
        if "Invalid range" in str(e) or "lance error" in str(e).lower():
            import shutil
            print(f"⚠️ Warnung: Tabelle '{TABLE_NAME}' beschädigt, wird neu erstellt (Fehler: {e})")
            try:
                db.drop_table(TABLE_NAME)
            except Exception:
                pass
            
            table_dir = LANCE_DB_PATH / f"{TABLE_NAME}.lance"
            if table_dir.exists():
                shutil.rmtree(table_dir, ignore_errors=True)
                
            return db.create_table(TABLE_NAME, schema=get_schema(), exist_ok=True)
        raise


def file_to_records(file_path: Path, splitter: RecursiveCharacterTextSplitter) -> list[dict]:
    wait_for_ram(floor_mb=500)
    try:
        if file_path.suffix.lower() == ".pdf":
            try:
                import fitz
                doc = fitz.open(str(file_path))
                text = "\n".join(page.get_text() for page in doc)
                doc.close()
            except Exception:
                return []
        else:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if not is_indexable_content(text, file_path):
        return []
    chunks = splitter.split_text(text)
    if not chunks:
        return []
    vectors = embed_documents(chunks)
    if not vectors or len(vectors) != len(chunks):
        return []
    ts = datetime.now().isoformat()
    src = str(file_path)
    records = [
        {
            "vector": vectors[i],
            "text": chunks[i],
            "source": src,
            "timestamp": ts,
        }
        for i in range(len(chunks))
    ]
    del vectors, chunks, text
    gc.collect()
    return records


def files_to_records_batched(files: list[Path], splitter: RecursiveCharacterTextSplitter) -> list[dict]:
    raw_chunks: list[dict] = []
    for file_path in files:
        wait_for_ram(floor_mb=400)
        try:
            if file_path.suffix.lower() == ".pdf":
                try:
                    import fitz
                    doc = fitz.open(str(file_path))
                    text = "\n".join(page.get_text() for page in doc)
                    doc.close()
                except Exception:
                    continue
            else:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not is_indexable_content(text, file_path):
            continue
        chunks = splitter.split_text(text)
        if not chunks:
            continue
        ts = datetime.now().isoformat()
        src = str(file_path)
        for chunk in chunks:
            raw_chunks.append({
                "text": chunk,
                "source": src,
                "timestamp": ts,
            })
        del text, chunks

    if not raw_chunks:
        return []

    texts = [c["text"] for c in raw_chunks]
    vectors = embed_documents(texts)
    gc.collect()
    if vectors and len(vectors) == len(texts):
        result = [
            {
                "vector": vectors[i],
                "text": raw_chunks[i]["text"],
                "source": raw_chunks[i]["source"],
                "timestamp": raw_chunks[i]["timestamp"],
            }
            for i in range(len(raw_chunks))
        ]
        del vectors, texts, raw_chunks
        gc.collect()
        return result
    else:
        db_batch = []
        for item in raw_chunks:
            v = embed_documents([item["text"]])
            if v and len(v) == 1:
                db_batch.append({
                    "vector": v[0],
                    "text": item["text"],
                    "source": item["source"],
                    "timestamp": item["timestamp"],
                })
        del raw_chunks, texts
        gc.collect()
        return db_batch


def iter_user_project_files():
    """Durchsucht deine Projektordner für persönliches Wissen."""
    exts = TEXT_EXTENSIONS | CODE_EXTENSIONS
    count = 0
    for root in USER_WORKSPACE_ROOTS:
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in exts:
                continue
            if any(skip in f.parts for skip in USER_SKIP_DIRS):
                continue
            if f.stat().st_size > MAX_USER_FILE_BYTES:
                continue
            if f.name.startswith(".") and f.suffix not in CODE_EXTENSIONS:
                continue
            yield f
            count += 1
            if INDEX_FILE_LIMIT and count >= INDEX_FILE_LIMIT:
                return


def user_source_label(file_path: Path) -> str:
    for root in USER_WORKSPACE_ROOTS:
        try:
            rel = file_path.resolve().relative_to(root.resolve())
            return f"user:{root.name}/{rel.as_posix()}"
        except ValueError:
            continue
    return f"user:{file_path.name}"


def file_to_user_records(file_path: Path, splitter: RecursiveCharacterTextSplitter) -> list[dict]:
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if len(text.strip()) < 30:
        return []
    src = user_source_label(file_path)
    if not is_indexable_content(text, src):
        return []
    chunks = splitter.split_text(text)
    vectors = embed_documents(chunks)
    if not vectors or len(vectors) != len(chunks):
        return []
    ts = datetime.now().isoformat()
    return [
        {"vector": vectors[i], "text": chunks[i], "source": src, "timestamp": ts}
        for i in range(len(chunks))
    ]


def user_files_to_records_batched(files: list[Path], splitter: RecursiveCharacterTextSplitter) -> list[dict]:
    raw_chunks: list[dict] = []
    for file_path in files:
        wait_for_ram(floor_mb=400)
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(text.strip()) < 30:
            continue
        src = user_source_label(file_path)
        if not is_indexable_content(text, src):
            continue
        chunks = splitter.split_text(text)
        if not chunks:
            continue
        ts = datetime.now().isoformat()
        for chunk in chunks:
            raw_chunks.append({
                "text": chunk,
                "source": src,
                "timestamp": ts,
            })
        del text, chunks

    if not raw_chunks:
        return []

    texts = [c["text"] for c in raw_chunks]
    vectors = embed_documents(texts)
    gc.collect()
    if vectors and len(vectors) == len(texts):
        result = [
            {
                "vector": vectors[i],
                "text": raw_chunks[i]["text"],
                "source": raw_chunks[i]["source"],
                "timestamp": raw_chunks[i]["timestamp"],
            }
            for i in range(len(raw_chunks))
        ]
        del vectors, texts, raw_chunks
        gc.collect()
        return result
    else:
        db_batch = []
        for item in raw_chunks:
            v = embed_documents([item["text"]])
            if v and len(v) == 1:
                db_batch.append({
                    "vector": v[0],
                    "text": item["text"],
                    "source": item["source"],
                    "timestamp": item["timestamp"],
                })
        del raw_chunks, texts
        gc.collect()
        return db_batch


def maybe_delete_source(path: Path) -> None:
    if DELETE_SOURCE_AFTER_INDEX:
        try:
            path.unlink(missing_ok=True)
            print(f"🗑️ Quelldatei erfolgreich gelöscht: {path.name}")
        except Exception as e:
            print(f"⚠️ Fehler beim Löschen der Quelldatei {path.name}: {e}")
