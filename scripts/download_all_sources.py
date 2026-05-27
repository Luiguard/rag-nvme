#!/usr/bin/env python3
"""Lädt alle Bulk-Quellen ohne GUI (Git + Stack Exchange)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_core.bulk_download import download_all_git, download_all_stackexchange
from rag_core.config import DATA_DIR

running = True


def log(msg: str):
    print(msg, flush=True)


def main():
    only = sys.argv[1:] if len(sys.argv) > 1 else []
    print("📦 Bulk-Download aller IT-Quellen\n")

    if not only or "git" in only:
        download_all_git(DATA_DIR, log, lambda: running)

    if not only or "stackexchange" in only:
        max_sites = 0
        if "--quick" in only:
            max_sites = 2  # SO + Server Fault
        download_all_stackexchange(DATA_DIR, log, lambda: running, max_sites=max_sites)

    print("\n✅ Download-Phase abgeschlossen.")
    print("   Index: ./local_rag.sh prime")
    print("   Stack Exchange XML: .venv/bin/python scripts/ingest_stackexchange.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
