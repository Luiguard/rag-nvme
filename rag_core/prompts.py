"""Prompts für lokale KI: Domänenagnostisch mit Wissens-Modus."""
from __future__ import annotations

from .secure_coding import BEST_PRACTICES, SECURE_CODING_RULES

MODE_SUPPORT = "support"
MODE_CODE = "code"
MODE_REVIEW = "review"
MODE_AUDIT = "audit"


def _domain_context() -> str:
    try:
        from .quality import get_current_domains, is_universal_mode
        if is_universal_mode():
            return "Du bist ein universeller Wissensassistent, spezialisiert auf alle Fachgebiete."
        domains = get_current_domains()
        names = ", ".join(d.name for d in domains)
        return f"Du bist spezialisiert auf: {names}."
    except Exception:
        return ""


_BASE = """Du bist ein lokaler KI-Assistent (100 % offline).
1. QUELLEN & FAKTEN: Wenn der RAG-Kontext Informationen enthält, priorisiere diese absolut.
2. MODELLWISSEN NUTZEN: Falls der Kontext unvollständig ist oder keine direkten Antworten liefert (z. B. bei Standard-Coding-Vorlagen, HTML/CSS-Grundgerüsten, allgemeinen Syntaxfragen oder Begrüßungen), nutze dein pre-trained Modellwissen. Markiere diese Antworten/Abschnitte mit "(ohne RAG-Quellenbeleg / Allgemeines Modellwissen)".
3. ECHTE WISSENSLÜCKEN: Bei hochspezifischen, privaten Daten oder fehlenden Dokumenten weise freundlich darauf hin und bitte den Nutzer, eigene PDFs oder Ordner über den Collector hochzuladen.
4. ZITIERPFLICHT: Zitiere genutzte Quellen als [1], [2] am Ende.

Sprache: Deutsch, präzise.
"""


def _build_system(mode: str) -> str:
    base = _BASE + _domain_context() + "\n"
    if mode == MODE_CODE:
        return (
            base + SECURE_CODING_RULES + BEST_PRACTICES
            + "\nLiefere vollständigen, produktionsreifen Code mit kurzer Begründung der Architektur-Entscheidungen."
        )
    if mode == MODE_REVIEW:
        return (
            base + SECURE_CODING_RULES + BEST_PRACTICES
            + "\nModus: CODE-REVIEW. Liste Stärken, Risiken (Severity), konkrete Fix-Vorschläge mit Code-Snippets."
        )
    if mode == MODE_AUDIT:
        return (
            base + SECURE_CODING_RULES
            + "\nModus: SICHERHEITS-AUDIT. OWASP-orientiert: Injection, Auth, Secrets, Deserialisierung, SSRF, Logging."
        )
    return (
        base + SECURE_CODING_RULES + BEST_PRACTICES
        + "\nBeantworte Fragen hilfreich; bei Unsicherheit nachfragen."
    )


SYSTEM_BY_MODE = {
    MODE_SUPPORT: _build_system(MODE_SUPPORT),
    MODE_CODE: _build_system(MODE_CODE),
    MODE_REVIEW: _build_system(MODE_REVIEW),
    MODE_AUDIT: _build_system(MODE_AUDIT),
}


def get_system_prompt(mode: str) -> str:
    return _build_system(mode)


def build_rag_prompt(
    query: str,
    context: str,
    sources: list[str],
    *,
    mode: str = MODE_SUPPORT,
    user_context: str = "",
) -> list[dict]:
    system = get_system_prompt(mode)
    src_block = "\n".join(f"- {s}" for s in sources[:10]) if sources else "(keine Treffer in Wissensbasis)"
    extra = f"\n\nNUTZER-KONTEXT (eigene Projekte/Notizen):\n{user_context}" if user_context else ""

    user_content = f"""WISSENSBASIS (RAG):
{context or "(LEER - Du MUSST den Nutzer auffordern, relevante Dokumente in den RAG-Collector hochzuladen.)"}
{extra}

---
AUFGABE: {query}

Quellen aus Wissensbasis:
{src_block}"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def build_fix_prompt(original_answer: str, findings_summary: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": get_system_prompt(MODE_CODE)
            + "\nDie vorherige Antwort hatte Sicherheitsprobleme. Korrigiere sie vollständig.",
        },
        {
            "role": "user",
            "content": f"""VORHERIGE ANTWORT:
{original_answer[:6000]}

SICHERHEITSPROBLEME:
{findings_summary}

Liefere eine bereinigte, sichere Version ohne die genannten Probleme.""",
        },
    ]


def _short_source(src: str) -> str:
    from pathlib import Path
    s = src.strip()
    if s.startswith("live:"):
        parts = s.split(":")
        return parts[-1] if len(parts) >= 3 else s
    if s.startswith("user:"):
        return s.split("/")[-1] if "/" in s else s
    if s.startswith("stackexchange:"):
        return s
    p = Path(s)
    return p.stem if len(s) > 40 else p.name


def build_context_block(hits: list[dict]) -> tuple[str, list[str]]:
    from .config import MAX_CONTEXT_CHARS

    parts: list[str] = []
    sources: list[str] = []
    total = 0

    for i, hit in enumerate(hits, 1):
        src = hit.get("source", "unbekannt")
        text = (hit.get("text") or "").strip()
        short = _short_source(src)
        block = f"[{i}] {short}\n{text}\n"
        if total + len(block) > MAX_CONTEXT_CHARS:
            break
        parts.append(block)
        sources.append(src)
        total += len(block)

    return "\n".join(parts), sources
