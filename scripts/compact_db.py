#!/usr/bin/env python3
"""Kompaktiert LanceDB-Tabellen: 19k Fragmente → ~20 Fragmente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lancedb
from rag_core.config import LANCE_DB_PATH

db = lancedb.connect(str(LANCE_DB_PATH))
tables = db.table_names() if hasattr(db, "table_names") else list(db.list_tables())

for name in tables:
    print(f"\n🔧 Kompaktiere: {name}")
    try:
        t = db.open_table(name)
        rows = t.count_rows()
        print(f"   Rows: {rows:,}")
        t.optimize()
        print(f"   ✅ optimize() (Kompaktierung & Cleanup) fertig")
        
        try:
            print(f"   ⏳ Erstelle/Aktualisiere FTS-Index (Full-Text Search)...")
            t.create_fts_index("text", replace=True)
            print(f"   ✅ FTS-Index aktualisiert")
        except Exception as fts_err:
            print(f"   ⚠️ FTS-Index konnte nicht erstellt werden: {fts_err}")
            
    except Exception as e:
        print(f"   ⚠️ Fehler: {e}")

print("\n✅ Kompaktierung abgeschlossen")
