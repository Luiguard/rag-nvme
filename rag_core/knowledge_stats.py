"""Wissensbasis-Größe berechnen und formatieren."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .config import BASE_DIR, DATA_DIR, LANCE_DB_PATH, TABLE_NAME_LEGACY, TABLE_NAME_PRIME

_SIZE_CACHE_FILE = BASE_DIR / ".knowledge_size_cache.json"
_LEGACY_CACHE_TTL = 3600  # s – volles du auf 35 GB Legacy nur selten

# Referenzgrößen für Vergleich (ungefähr, BF16/FP16)
MODEL_REFERENCE_GB = {
    "7B": 14,
    "13B": 26,
    "32B": 64,
    "70B": 140,
}


def dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    try:
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _du_bytes(path: Path, timeout: int = 120) -> int:
    try:
        import subprocess
        r = subprocess.run(
            ["du", "-sb", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.split()[0])
    except Exception:
        pass
    return dir_size_bytes(path)


def _read_size_cache() -> dict:
    try:
        if _SIZE_CACHE_FILE.exists():
            return json.loads(_SIZE_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_size_cache(data: dict) -> None:
    try:
        _SIZE_CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def lance_table_sizes(refresh_legacy: bool = False) -> dict[str, int]:
    """Bytes pro LanceDB-Tabelle (Legacy gecacht – schnelle GUI-Updates)."""
    out: dict[str, int] = {}
    base = LANCE_DB_PATH
    if not base.exists():
        return out
    legacy_path = base / f"{TABLE_NAME_LEGACY}.lance"
    prime_path = base / f"{TABLE_NAME_PRIME}.lance"
    cache = _read_size_cache()
    now = time.time()

    if prime_path.exists():
        out[TABLE_NAME_PRIME] = _du_bytes(prime_path, timeout=30)

    if legacy_path.exists():
        cached_legacy = int(cache.get("legacy_bytes", 0))
        cached_at = float(cache.get("legacy_ts", 0))
        if (
            not refresh_legacy
            and cached_legacy > 0
            and (now - cached_at) < _LEGACY_CACHE_TTL
        ):
            out[TABLE_NAME_LEGACY] = cached_legacy
        else:
            legacy_b = _du_bytes(legacy_path, timeout=300)
            out[TABLE_NAME_LEGACY] = legacy_b
            cache["legacy_bytes"] = legacy_b
            cache["legacy_ts"] = now
            cache["prime_bytes"] = out.get(TABLE_NAME_PRIME, cache.get("prime_bytes", 0))
            _write_size_cache(cache)
    return out


def count_table_rows(only: tuple[str, ...] | None = None) -> dict[str, int]:
    """Zeilen zählen – große Legacy-Tabellen standardmäßig überspringen (sehr langsam)."""
    rows: dict[str, int] = {}
    try:
        import lancedb
        if not LANCE_DB_PATH.exists():
            return rows
        db = lancedb.connect(str(LANCE_DB_PATH))
        raw = db.list_tables()
        names = list(raw.tables if hasattr(raw, "tables") else raw)
        if only is not None:
            names = [n for n in names if n in only]
        else:
            skip = {TABLE_NAME_LEGACY}
            names = [n for n in names if n not in skip]
        for name in names:
            try:
                rows[name] = db.open_table(name).count_rows()
            except Exception:
                rows[name] = 0
    except Exception:
        pass
    return rows


def format_bytes(n: int) -> str:
    if n < 0:
        n = 0
    if n >= 1024**4:
        return f"{n / 1024**4:.2f} TB"
    if n >= 1024**3:
        return f"{n / 1024**3:.2f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def model_equivalent_gb(total_bytes: int) -> str:
    gb = total_bytes / (1024**3)
    if gb <= 0:
        return "—"
    parts = []
    for label, ref in sorted(MODEL_REFERENCE_GB.items(), key=lambda x: x[1]):
        ratio = gb / ref
        if ratio < 0.05:
            parts.append(f"&lt;0.1× {label}")
        else:
            parts.append(f"≈{ratio:.1f}× {label}")
    return " | ".join(parts[:3])


def gather_stats(
    use_du_for_data: bool = False,
    data_bytes_hint: int | None = None,
    refresh_legacy: bool = False,
) -> dict:
    """Sammelt Größenangaben. Index per du; data/ optional (sonst Hint aus GUI)."""
    table_bytes = lance_table_sizes(refresh_legacy=refresh_legacy)
    row_counts = count_table_rows(only=(TABLE_NAME_PRIME,))

    index_bytes = sum(table_bytes.values())
    prime_bytes = table_bytes.get(TABLE_NAME_PRIME, 0)
    legacy_bytes = table_bytes.get(TABLE_NAME_LEGACY, 0)

    if data_bytes_hint is not None:
        data_bytes = data_bytes_hint
    elif use_du_for_data and DATA_DIR.exists():
        data_bytes = _du_bytes(DATA_DIR, timeout=90)
    else:
        data_bytes = 0

    prime_rows = row_counts.get(TABLE_NAME_PRIME, 0)
    legacy_rows = 0  # Legacy-Zählung zu langsam; Größe reicht als Näherung
    active_rows = prime_rows if prime_rows > 0 else legacy_rows
    active_index = prime_bytes if prime_rows > 0 else legacy_bytes

    total_disk = index_bytes + data_bytes
    # Grobe Schätzung Klartext in Chunks (~1.5 KB/Chunk inkl. Vektor anteilig)
    est_text_bytes = active_rows * 1500 if active_rows else 0

    return {
        "index_bytes": index_bytes,
        "prime_bytes": prime_bytes,
        "legacy_bytes": legacy_bytes,
        "active_index_bytes": active_index,
        "data_bytes": data_bytes,
        "total_disk_bytes": total_disk,
        "prime_rows": prime_rows,
        "legacy_rows": legacy_rows,
        "active_rows": active_rows,
        "est_text_bytes": est_text_bytes,
        "table_bytes": table_bytes,
    }


def _markup_esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&apos;")
        .replace('"', "&quot;")
    )


def format_stats_markup(stats: dict) -> str:
    """GTK-kompatibles Markup für die GUI."""
    esc = _markup_esc

    idx_b = stats["active_index_bytes"] or stats["index_bytes"]
    total_b = stats["total_disk_bytes"]
    rows = stats["active_rows"]
    prime_rows = stats["prime_rows"]
    legacy_rows = stats["legacy_rows"]

    active_table = TABLE_NAME_PRIME if prime_rows > 0 else TABLE_NAME_LEGACY

    lines = [
        f"<b>Wissen gesamt (Platte):</b> {esc(format_bytes(total_b))}",
        f"<b>Durchsuchbarer Index ({esc(active_table)}):</b> {esc(format_bytes(idx_b))}"
        + (f" · {rows:,} Chunks" if rows else ""),
        f"<b>Rohdaten (data/):</b> {esc(format_bytes(stats['data_bytes']))}",
    ]
    if stats["legacy_bytes"] > 0:
        leg = esc(format_bytes(stats["legacy_bytes"]))
        if prime_rows:
            lines.append(
                f"<span size='small'>it_prime: {esc(format_bytes(stats['prime_bytes']))} "
                f"({prime_rows:,}) | Legacy it_knowledge: {leg}</span>"
            )
        else:
            lines.append(
                f"<span size='small'>Legacy it_knowledge (nicht aktiv): {leg}</span>"
            )
    est = stats.get("est_text_bytes", 0)
    if est and rows:
        lines.append(
            f"<span size='small'><i>~Klartext in Chunks:</i> {esc(format_bytes(est))}</span>"
        )
    return "\n".join(lines)


def format_stats_plain(stats: dict) -> str:
    idx = stats["active_index_bytes"] or stats["index_bytes"]
    return (
        f"Gesamt: {format_bytes(stats['total_disk_bytes'])} | "
        f"Index: {format_bytes(idx)} ({stats['active_rows']:,} Chunks) | "
        f"data/: {format_bytes(stats['data_bytes'])}"
    )
