#!/usr/bin/env python3
"""Indexiert deine Projekt-Repositories in die Prime-Wissensbasis (user:…)."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("OMP_NUM_THREADS", str(max(1, (os.cpu_count() or 4) - 2)))

from rag_core.config import INDEX_BATCH_SIZE, USER_WORKSPACE_ROOTS
from rag_core.indexing import (
    file_to_user_records,
    iter_user_project_files,
    make_splitter,
    open_prime_table,
)


def main():
    print("📁 Indexiere deine Projekte für persönliche KI-Unterstützung")
    for r in USER_WORKSPACE_ROOTS:
        print(f"   → {r}")
    table = open_prime_table(recreate=False)
    splitter = make_splitter()
    files = list(iter_user_project_files())
    print(f"\n{len(files)} Dateien gefunden.\n")

    batch: list[dict] = []
    done = skipped = 0
    t0 = time.time()

    for path in files:
        recs = file_to_user_records(path, splitter)
        if not recs:
            skipped += 1
            continue
        batch.extend(recs)
        done += 1
        if len(batch) >= INDEX_BATCH_SIZE:
            table.add(batch)
            print(f"   … {done} Dateien | {table.count_rows():,} Chunks gesamt")
            batch = []

    if batch:
        table.add(batch)

    print(f"\n✨ Fertig: {done} Dateien, {skipped} übersprungen, {(time.time()-t0)/60:.1f} min")
    print(f"   Chunks gesamt: {table.count_rows():,}")
    print("   Chat: ./local_rag.sh chat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
