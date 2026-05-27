"""Systemcheck für GUI (ohne Terminal-Ausgabe)."""
from __future__ import annotations

from pathlib import Path

from .config import LANCE_DB_PATH, MAX_L2_DISTANCE, TOP_K_CONTEXT
from .ollama import list_models, resolve_model
from .quality import hit_relevance_score
from .retrieval import KnowledgeRetriever

TEST_QUERIES = [
    "nginx reverse proxy konfiguration",
    "python list comprehension",
    "docker compose volumes",
    "git rebase vs merge",
]


def run_system_check() -> dict:
    lines: list[str] = []
    ok = True

    if not LANCE_DB_PATH.exists():
        lines.append("❌ lancedb_index fehlt – zuerst „Optimale DB aufbauen“ im Kollektor.")
        return {"ok": False, "lines": lines}

    lines.append(f"✅ Index: {LANCE_DB_PATH}")

    r = KnowledgeRetriever()
    if not r.ready:
        lines.append("❌ Keine durchsuchbare Tabelle (it_prime / it_knowledge).")
        return {"ok": False, "lines": lines}

    n = r.row_count()
    lines.append(f"✅ {n:,} Chunks in «{r.table_name}»")

    models = list_models()
    om = resolve_model()
    if om:
        lines.append(f"✅ Ollama: {om} ({len(models)} Modelle)")
    else:
        lines.append("⚠️ Ollama nicht erreichbar – Antworten nur als Kontext-Vorschau.")
        ok = False

    lines.append(
        f"\nRetrieval (L2 ≤ {MAX_L2_DISTANCE}, top {TOP_K_CONTEXT}):"
    )
    for q in TEST_QUERIES:
        hits = r.search(q)
        if hits:
            best = hit_relevance_score(hits[0])
            dist = float(hits[0].get("_distance", 0))
            src = Path(hits[0].get("source", "")).name[:48]
            lines.append(f"  ✅ {q[:36]}… → {src} (rel {best:.2f}, d={dist:.2f})")
        else:
            lines.append(f"  ⚠️ {q} → keine Treffer")
            ok = False

    return {"ok": ok, "lines": lines, "rows": n, "model": om, "table": r.table_name}
