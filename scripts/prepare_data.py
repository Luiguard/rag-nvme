import os
from pathlib import Path
import json

# Ressourcen-Management: 2 Threads frei lassen
NUM_THREADS = str(max(1, (os.cpu_count() or 4) - 2))
os.environ["OMP_NUM_THREADS"] = NUM_THREADS
os.environ["MKL_NUM_THREADS"] = NUM_THREADS

DATA_DIR = Path("/home/benjamin/projects/rag-it-knowledge/data")
OUT_DIR = Path("/home/benjamin/projects/rag-it-knowledge/processed")
OUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_CHARS = 1500

def iter_text_files():
    exts = [".txt", ".md", ".rst"]
    for root, _, files in os.walk(DATA_DIR):
        for f in files:
            if any(f.endswith(e) for e in exts):
                yield Path(root) / f

def main():
    out_file = OUT_DIR / "chunks.jsonl"
    print(f"🔄 Verarbeite Dateien in {DATA_DIR}...")
    count = 0
    with out_file.open("w", encoding="utf-8") as f_out:
        for path in iter_text_files():
            try:
                txt = path.read_text(encoding="utf-8", errors="ignore")
                # Einfaches Chunking
                for i in range(0, len(txt), MAX_CHARS):
                    chunk = txt[i:i+MAX_CHARS].strip()
                    if chunk:
                        f_out.write(json.dumps({"source": str(path), "text": chunk}, ensure_ascii=False) + "\n")
                        count += 1
            except Exception as e:
                print(f"⚠️ Fehler bei {path}: {e}")
    print(f"✅ Fertig. {count} Chunks in {out_file} gespeichert.")

if __name__ == "__main__":
    main()
