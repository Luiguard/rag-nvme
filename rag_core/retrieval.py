"""LanceDB-Suche mit Qualitäts-Reranking für lokale KI."""
from __future__ import annotations

from pathlib import Path

import lancedb

from .config import (
    LANCE_DB_PATH,
    MAX_L2_DISTANCE,
    TABLE_NAME,
    TABLE_NAME_LEGACY,
    TABLE_NAME_PRIME,
    TOP_K_CONTEXT,
    TOP_K_FETCH,
    TOP_K_USER,
    RAG_LAZY_EMBEDDING,
)
from .quality import is_user_project_source, rerank_hits
from .embeddings import embed_query
from .prompts import build_context_block, build_rag_prompt
from .query_cache import get_search_cache


class KnowledgeRetriever:
    def __init__(self, db_path: Path | None = None, table_name: str | None = None):
        self.db_path = db_path or LANCE_DB_PATH
        self.table_name = table_name or TABLE_NAME
        self.db = None
        self.table = None
        self._cache = get_search_cache()
        self._connect()

    def _connect(self) -> None:
        if not self.db_path.exists():
            return
        self.db = lancedb.connect(str(self.db_path))
        raw = (
            self.db.list_tables()
            if hasattr(self.db, "list_tables")
            else self.db.table_names()
        )
        if hasattr(raw, "tables"):
            tables = raw.tables
        elif isinstance(raw, list):
            tables = raw
        else:
            tables = list(raw)
        preferred = [TABLE_NAME_PRIME, TABLE_NAME_LEGACY]
        if TABLE_NAME not in preferred:
            preferred.insert(0, TABLE_NAME)
        if self.table_name and self.table_name not in preferred:
            preferred.insert(0, self.table_name)
        elif self.table_name:
            preferred.remove(self.table_name)
            preferred.insert(0, self.table_name)
        seen = set()
        preferred = [x for x in preferred if not (x in seen or seen.add(x))]
        for name in preferred:
            if name in tables:
                try:
                    self.table_name = name
                    self.table = self.db.open_table(name)
                    break
                except Exception as e:
                    if "Invalid range" in str(e) or "lance error" in str(e).lower():
                        import shutil
                        try:
                            self.db.drop_table(name)
                        except Exception:
                            pass
                        table_dir = self.db_path / f"{name}.lance"
                        if table_dir.exists():
                            shutil.rmtree(table_dir, ignore_errors=True)
                        try:
                            from .indexing import get_schema
                            self.table = self.db.create_table(name, schema=get_schema(), exist_ok=True)
                            self.table_name = name
                            break
                        except Exception:
                            pass

    @property
    def ready(self) -> bool:
        return self.table is not None

    def row_count(self) -> int:
        if not self.table:
            return 0
        return self.table.count_rows()

    def _do_search(self, query: str, vector: list[float], top_k_fetch: int) -> list[dict]:
        candidates = {}
        
        # 1. FTS Search (Schnell, keyword-basiert)
        try:
            fts_raw = self.table.search(query).limit(top_k_fetch).to_list()
            for h in fts_raw:
                candidates[(h.get("source", ""), h.get("text", "")[:100])] = h
        except Exception:
            pass

        # 2. Dense Vector Search (Perfekt für Konzepte, ignoriert Dummy-Vektoren)
        try:
            dense_raw = self.table.search(vector).limit(top_k_fetch).to_list()
            for h in dense_raw:
                h_vec = h.get("vector")
                if h_vec and sum(h_vec) != 0.0:
                    candidates[(h.get("source", ""), h.get("text", "")[:100])] = h
        except Exception:
            pass

        raw = list(candidates.values())
        if not raw:
            return []

        # 3. Dynamic Dense Rescoring für Keyword-Treffer ohne echte Vektoren
        texts_to_embed = [h["text"] for h in raw if sum(h.get("vector", [])) == 0.0]
        if texts_to_embed:
            from .embeddings import embed_documents
            new_vectors = embed_documents(texts_to_embed, force=True)
            if new_vectors and len(new_vectors) == len(texts_to_embed):
                idx = 0
                for h in raw:
                    if sum(h.get("vector", [])) == 0.0:
                        h["vector"] = new_vectors[idx]
                        idx += 1

        # 4. Exakte L2-Distanz für das gesamte (nun vektorisierte) Kandidatenfeld
        for h in raw:
            h_vec = h.get("vector")
            if h_vec and sum(h_vec) != 0.0:
                h["_distance"] = sum((x - y) ** 2 for x, y in zip(vector, h_vec))
            else:
                h["_distance"] = 999.0

        raw.sort(key=lambda x: x.get("_distance", 999.0))
        return raw[:top_k_fetch]

    def search(self, query: str, *, top_k: int | None = None, include_user: bool = True) -> list[dict]:
        if not self.table:
            return []
        k = top_k or TOP_K_CONTEXT

        vector, _ = embed_query(query)

        cached = self._cache.get(query, vector)
        if cached is not None:
            return cached

        raw = self._do_search(query, vector, TOP_K_FETCH)
        if include_user:
            user_raw = [h for h in raw if is_user_project_source(h.get("source", ""))]
            general = [h for h in raw if not is_user_project_source(h.get("source", ""))]
            user_ranked = rerank_hits(user_raw, TOP_K_USER)
            general_ranked = rerank_hits(general, k)
            seen = set()
            merged: list[dict] = []
            for h in user_ranked + general_ranked:
                key = (h.get("source"), (h.get("text") or "")[:120])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(h)
            raw = merged
        ranked = rerank_hits(raw, max(k + TOP_K_USER, TOP_K_CONTEXT))
        good = [h for h in ranked if float(h.get("_distance", 999)) <= MAX_L2_DISTANCE]
        if good:
            res = good[:k]
        else:
            from .quality import is_blocked_source, is_low_quality_hit
            soft = [
                h for h in ranked
                if not is_blocked_source(h.get("source", "")) and not is_low_quality_hit(h)
            ]
            res = (soft or ranked)[:k]

        self._cache.put(query, vector, res)
        return res

    def get_context(self, query: str) -> tuple[str, list[str], list[dict]]:
        hits = self.search(query)
        context, sources = build_context_block(hits)
        return context, sources, hits

    def get_user_context(self, query: str) -> str:
        if not self.table:
            return ""
        vector, _ = embed_query(query)
        raw = self._do_search(query, vector, TOP_K_FETCH)
        user_hits = rerank_hits(
            [h for h in raw if is_user_project_source(h.get("source", ""))],
            TOP_K_USER,
        )
        if not user_hits:
            return ""
        ctx, _ = build_context_block(user_hits)
        return ctx

    def get_messages_for_ollama(
        self,
        query: str,
        *,
        mode: str = "support",
        user_context: str = "",
    ) -> tuple[list[dict], list[dict]]:
        hits = self.search(query)
        if not user_context:
            user_context = self.get_user_context(query)
        context, sources = build_context_block(hits)
        return build_rag_prompt(query, context, sources, mode=mode, user_context=user_context), hits

    def format_hit_summary(self, hits: list[dict], *, mobile: bool = False) -> str:
        lines = []
        icon = "📱" if mobile else "💻"
        for h in hits:
            src = h.get("source", "?")
            from .quality import hit_relevance_score
            score = hit_relevance_score(h)
            lines.append(f"  {icon} {Path(src).name if len(src) > 60 else src}  (Relevanz {score:.3f})")
        return "\n".join(lines) if lines else "  (keine relevanten Treffer über Mindest-Score)"
