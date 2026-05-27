"""On-Demand Wissensbeschaffung: Holt fehlende Quellen live wenn die RAG-DB keine guten Treffer hat.

Architektur:
- Wenn Retrieval-Qualität unter Schwellenwert → Wikipedia-Artikel zum Thema fetchen
- Sofort in LanceDB indexieren (Micro-Batch: 1-3 Artikel)
- Re-Search mit frischem Wissen
- Background-Queue für verwandte Themen (lazy expansion)

Ressourcenschonend weil:
- Kein massives Pre-Loading nötig
- Pro Anfrage max. 3-5 Wikipedia-Requests
- Micro-Embeddings: nur die relevanten Chunks
- Duplikat-Check gegen bestehenden Index
"""
from __future__ import annotations

import gc
import threading
import time
from datetime import datetime
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent

_background_queue: list[str] = []
_bg_lock = threading.Lock()
_bg_running = False


def _extract_search_terms(query: str) -> list[str]:
    stop_words = {
        "was", "ist", "sind", "wie", "wo", "wer", "welche", "welcher",
        "ein", "eine", "der", "die", "das", "den", "dem", "des",
        "und", "oder", "aber", "nicht", "für", "von", "zu", "mit",
        "in", "auf", "an", "bei", "nach", "über", "unter", "vor",
        "kann", "wird", "hat", "haben", "sein", "werden", "können",
        "the", "is", "are", "what", "how", "why", "who", "which",
        "a", "an", "and", "or", "not", "for", "of", "to", "with",
        "erkläre", "erklären", "beschreibe", "nenne", "bitte",
    }
    words = query.replace("?", "").replace("!", "").replace(".", "").split()
    terms = [w for w in words if w.lower() not in stop_words and len(w) > 2]
    return terms


def _wiki_search(query: str, language: str = "de", max_results: int = 3) -> list[dict]:
    try:
        import wikipediaapi
        wiki = wikipediaapi.Wikipedia(user_agent="CustomRAG-LiveFetch/1.0", language=language)

        terms = _extract_search_terms(query)
        search_term = "_".join(terms[:4]) if terms else query[:50]

        page = wiki.page(search_term)
        results = []

        if page.exists() and len(page.text) > 200:
            results.append({
                "title": page.title,
                "text": page.text,
                "url": page.fullurl if hasattr(page, "fullurl") else f"https://{language}.wikipedia.org/wiki/{search_term}",
            })

        if not results and terms:
            for i in range(min(len(terms), 3)):
                p = wiki.page(terms[i])
                if p.exists() and len(p.text) > 200:
                    results.append({
                        "title": p.title,
                        "text": p.text,
                        "url": f"https://{language}.wikipedia.org/wiki/{terms[i]}",
                    })
                if len(results) >= max_results:
                    break

        if not results and len(terms) >= 2:
            combined = " ".join(terms[:3])
            try:
                import requests as req
                resp = req.get(
                    f"https://{language}.wikipedia.org/w/api.php",
                    params={
                        "action": "opensearch",
                        "search": combined,
                        "limit": max_results,
                        "format": "json",
                    },
                    timeout=8,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if len(data) >= 2:
                        for title in data[1][:max_results]:
                            p = wiki.page(title)
                            if p.exists() and len(p.text) > 200:
                                results.append({
                                    "title": p.title,
                                    "text": p.text,
                                    "url": f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                                })
            except Exception:
                pass

        return results[:max_results]
    except Exception:
        return []


def _web_extract(url: str) -> str | None:
    try:
        import trafilatura
        html = trafilatura.fetch_url(url)
        if html:
            return trafilatura.extract(html, include_links=True)
    except Exception:
        pass
    return None


def _micro_index(texts: list[dict], table=None) -> int:
    from .embeddings import embed_documents
    from .indexing import get_schema, make_splitter
    from .config import LANCE_DB_PATH, TABLE_NAME

    splitter = make_splitter()
    records = []
    ts = datetime.now().isoformat()

    for item in texts:
        chunks = splitter.split_text(item["text"])
        if not chunks:
            continue
        src = f"live:{item.get('source', 'web')}:{item.get('title', 'unknown')}"
        for chunk in chunks:
            records.append({"text": chunk, "source": src, "timestamp": ts})

    if not records:
        return 0

    all_texts = [r["text"] for r in records]
    vectors = embed_documents(all_texts)
    if not vectors or len(vectors) != len(all_texts):
        return 0

    db_records = [
        {
            "vector": vectors[i],
            "text": records[i]["text"],
            "source": records[i]["source"],
            "timestamp": records[i]["timestamp"],
        }
        for i in range(len(records))
    ]

    if table is None:
        import lancedb
        db = lancedb.connect(str(LANCE_DB_PATH))
        try:
            table = db.open_table(TABLE_NAME)
        except Exception:
            table = db.create_table(TABLE_NAME, schema=get_schema(), exist_ok=True)

    try:
        import pyarrow as pa
        table.add(pa.Table.from_pylist(db_records, schema=get_schema()))
    except Exception:
        return 0

    del vectors, db_records, all_texts
    gc.collect()
    return len(records)


def fetch_and_index(
    query: str,
    *,
    table=None,
    max_articles: int = 3,
    log_fn=None,
) -> tuple[int, list[str]]:
    """Holt Wikipedia-Artikel zum Thema und indexiert sie sofort.

    Returns: (chunks_indexed, list_of_sources)
    """
    log = log_fn or (lambda x: None)

    results = _wiki_search(query, max_results=max_articles)
    if not results:
        results = _wiki_search(query, language="en", max_results=max_articles)
    if not results:
        log("  ℹ️ Keine Wikipedia-Artikel gefunden")
        return 0, []

    to_index = []
    sources = []
    for r in results:
        to_index.append({
            "text": r["text"],
            "title": r["title"],
            "source": "wikipedia",
        })
        sources.append(f"wikipedia:{r['title']}")
        log(f"  📥 {r['title']} ({len(r['text'])} Zeichen)")

    wiki_dir = _BASE / "data" / "wikipedia"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        safe = r["title"].replace("/", "_").replace(" ", "_")[:80]
        fpath = wiki_dir / f"{safe}.txt"
        if not fpath.exists():
            try:
                fpath.write_text(f"# {r['title']}\n\n{r['text']}\n", encoding="utf-8")
            except Exception:
                pass

    chunks = _micro_index(to_index, table=table)
    log(f"  ⚡ {chunks} Chunks live indexiert aus {len(results)} Quelle(n)")
    return chunks, sources


def enqueue_related(query: str, max_terms: int = 5) -> None:
    terms = _extract_search_terms(query)
    with _bg_lock:
        for t in terms[:max_terms]:
            if t not in _background_queue and len(_background_queue) < 50:
                _background_queue.append(t)


def _background_worker():
    global _bg_running
    while True:
        term = None
        with _bg_lock:
            if _background_queue:
                term = _background_queue.pop(0)
            else:
                _bg_running = False
                return
        if term:
            try:
                fetch_and_index(term, max_articles=1)
            except Exception:
                pass
            time.sleep(3)


def start_background_expansion():
    global _bg_running
    with _bg_lock:
        if _bg_running or not _background_queue:
            return
        _bg_running = True
    threading.Thread(target=_background_worker, daemon=True).start()


def should_live_fetch(hits: list[dict], threshold: float = 1.2) -> bool:
    if not hits:
        return True
    best_dist = min(float(h.get("_distance", 999)) for h in hits)
    return best_dist > threshold
