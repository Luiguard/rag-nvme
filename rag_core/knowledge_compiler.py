"""Knowledge Compiler: Konvertiert LanceDB-Wissen in NVMe-Blöcke.

Designed für:
  - 10M bis 100M+ Chunks
  - Streaming-Migration (pausierbar, resumable)
  - Konstanter RAM (~200-500 MB)
  - Lance Scanner für batch-weise Iteration ohne Offset-Probleme
  - State-Persistence für Fortschritt

Migration Pipeline:
  LanceDB chunks → Artikel-Rekonstruktion → Komprimierte NVMe-Blöcke
  + Zeitachsen-Extraktion → EVENT-Blöcke
  + Fakten-Extraktion → FACT-Blöcke
"""
from __future__ import annotations

import gc
import json
import logging
import os
import re
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("knowledge_compiler")

from .nvme_blocks import (
    NVMeBlockStore,
    TYPE_ARTICLE,
    TYPE_EVENT,
    TYPE_FACT,
    TYPE_META,
)

_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\b")

_FACT_PATTERNS = [
    re.compile(r"^(.{3,60})\s+(?:ist|war|sind|waren|wird|wurde)\s+(.{5,200})", re.MULTILINE),
    re.compile(r"^(.{3,60})\s+(?:is|was|are|were)\s+(.{5,200})", re.MULTILINE),
]

_EVENT_PATTERNS = [
    re.compile(r"(?:am\s+)?(\d{1,2}\.\s*\w+\s+\d{4})\s*[:\-–]\s*(.{10,300})"),
    re.compile(r"(\d{4})\s*[:\-–]\s*(.{10,300})"),
    re.compile(r"(?:Im\s+(?:Jahr(?:e)?\s+)?|In\s+)(\d{4})\s+(.{10,200})"),
]

STATE_FILE_NAME = "nvme_migration_state.json"


class MigrationState:
    """Persistenter Fortschritt für Pause/Resume."""

    def __init__(self, state_path: Path):
        self.path = state_path
        self.data = {
            "status": "idle",
            "batches_processed": 0,
            "chunks_processed": 0,
            "articles_compiled": 0,
            "facts_extracted": 0,
            "events_extracted": 0,
            "years_seen": 0,
            "errors": 0,
            "processed_sources": [],
            "start_time": 0,
            "last_update": 0,
            "total_chunks": 0,
        }
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r") as f:
                    saved = json.load(f)
                self.data.update(saved)
                log.info(f"Migration-State geladen: {self.path}, "
                         f"status={saved.get('status')}, "
                         f"chunks={saved.get('chunks_processed', 0):,}, "
                         f"articles={saved.get('articles_compiled', 0):,}")
            except json.JSONDecodeError as e:
                log.error(f"Migration-State JSON defekt: {self.path}: {e}")
            except OSError as e:
                log.error(f"Migration-State Lesefehler: {self.path}: {e}")

    def save(self):
        self.data["last_update"] = time.time()
        try:
            with open(self.path, "w") as f:
                json.dump(self.data, f, indent=2)
        except OSError as e:
            log.error(f"Migration-State Speicherfehler: {self.path}: {e}\n"
                      f"{traceback.format_exc()}")
        except Exception as e:
            log.error(f"Migration-State unerwartet: {e}\n{traceback.format_exc()}")

    def is_source_done(self, source: str) -> bool:
        return source in self.data.get("processed_sources", [])

    def mark_source_done(self, source: str):
        if "processed_sources" not in self.data:
            self.data["processed_sources"] = []
        if source not in self.data["processed_sources"]:
            self.data["processed_sources"].append(source)

    @property
    def progress_pct(self) -> float:
        total = self.data.get("total_chunks", 0)
        if total == 0:
            return 0.0
        return min(100.0, (self.data["chunks_processed"] / total) * 100)


def reconstruct_articles_from_batch(batch_texts: list[str], batch_sources: list[str]) -> dict[str, str]:
    """Group chunks back into articles by source. Streaming-friendly."""
    articles = defaultdict(list)
    for text, src in zip(batch_texts, batch_sources):
        if text and text.strip():
            articles[src].append(text)

    result = {}
    for src, texts in articles.items():
        full_text = "\n".join(texts)
        if len(full_text.strip()) > 50:
            result[src] = full_text
    return result


def extract_years(text: str) -> set[int]:
    years = set()
    for match in _YEAR_RE.finditer(text):
        y = int(match.group(1))
        if 1000 <= y <= 2030:
            years.add(y)
    return years


def extract_facts(text: str, source: str) -> list[dict]:
    facts = []
    for pattern in _FACT_PATTERNS:
        for match in pattern.finditer(text[:5000]):
            subj = match.group(1).strip()
            obj = match.group(2).strip().rstrip(".")
            if len(subj) > 3 and len(obj) > 5 and "\n" not in subj:
                facts.append({
                    "subject": subj,
                    "predicate": "ist",
                    "object": obj,
                    "source": source,
                })
    return facts[:20]


def extract_events(text: str, source: str) -> list[dict]:
    # Filter: Life span patterns are NOT events
    _LIFESPAN_RE = re.compile(r"\(\s*\*?\s*\d{4}\s*[-–]\s*\d{4}\s*\)")
    _LIFESPAN_INLINE = re.compile(r"\d{4}\s*[-–]\s*\d{4}\s*\)")

    events = []
    seen_descs = set()

    # Only match standalone year patterns with actual event descriptions
    _clean_event_patterns = [
        re.compile(r"(?:^|\n)\s*(\d{1,2}\.\s*(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+\d{4})\s*[:\-–]\s*(.{15,300})", re.MULTILINE),
        re.compile(r"(?:^|\n)\s*(\d{4})\s*[:\-–]\s+([A-ZÄÖÜ].{15,300})", re.MULTILINE),
        re.compile(r"(?:Im\s+(?:Jahre?\s+)?)(1[0-9]{3}|20[0-2][0-9])\s+((?:wurde|fand|begann|endete|gründete|entstand|erfolgte|eröffnete|startete).{10,200})", re.IGNORECASE),
    ]

    for pattern in _clean_event_patterns:
        for match in pattern.finditer(text[:15000]):
            date_str = match.group(1).strip()
            desc = match.group(2).strip().rstrip(".")

            # Skip if description looks like a lifespan
            if _LIFESPAN_INLINE.search(desc[:60]):
                continue

            year = None
            y_match = re.search(r"\d{4}", date_str)
            if y_match:
                year = int(y_match.group())

            if not year or year < 1000 or year > 2030:
                continue
            if len(desc) < 15:
                continue

            # Dedup by description prefix
            desc_key = desc[:40].lower()
            if desc_key in seen_descs:
                continue
            seen_descs.add(desc_key)

            events.append({
                "date": date_str,
                "year": year,
                "description": desc[:300],
                "source": source,
            })
    return events[:30]


def compile_article_to_blocks(
    store: NVMeBlockStore,
    source: str,
    text: str,
) -> dict:
    stats = {"articles": 0, "facts": 0, "events": 0, "years": set()}

    try:
        article_data = json.dumps({
            "source": source,
            "text": text,
            "length": len(text),
        }, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as e:
        log.error(f"Artikel-Serialisierung fehlgeschlagen: source={source[:60]}, "
                  f"text_len={len(text)}: {e}")
        return stats

    years = extract_years(text)
    primary_year = min(years) if years else None

    try:
        store.store(source, article_data, block_type=TYPE_ARTICLE, year=primary_year)
        stats["articles"] = 1
        stats["years"] = years
    except Exception as e:
        log.error(f"Artikel-Block Speichern fehlgeschlagen: source={source[:60]}: {e}\n"
                  f"{traceback.format_exc()}")
        return stats

    try:
        facts = extract_facts(text, source)
        for fact in facts:
            fact_data = json.dumps(fact, ensure_ascii=False).encode("utf-8")
            fact_key = f"fact:{fact['subject']}:{fact['object'][:50]}"
            store.store(fact_key, fact_data, block_type=TYPE_FACT)
            stats["facts"] += 1
    except Exception as e:
        log.warning(f"Fakten-Extraktion fehlgeschlagen: source={source[:60]}: {e}")

    try:
        events = extract_events(text, source)
        for event in events:
            event_data = json.dumps(event, ensure_ascii=False).encode("utf-8")
            event_key = f"event:{event['year']}:{event['description'][:50]}"
            store.store(event_key, event_data, block_type=TYPE_EVENT, year=event["year"])
            stats["events"] += 1
    except Exception as e:
        log.warning(f"Event-Extraktion fehlgeschlagen: source={source[:60]}: {e}")

    return stats


def migrate_lancedb_to_nvme(
    store: NVMeBlockStore,
    *,
    table_name: str | None = None,
    lance_db_path: str | Path | None = None,
    batch_size: int = 50000,
    max_articles: int = 0,
    log_fn=None,
    stop_event=None,
) -> dict:
    """Migrate LanceDB → NVMe blocks.

    Designed for 10M-100M+ chunks:
    - Uses lance scanner (no offset pagination)
    - Streams batches without loading all into RAM
    - Persistent state for pause/resume
    - stop_event: threading.Event to signal graceful stop
    """
    _log = log_fn or print

    from .config import LANCE_DB_PATH, TABLE_NAME, BASE_DIR
    db_path = Path(lance_db_path) if lance_db_path else LANCE_DB_PATH
    tbl_name = table_name or TABLE_NAME

    log.info(f"Migration gestartet: db={db_path}, table={tbl_name}, batch_size={batch_size}")

    try:
        import lancedb
        db = lancedb.connect(str(db_path))
    except Exception as e:
        _log(f"[MIGRATE] FEHLER: LanceDB Verbindung fehlgeschlagen: {db_path}: {e}")
        log.error(f"LanceDB connect fehlgeschlagen: {db_path}: {e}\n{traceback.format_exc()}")
        return {"error": str(e)}

    try:
        tbl = db.open_table(tbl_name)
    except Exception as e:
        _log(f"[MIGRATE] FEHLER: Tabelle '{tbl_name}' nicht gefunden in {db_path}")
        log.error(f"Tabelle nicht gefunden: '{tbl_name}' in {db_path}: {e}\n"
                  f"Verfügbare Tabellen: {db.table_names() if hasattr(db, 'table_names') else 'unbekannt'}")
        return {"error": str(e)}

    try:
        total_rows = tbl.count_rows()
    except Exception as e:
        _log(f"[MIGRATE] FEHLER: count_rows fehlgeschlagen: {e}")
        log.error(f"count_rows fehlgeschlagen für '{tbl_name}': {e}")
        return {"error": str(e)}

    state = MigrationState(BASE_DIR / STATE_FILE_NAME)
    state.data["total_chunks"] = total_rows
    state.data["status"] = "running"
    state.data["start_time"] = state.data.get("start_time") or time.time()
    state.save()

    _log(f"[MIGRATE] LanceDB '{tbl_name}': {total_rows:,} chunks")
    _log(f"[MIGRATE] Already processed: {state.data['chunks_processed']:,} chunks, "
        f"{state.data['articles_compiled']:,} articles")

    try:
        import lance
        ds = tbl.to_lance()
        scanner = ds.scanner(columns=["text", "source"], batch_size=batch_size)
        use_scanner = True
        _log("[MIGRATE] Using lance scanner (optimal)")
        log.info(f"Lance scanner initialisiert, batch_size={batch_size}")
    except ImportError:
        use_scanner = False
        _log("[MIGRATE] Fallback: search-based batching (lance nicht installiert)")
        log.warning("lance nicht installiert – Fallback auf search-basierte Iteration. "
                    "Installation empfohlen: pip install pylance")
    except Exception as e:
        use_scanner = False
        _log(f"[MIGRATE] Fallback: search-based batching ({e})")
        log.warning(f"Lance scanner fehlgeschlagen, Fallback: {e}\n{traceback.format_exc()}")

    article_buffer: dict[str, list[str]] = defaultdict(list)
    article_count = 0
    batch_count = 0
    chunks_in_buffer = 0

    def flush_buffer():
        nonlocal article_count
        for source, texts in article_buffer.items():
            if state.is_source_done(source):
                continue

            full_text = "\n".join(texts)
            if len(full_text.strip()) < 50:
                continue

            try:
                stats = compile_article_to_blocks(store, source, full_text)
                state.data["articles_compiled"] += stats["articles"]
                state.data["facts_extracted"] += stats["facts"]
                state.data["events_extracted"] += stats["events"]
                state.data["years_seen"] += len(stats["years"])
                state.mark_source_done(source)
                article_count += 1
            except Exception as e:
                state.data["errors"] += 1
                log.error(f"compile_article fehlgeschlagen: source={source[:60]}: {e}\n"
                          f"{traceback.format_exc()}")
                if state.data["errors"] <= 20:
                    _log(f"[MIGRATE] Fehler bei '{source[:50]}': {e}")

            if max_articles and article_count >= max_articles:
                break

        article_buffer.clear()

    if use_scanner:
        for arrow_batch in scanner.to_batches():
            if stop_event and stop_event.is_set():
                _log("[MIGRATE] Stop signal received — saving state")
                log.info("Stop-Signal empfangen, speichere State")
                break

            try:
                texts = arrow_batch.column("text").to_pylist()
                sources = arrow_batch.column("source").to_pylist()
            except Exception as e:
                log.error(f"Arrow-Batch Spalten-Zugriff fehlgeschlagen: {e}\n"
                          f"Batch-Schema: {arrow_batch.schema}\n{traceback.format_exc()}")
                _log(f"[MIGRATE] Batch-Fehler: {e}")
                state.data["errors"] += 1
                continue

            for text, src in zip(texts, sources):
                if text and text.strip():
                    article_buffer[src].append(text)
                    chunks_in_buffer += 1

            batch_count += 1
            state.data["chunks_processed"] += len(texts)
            state.data["batches_processed"] = batch_count

            # Flush when buffer gets large (memory control)
            if chunks_in_buffer >= batch_size:
                flush_buffer()
                chunks_in_buffer = 0
                gc.collect()

                state.save()
                elapsed = time.time() - state.data["start_time"]
                rate = state.data["chunks_processed"] / max(elapsed, 1)
                eta = (total_rows - state.data["chunks_processed"]) / max(rate, 1)
                _log(f"[MIGRATE] {state.data['chunks_processed']:,}/{total_rows:,} chunks "
                    f"({state.progress_pct:.1f}%) | "
                    f"{state.data['articles_compiled']:,} articles | "
                    f"ETA: {eta/60:.0f} min")
                if state.data["errors"] > 0:
                    _log(f"  ⚠️ {state.data['errors']} Fehler bisher")

            if max_articles and article_count >= max_articles:
                break
    else:
        # Fallback: search-based batching (less efficient)
        offset = state.data.get("chunks_processed", 0)
        while offset < total_rows:
            if stop_event and stop_event.is_set():
                log("[MIGRATE] Stop signal received — saving state")
                break

            try:
                batch = tbl.search().select(["text", "source"]).limit(batch_size).to_list()
            except Exception as e:
                log.warning(f"Fallback search mit select fehlgeschlagen: {e}")
                try:
                    batch = tbl.search().limit(batch_size).to_list()
                except Exception as e2:
                    log.error(f"Fallback search komplett fehlgeschlagen: {e2}\n"
                              f"{traceback.format_exc()}")
                    _log(f"[MIGRATE] FEHLER: Batch lesen fehlgeschlagen: {e2}")
                    break

            if not batch:
                break

            for row in batch:
                text = row.get("text", "")
                src = row.get("source", "")
                if text and text.strip():
                    article_buffer[src].append(text)
                    chunks_in_buffer += 1

            batch_count += 1
            state.data["chunks_processed"] += len(batch)

            if chunks_in_buffer >= batch_size:
                flush_buffer()
                chunks_in_buffer = 0
                gc.collect()
                state.save()

            offset += batch_size
            if max_articles and article_count >= max_articles:
                break

    flush_buffer()
    gc.collect()

    try:
        meta = {
            "migrated_at": time.time(),
            "source": f"lancedb:{tbl_name}",
            "total_chunks": state.data["chunks_processed"],
            "articles": state.data["articles_compiled"],
            "facts": state.data["facts_extracted"],
            "events": state.data["events_extracted"],
            "errors": state.data["errors"],
        }
        store.store("__meta__", json.dumps(meta, ensure_ascii=False).encode("utf-8"),
                    block_type=TYPE_META)
    except Exception as e:
        log.error(f"Meta-Block Speichern fehlgeschlagen: {e}")

    final_status = "completed" if not (stop_event and stop_event.is_set()) else "paused"
    state.data["status"] = final_status
    state.save()

    result = {k: v for k, v in state.data.items() if k != "processed_sources"}
    _log(f"[MIGRATE] {'Done' if final_status == 'completed' else 'Paused'}: "
        f"{json.dumps(result, indent=2)}")
    log.info(f"Migration {final_status}: {state.data['articles_compiled']:,} Artikel, "
             f"{state.data['facts_extracted']:,} Fakten, "
             f"{state.data['events_extracted']:,} Events, "
             f"{state.data['errors']} Fehler")
    return result


def run_migration_cli():
    """CLI entry point: python -m rag_core.knowledge_compiler [options]"""
    import argparse
    parser = argparse.ArgumentParser(description="Migrate LanceDB → NVMe blocks")
    parser.add_argument("--table", default=None, help="LanceDB table name")
    parser.add_argument("--db-path", default=None, help="LanceDB directory path")
    parser.add_argument("--output", default=None, help="NVMe store file path")
    parser.add_argument("--batch-size", type=int, default=50000, help="Chunks per batch")
    parser.add_argument("--max-articles", type=int, default=0, help="Limit articles (0=all)")
    parser.add_argument("--resume", action="store_true", help="Resume previous migration")
    args = parser.parse_args()

    from .config import NVME_KNOWLEDGE_PATH
    store_path = Path(args.output) if args.output else NVME_KNOWLEDGE_PATH

    if not args.resume and store_path.exists():
        print(f"⚠️  Store exists: {store_path} ({store_path.stat().st_size / 1024 / 1024:.1f} MB)")
        print("    Use --resume to continue, or delete the file to start fresh.")
        return

    store = NVMeBlockStore(store_path)
    try:
        migrate_lancedb_to_nvme(
            store,
            table_name=args.table,
            lance_db_path=args.db_path,
            batch_size=args.batch_size,
            max_articles=args.max_articles,
        )
    finally:
        print(f"\nStore stats: {json.dumps(store.stats(), indent=2)}")
        store.close()


if __name__ == "__main__":
    run_migration_cli()
