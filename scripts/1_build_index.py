#!/usr/bin/env python3
"""Indiziert IT-Quellen in LanceDB (qualitätsgefiltert)."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NUM_THREADS = str(max(1, (os.cpu_count() or 4) - 2))
os.environ["OMP_NUM_THREADS"] = NUM_THREADS
os.environ["MKL_NUM_THREADS"] = NUM_THREADS
os.environ["NUMEXPR_NUM_THREADS"] = NUM_THREADS

os.environ.setdefault("RAG_PREFER_LOCAL", "1")

from rag_core.config import DATA_DIR, DELETE_SOURCE_AFTER_INDEX, LANCE_DB_PATH
from rag_core.indexing import (
    iter_indexable_files,
    files_to_records_batched,
    make_splitter,
    maybe_delete_source,
    open_table,
)


def main():
    print("🚀 IT-Wissensbasis → LanceDB")
    print(f"   Daten: {DATA_DIR.resolve()}")
    print(f"   Index: {LANCE_DB_PATH.resolve()}")
    print(f"   Quellen löschen nach Index: {DELETE_SOURCE_AFTER_INDEX}\n")

    table = open_table()
    splitter = make_splitter()
    all_files = list(iter_indexable_files())
    print(f"📂 {len(all_files)} Dateien nach Qualitätsfilter (Pfade)")

    done = 0
    skipped = 0
    t0 = time.time()
    file_batch_size = 50

    for idx in range(0, len(all_files), file_batch_size):
        sub_files = all_files[idx : idx + file_batch_size]
        records = files_to_records_batched(sub_files, splitter)
        if records:
            table.add(records)
            for f in sub_files:
                maybe_delete_source(f)
            done += len(sub_files)
        else:
            skipped += len(sub_files)

        elapsed = time.time() - t0
        rate = done / elapsed if elapsed else 0
        print(f"✅ {done}/{len(all_files)} Dateien | {rate:.1f} files/s", end="\r", flush=True)

    print(f"\n✨ Fertig: {done} Dateien indiziert, {skipped} übersprungen (Qualität/Leer).")
    print(f"   Chunks gesamt: {table.count_rows():,}")
    
    print("⚡ Erstelle FTS-Invertierten-Index...")
    try:
        table.create_fts_index("text", replace=True)
        print("✅ FTS-Index erfolgreich erstellt!")
    except Exception as e:
        print(f"⚠️ FTS-Index Erstellung fehlgeschlagen: {e}")
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
