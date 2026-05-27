"""Zweistufiger semantischer Query-Cache.

L1: Exakter String-Match → O(1) dict lookup
L2: Embedding-Cosine-Similarity → semantisch ähnliche Queries treffen Cache

Enterprise-Kernstück: Bei repetitiven Konzern-Anfragen (Onboarding, Compliance,
IT-Support) werden 60-80% aller Queries aus dem Cache bedient.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class CacheEntry:
    query: str
    vector: np.ndarray
    result: Any
    created: float = field(default_factory=time.time)
    hits: int = 0


class QueryCache:
    """Thread-safe LRU-Cache mit semantischer Similarity-Suche."""

    def __init__(
        self,
        max_entries: int = 500,
        ttl_seconds: float = 600.0,
        similarity_threshold: float = 0.95,
    ):
        self._max = max_entries
        self._ttl = ttl_seconds
        self._threshold = similarity_threshold
        self._lock = threading.Lock()
        self._l1: OrderedDict[str, CacheEntry] = OrderedDict()
        self._vectors: np.ndarray | None = None
        self._entries_list: list[CacheEntry] = []
        self._dirty = True

        self.total_hits = 0
        self.total_misses = 0
        self.l1_hits = 0
        self.l2_hits = 0

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [k for k, e in self._l1.items() if now - e.created > self._ttl]
        for k in expired:
            del self._l1[k]
        if expired:
            self._dirty = True

    def _evict_lru(self) -> None:
        while len(self._l1) > self._max:
            self._l1.popitem(last=False)
            self._dirty = True

    def _rebuild_matrix(self) -> None:
        if not self._dirty:
            return
        self._entries_list = list(self._l1.values())
        if self._entries_list:
            self._vectors = np.vstack([e.vector for e in self._entries_list])
            norms = np.linalg.norm(self._vectors, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            self._vectors = self._vectors / norms
        else:
            self._vectors = None
        self._dirty = False

    def get(self, query: str, vector: list[float] | np.ndarray | None = None) -> Any | None:
        with self._lock:
            self._evict_expired()

            if query in self._l1:
                entry = self._l1[query]
                self._l1.move_to_end(query)
                entry.hits += 1
                self.total_hits += 1
                self.l1_hits += 1
                return entry.result

            if vector is None or len(self._l1) == 0:
                self.total_misses += 1
                return None

            self._rebuild_matrix()
            if self._vectors is None or len(self._entries_list) == 0:
                self.total_misses += 1
                return None

            q_vec = np.asarray(vector, dtype=np.float32)
            norm = np.linalg.norm(q_vec)
            if norm > 0:
                q_vec = q_vec / norm

            similarities = self._vectors @ q_vec
            best_idx = int(np.argmax(similarities))
            best_sim = float(similarities[best_idx])

            if best_sim >= self._threshold:
                entry = self._entries_list[best_idx]
                self._l1.move_to_end(entry.query)
                entry.hits += 1
                self.total_hits += 1
                self.l2_hits += 1
                return entry.result

            self.total_misses += 1
            return None

    def put(self, query: str, vector: list[float] | np.ndarray, result: Any) -> None:
        vec = np.asarray(vector, dtype=np.float32)
        entry = CacheEntry(query=query, vector=vec, result=result)

        with self._lock:
            if query in self._l1:
                self._l1[query] = entry
                self._l1.move_to_end(query)
            else:
                self._l1[query] = entry
                self._evict_lru()
            self._dirty = True

    def invalidate(self, query: str | None = None) -> None:
        with self._lock:
            if query is None:
                self._l1.clear()
            elif query in self._l1:
                del self._l1[query]
            self._dirty = True

    def stats(self) -> dict:
        with self._lock:
            total = self.total_hits + self.total_misses
            return {
                "entries": len(self._l1),
                "max_entries": self._max,
                "total_hits": self.total_hits,
                "total_misses": self.total_misses,
                "l1_hits": self.l1_hits,
                "l2_hits": self.l2_hits,
                "hit_rate": self.total_hits / total if total > 0 else 0.0,
                "ttl_seconds": self._ttl,
                "similarity_threshold": self._threshold,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._l1)


_search_cache: QueryCache | None = None
_chat_cache: QueryCache | None = None


def get_search_cache() -> QueryCache:
    global _search_cache
    if _search_cache is None:
        _search_cache = QueryCache(
            max_entries=1000,
            ttl_seconds=300.0,
            similarity_threshold=0.96,
        )
    return _search_cache


def get_chat_cache() -> QueryCache:
    global _chat_cache
    if _chat_cache is None:
        _chat_cache = QueryCache(
            max_entries=200,
            ttl_seconds=600.0,
            similarity_threshold=0.94,
        )
    return _chat_cache
