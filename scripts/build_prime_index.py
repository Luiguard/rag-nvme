#!/usr/bin/env python3
"""
Baut die kuratierte Prime-Wissensbasis (it_prime) aus hochwertigen IT-Quellen.
Manpages, Stack Overflow, RFCs, MDN, TLDR, Wikipedia-API, Linux-Docs.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NUM_THREADS = str(max(1, (os.cpu_count() or 4) - 2))
os.environ.setdefault("OMP_NUM_THREADS", NUM_THREADS)
os.environ.setdefault("MKL_NUM_THREADS", NUM_THREADS)

os.environ.setdefault("RAG_PREFER_LOCAL", "1")

from rag_core.config import (
    DATA_DIR,
    INDEX_FILE_LIMIT,
    LANCE_DB_PATH,
    PRIORITY_DATA_ROOTS,
    TABLE_NAME,
)
from rag_core.indexing import (
    files_to_records_batched,
    user_files_to_records_batched,
    iter_indexable_files,
    iter_user_project_files,
    make_splitter,
    maybe_delete_source,
    open_prime_table,
)


def main():
    recreate = "--fresh" in sys.argv
    print("🎯 Prime-Index: kuratierte IT-Wissensbasis für lokale KI")
    print(f"   Quellen: {', '.join(PRIORITY_DATA_ROOTS)}")
    print(f"   Ziel: {LANCE_DB_PATH}/{TABLE_NAME}")
    if INDEX_FILE_LIMIT:
        print(f"   Limit: {INDEX_FILE_LIMIT} Dateien (Testmodus)")
    if recreate:
        print("   Modus: Neuaufbau (--fresh)\n")
    else:
        print()

    table = open_prime_table(recreate=recreate)
    
    existing_sources = set()
    if not recreate and table is not None:
        try:
            ds = table.to_lance()
            existing_sources = set(ds.scanner(columns=["source"]).to_table()["source"].to_pylist())
            print(f"🔍 {len(existing_sources)} bereits indizierte Quellen gefunden (Deduplizierung aktiv)")
        except Exception:
            pass

    splitter = make_splitter()
    
    all_files = list(iter_indexable_files(priority_only=True))
    files = [f for f in all_files if str(f) not in existing_sources]
    print(f"📂 {len(all_files)} Dateien in Prime-Quellen, davon {len(files)} neu zu verarbeiten")

    done = skipped = 0
    t0 = time.time()
    file_batch_size = 50

    for idx in range(0, len(files), file_batch_size):
        sub_files = files[idx : idx + file_batch_size]
        records = files_to_records_batched(sub_files, splitter)
        if records:
            table.add(records)
            for f in sub_files:
                maybe_delete_source(f)
            done += len(sub_files)
        else:
            skipped += len(sub_files)
        print(f"   … {done}/{len(files)} Dateien | {table.count_rows():,} Chunks", end="\r", flush=True)

    print()
    elapsed = time.time() - t0
    print(f"\n📁 Phase 2: Deine Projekte …")
    u_done = u_skip = 0
    from rag_core.indexing import user_source_label
    all_user_files = list(iter_user_project_files())
    user_files = [f for f in all_user_files if user_source_label(f) not in existing_sources]
    print(f"   {len(all_user_files)} Dateien gefunden, davon {len(user_files)} neu zu verarbeiten")
    for idx in range(0, len(user_files), file_batch_size):
        sub_files = user_files[idx : idx + file_batch_size]
        recs = user_files_to_records_batched(sub_files, splitter)
        if recs:
            table.add(recs)
            u_done += len(sub_files)
        else:
            u_skip += len(sub_files)

    print(f"\n✨ Prime-Index fertig: {table.count_rows():,} Chunks")
    print(f"   IT-Quellen: {done} OK, {skipped} skip | Projekte: {u_done} OK, {u_skip} skip")
    print(f"   Dauer: {(time.time()-t0)/60:.1f} min")
    
    print("⚡ Erstelle FTS-Invertierten-Index...")
    try:
        table.create_fts_index("text", replace=True)
        print("✅ FTS-Index erfolgreich erstellt!")
    except Exception as e:
        print(f"⚠️ FTS-Index Erstellung fehlgeschlagen: {e}")
        
    print("   Chat: ./local_rag.sh chat  |  Nur Projekte: ./local_rag.sh projects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
