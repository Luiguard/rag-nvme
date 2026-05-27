"""Machine Core: NVMe-basierter Wissenskern.

Denkt in Blöcken, Zustandsvektoren, Adressen.
Menschliche Sprache wird nur am Ein-/Ausgang übersetzt (Adapter).

Kernprinzip: O(1) RAM, O(N) NVMe-Zugriff.
"""
from __future__ import annotations

import json
import logging
import re
import time
import traceback
from pathlib import Path
from typing import Any

log = logging.getLogger("machine_core")

from .nvme_blocks import (
    NVMeBlockStore,
    TYPE_ARTICLE,
    TYPE_EVENT,
    TYPE_FACT,
)


class MachineCore:
    """Storage-zentrische KI: NVMe als primärer Intelligenzträger."""

    def __init__(self, store_path: str | Path | None = None):
        from .config import NVME_KNOWLEDGE_PATH
        self.store_path = Path(store_path) if store_path else NVME_KNOWLEDGE_PATH
        self._store: NVMeBlockStore | None = None
        log.info(f"MachineCore init: store_path={self.store_path}")

    @property
    def store(self) -> NVMeBlockStore:
        if self._store is None:
            if self.store_path.exists():
                log.info(f"Lade NVMe-Store: {self.store_path} "
                         f"({self.store_path.stat().st_size / (1024*1024):.1f} MB)")
                try:
                    self._store = NVMeBlockStore(self.store_path, readonly=True)
                    log.info(f"Store geladen: {self._store.index.total_blocks:,} Blöcke")
                except Exception as e:
                    log.error(f"Store laden fehlgeschlagen: {self.store_path}: {e}\n"
                              f"{traceback.format_exc()}")
                    raise
            else:
                log.warning(f"NVMe-Store nicht gefunden: {self.store_path}")
                raise FileNotFoundError(f"NVMe store not found: {self.store_path}")
        return self._store

    @property
    def ready(self) -> bool:
        try:
            return self.store.index.total_blocks > 0
        except Exception as e:
            log.debug(f"ready-Check fehlgeschlagen: {e}")
            return False

    def stats(self) -> dict:
        try:
            return self.store.stats()
        except Exception as e:
            log.warning(f"stats() fehlgeschlagen: {e}")
            return {"total_blocks": 0, "error": str(e)}

    # ─── Anfrage-Parser ──────────────────────────────────────────────

    def parse_query(self, human_query: str) -> dict:
        """Parse human query into machine-internal request structure."""
        q = human_query.strip().lower()
        request = {
            "raw": human_query,
            "type": "search",
            "terms": [],
            "years": [],
            "topics": [],
        }

        # Year extraction
        year_matches = re.findall(r"\b(1[0-9]{3}|20[0-2][0-9])\b", human_query)
        if year_matches:
            request["years"] = [int(y) for y in year_matches]
            request["type"] = "time_query"

        # Topic detection
        topic_keywords = {
            "politik": "politics", "political": "politics", "regierung": "politics",
            "wissenschaft": "science", "science": "science", "forschung": "science",
            "technik": "technology", "technology": "technology", "computer": "technology",
            "kultur": "culture", "music": "culture", "kunst": "culture", "film": "culture",
            "sport": "sports", "olympi": "sports", "fußball": "sports",
            "krieg": "war", "war": "war", "militär": "war", "schlacht": "war",
            "wirtschaft": "economy", "economy": "economy",
        }
        for keyword, topic in topic_keywords.items():
            if keyword in q:
                if topic not in request["topics"]:
                    request["topics"].append(topic)

        # Search terms (remove stop words)
        stop_words = {
            "was", "ist", "sind", "wie", "wo", "wer", "welche", "der", "die", "das",
            "ein", "eine", "und", "oder", "für", "von", "zu", "mit", "in", "auf",
            "an", "bei", "nach", "über", "unter", "vor", "alles", "passiert",
            "what", "is", "are", "the", "a", "an", "happened", "about", "tell",
        }
        words = re.findall(r"\b\w{3,}\b", q)
        request["terms"] = [w for w in words if w not in stop_words]

        return request

    # ─── Kern-Abfragen ───────────────────────────────────────────────

    def query_by_year(self, year: int, limit: int = 100) -> list[dict]:
        """Get all knowledge blocks for a specific year."""
        results = []

        event_blocks = self.store.read_by_year(year, limit=limit)
        for hdr, data in event_blocks:
            try:
                parsed = json.loads(data)
                parsed["_block_type"] = hdr.block_type
                parsed["_offset"] = hdr.offset
                results.append(parsed)
            except json.JSONDecodeError as e:
                log.warning(f"JSON-Decode fehlgeschlagen bei Year-Block offset={hdr.offset}, "
                            f"year={year}: {e}, data_preview={data[:100]}")
            except Exception as e:
                log.error(f"Unerwarteter Fehler bei Year-Block offset={hdr.offset}: {e}")

        year_str = str(year)
        article_hits = self.store.search_text(year_str, block_type=TYPE_ARTICLE, limit=50)
        for hdr, data, score in article_hits:
            try:
                parsed = json.loads(data)
                parsed["_block_type"] = hdr.block_type
                parsed["_score"] = score
                results.append(parsed)
            except json.JSONDecodeError as e:
                log.warning(f"JSON-Decode fehlgeschlagen bei Artikel offset={hdr.offset}: {e}")
            except Exception as e:
                log.error(f"Unerwarteter Fehler bei Artikel offset={hdr.offset}: {e}")

        log.debug(f"query_by_year({year}): {len(results)} Ergebnisse")
        return results

    def query_by_terms(self, terms: list[str], limit: int = 50) -> list[dict]:
        """Search across all blocks for matching terms."""
        query = " ".join(terms)
        results = []

        hits = self.store.search_text(query, limit=limit)
        for hdr, data, score in hits:
            try:
                parsed = json.loads(data)
                parsed["_block_type"] = hdr.block_type
                parsed["_score"] = score
                results.append(parsed)
            except json.JSONDecodeError as e:
                log.warning(f"JSON-Decode fehlgeschlagen bei Term-Block offset={hdr.offset}: "
                            f"{e}, query='{query[:40]}'")
            except Exception as e:
                log.error(f"Unerwarteter Fehler bei Term-Block offset={hdr.offset}: {e}")

        log.debug(f"query_by_terms({query[:40]}): {len(results)} Ergebnisse")
        return results

    def query_by_key(self, key: str) -> list[dict]:
        """Direct key lookup."""
        results = []
        blocks = self.store.read_by_key(key)
        for hdr, data in blocks:
            try:
                parsed = json.loads(data)
                parsed["_block_type"] = hdr.block_type
                results.append(parsed)
            except json.JSONDecodeError as e:
                log.warning(f"JSON-Decode fehlgeschlagen bei Key-Block offset={hdr.offset}, "
                            f"key='{key[:40]}': {e}")
            except Exception as e:
                log.error(f"Unerwarteter Fehler bei Key-Block offset={hdr.offset}: {e}")
        return results

    # ─── Haupt-Interface ─────────────────────────────────────────────

    def process(self, human_query: str) -> dict:
        """Main entry point: human query → structured machine result."""
        t0 = time.time()
        log.info(f"process() Anfrage: '{human_query[:80]}'")

        try:
            request = self.parse_query(human_query)
        except Exception as e:
            log.error(f"parse_query fehlgeschlagen: '{human_query[:60]}': {e}\n"
                      f"{traceback.format_exc()}")
            return {"query": human_query, "error": str(e), "events": [],
                    "facts": [], "articles": [], "total_results": 0,
                    "elapsed_ms": 0, "store_stats": self.stats()}

        results = []

        if request["type"] == "time_query" and request["years"]:
            for year in request["years"]:
                try:
                    year_results = self.query_by_year(year, limit=100)
                    results.extend(year_results)
                except Exception as e:
                    log.error(f"query_by_year({year}) fehlgeschlagen: {e}\n"
                              f"{traceback.format_exc()}")

        if request["terms"]:
            try:
                term_results = self.query_by_terms(request["terms"], limit=50)
                results.extend(term_results)
            except Exception as e:
                log.error(f"query_by_terms({request['terms'][:5]}) fehlgeschlagen: {e}\n"
                          f"{traceback.format_exc()}")

        seen = set()
        unique = []
        for r in results:
            key = r.get("source", "") + ":" + r.get("description", r.get("text", ""))[:80]
            if key not in seen:
                seen.add(key)
                unique.append(r)

        events = [r for r in unique if r.get("_block_type") == TYPE_EVENT]
        facts = [r for r in unique if r.get("_block_type") == TYPE_FACT]
        articles = [r for r in unique if r.get("_block_type") == TYPE_ARTICLE]

        events.sort(key=lambda e: (e.get("year", 0), e.get("date", "")))

        elapsed = time.time() - t0
        log.info(f"process() fertig: {len(unique)} unique Ergebnisse "
                 f"({len(events)} events, {len(facts)} facts, {len(articles)} articles) "
                 f"in {elapsed*1000:.1f}ms")

        return {
            "query": human_query,
            "request": request,
            "events": events[:50],
            "facts": facts[:30],
            "articles": articles[:20],
            "total_results": len(unique),
            "elapsed_ms": round(elapsed * 1000, 1),
            "store_stats": self.stats(),
        }

    def close(self):
        if self._store:
            self._store.close()
            self._store = None
