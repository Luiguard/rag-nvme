"""Qualitätsfilter und Quellen-Priorisierung – domänenagnostisch."""
from __future__ import annotations

import json
from pathlib import Path

from .domains import (
    ALL_DOMAINS,
    DEFAULT_DOMAIN_ID,
    DOMAIN_UNIVERSAL,
    KnowledgeDomain,
    get_domain,
    merged_blocked_patterns,
    merged_content_signals,
    merged_source_boosts,
)

_BASE_DIR = Path(__file__).resolve().parent.parent
_DOMAIN_STATE_FILE = _BASE_DIR / "domain_config.json"

_active_domains: list[KnowledgeDomain] | None = None


def _load_active_domains() -> list[KnowledgeDomain]:
    global _active_domains
    if _active_domains is not None:
        return _active_domains
    try:
        if _DOMAIN_STATE_FILE.exists():
            cfg = json.loads(_DOMAIN_STATE_FILE.read_text("utf-8"))
            ids = cfg.get("active_domains", [DEFAULT_DOMAIN_ID])
            _active_domains = [get_domain(d) for d in ids if d in ALL_DOMAINS]
            if not _active_domains:
                _active_domains = [get_domain(DEFAULT_DOMAIN_ID)]
            return _active_domains
    except Exception:
        pass
    _active_domains = [get_domain(DEFAULT_DOMAIN_ID)]
    return _active_domains


def save_active_domains(domain_ids: list[str]) -> None:
    global _active_domains
    valid = [d for d in domain_ids if d in ALL_DOMAINS]
    if not valid:
        valid = [DEFAULT_DOMAIN_ID]
    cfg = {"active_domains": valid}
    _DOMAIN_STATE_FILE.write_text(json.dumps(cfg, indent=2), "utf-8")
    _active_domains = [get_domain(d) for d in valid]


def get_current_domains() -> list[KnowledgeDomain]:
    return _load_active_domains()


def reload_domains() -> None:
    global _active_domains
    _active_domains = None
    _load_active_domains()


def _get_blocked() -> tuple[str, ...]:
    domains = _load_active_domains()
    return merged_blocked_patterns(domains)


def _get_signals() -> tuple[str, ...]:
    domains = _load_active_domains()
    return merged_content_signals(domains)


def _get_boosts() -> dict[str, float]:
    domains = _load_active_domains()
    return merged_source_boosts(domains)


def is_universal_mode() -> bool:
    return any(d.id == "universal" for d in _load_active_domains())


def is_blocked_source(source: str) -> bool:
    s = source.lower()
    return any(b in s for b in _get_blocked())


def source_boost(source: str) -> float:
    s = source.lower()
    if is_blocked_source(s):
        return 0.0
    boosts = _get_boosts()
    for pattern, boost in boosts.items():
        if pattern in s:
            return boost
    return 0.95


def classify_source(path: str | Path) -> str:
    p = str(path).lower()
    if "manpages" in p:
        return "manpages"
    if "stackoverflow" in p:
        return "stackoverflow"
    if "rfcs" in p:
        return "rfcs"
    if "tldr" in p:
        return "tldr"
    if "stackexchange" in p:
        return "stackexchange"
    if "owasp" in p:
        return "owasp"
    if "arch-wiki" in p:
        return "archwiki"
    if "official-docs" in p:
        return "official-docs"
    if "mdn-web-docs" in p:
        return "mdn"
    if "linux-docs" in p:
        return "linux"
    if "wikipedia" in p or ("processed" in p and "wiki" in p):
        return "wikipedia"
    if "gutenberg" in p:
        return "gutenberg"
    if "custom_docs" in p:
        return "custom"
    return "other"


def is_user_project_source(source: str | Path) -> bool:
    return str(source).startswith("user:") or "/projects/" in str(source).replace("\\", "/")


def is_indexable_content(text: str, source: str | Path) -> bool:
    if len(text.strip()) < 30:
        return False
    src = str(source)
    if is_blocked_source(src):
        return False
    return True


def is_low_quality_hit(hit: dict) -> bool:
    src = hit.get("source", "")
    text = hit.get("text", "")
    sl = src.lower()
    if any(x in sl for x in (".jpg", ".png", ".svg", ".gif", "seite:la2", "blitz-0")):
        return True
    if len(text.strip()) < 25:
        return True
    return False


def hit_relevance_score(hit: dict) -> float:
    """Höher = besser (L2-Distanz-kompatibel)."""
    dist = float(hit.get("_distance", 999.0))
    base = 1.0 / (1.0 + dist)
    return base * source_boost(hit.get("source", ""))


def rerank_hits(hits: list[dict], top_k: int) -> list[dict]:
    """Filtert Rauschen und sortiert nach Ähnlichkeit × Quellen-Boost."""
    scored: list[tuple[float, dict]] = []
    seen_text: set[str] = set()

    for hit in hits:
        if is_low_quality_hit(hit):
            continue
        src = hit.get("source", "")
        if is_blocked_source(src):
            continue
        text = (hit.get("text") or "").strip()
        if not text or text[:200] in seen_text:
            continue
        seen_text.add(text[:200])
        scored.append((hit_relevance_score(hit), hit))

    scored.sort(key=lambda x: -x[0])
    return [h for _, h in scored[:top_k]]


def enhance_query(query: str) -> str:
    """Domänenspezifische Query-Erweiterung für bessere Embedding-Treffer."""
    q = query.strip()
    if is_universal_mode():
        return q
    domains = _load_active_domains()
    domain_names = [d.name for d in domains]
    prefix = " ".join(domain_names)
    lower = q.lower()
    if any(lower.startswith(n.lower()[:6]) for n in domain_names):
        return q
    return f"{prefix}: {q}"
