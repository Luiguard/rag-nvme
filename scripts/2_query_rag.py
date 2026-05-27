#!/usr/bin/env python3
"""Lokaler IT-Assistent: Wissen + sicheres Coding + Review (Ollama)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_core.assistant import LocalAssistant
from rag_core.embeddings import embed_query
from rag_core.ollama import list_models, resolve_model
from rag_core.retrieval import KnowledgeRetriever
from rag_core.secure_coding import is_safe_user_intent

HELP = """
Modi (Präfix oder automatisch erkannt):
  /code <aufgabe>     – sicherer, produktionsreifer Code
  /review <code/frage> – Code-Review mit Risiken & Fixes
  /audit <thema>      – Sicherheits-Audit (OWASP)
  /support <frage>    – allgemeine Unterstützung

Beispiele:
  /code FastAPI Endpoint mit JWT und Rate-Limit
  /review mein Docker-compose auf Schwachstellen
  /audit SQL-Injection in dieser Funktion: ...
"""


def main():
    retriever = KnowledgeRetriever()
    if not retriever.ready:
        print("❌ Kein LanceDB-Index. Starte die GUI: ./start-rag.sh")
        print("   → Kollektor: „Optimale DB aufbauen“")
        return 1

    assistant = LocalAssistant(retriever)
    rows = retriever.row_count()
    model = resolve_model()

    print("=" * 56)
    print("  Lokale KI – Wissen · Coding · Sicherheit · Review")
    print("=" * 56)
    print(f"  Wissensbasis: {rows:,} Chunks ({retriever.table_name})")
    print(f"  Ollama: {model or 'nicht erreichbar'}")
    print("  Eigene Projekte werden bevorzugt, wenn indexiert.")
    print(HELP)
    print("  exit = Beenden\n")

    while True:
        try:
            query = input("Du: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not query or query.lower() in ("exit", "quit", "q"):
            break
        if query.lower() in ("help", "?"):
            print(HELP)
            continue
        if not is_safe_user_intent(query):
            print("⛔ Anfrage blockiert (Sicherheitsrichtlinie).")
            continue

        _, mobile = embed_query(query)
        q, mode = assistant.parse_mode(query)
        hits = retriever.search(q)
        print("\n📚 Quellen:")
        print(retriever.format_hit_summary(hits, mobile=mobile))

        if not hits and not retriever.get_user_context(q):
            print("\n⚠️  Kein passender Kontext. In der GUI: „Meine Projekte“ indexieren")

        if not resolve_model():
            ctx, _, _ = retriever.get_context(q)
            print("\n--- Kontext ---\n", ctx[:5000])
            continue

        print(f"\n🤖 Modus: {mode}\n")
        try:
            assistant.answer(q, mode=mode, retry_on_unsafe=True)
        except Exception as e:
            print(f"Fehler: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
