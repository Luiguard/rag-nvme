import os
from pathlib import Path

# Ressourcen-Management: 2 CPU-Threads frei lassen
NUM_THREADS = str(max(1, (os.cpu_count() or 4) - 2))
os.environ["OMP_NUM_THREADS"] = NUM_THREADS
os.environ["MKL_NUM_THREADS"] = NUM_THREADS
os.environ["NUMEXPR_NUM_THREADS"] = NUM_THREADS

import bz2
import gzip
import xml.etree.ElementTree as ET
import sys
from datetime import datetime

try:
    import mwparserfromhell
except ImportError:
    mwparserfromhell = None

try:
    import fitz  # pymupdf
except ImportError:
    fitz = None

DATA_DIR = Path(os.path.join(os.path.dirname(__file__), "../data")).resolve()
OUTPUT_DIR = DATA_DIR / "processed"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def wiki_to_text(wikitext):
    if mwparserfromhell:
        try:
            parsed = mwparserfromhell.parse(wikitext)
            return parsed.strip_code()
        except:
            return wikitext
    return wikitext

class ProgressFile:
    def __init__(self, path):
        self.fileobj = open(path, "rb")
        self.total_size = path.stat().st_size
        self.bytes_read = 0

    def read(self, size=-1):
        data = self.fileobj.read(size)
        self.bytes_read += len(data)
        return data

    def readinto(self, b):
        num_bytes = self.fileobj.readinto(b)
        self.bytes_read += num_bytes
        return num_bytes

    def seek(self, offset, whence=0):
        res = self.fileobj.seek(offset, whence)
        self.bytes_read = self.fileobj.tell()
        return res

    def tell(self):
        return self.fileobj.tell()

    def close(self):
        self.fileobj.close()

    def readable(self):
        return True

    def seekable(self):
        return True

def process_wiki_dump(dump_path, out_dir):
    log(f"Verarbeite Wikipedia-Dump: {dump_path.name}")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_wrapper = ProgressFile(dump_path)

    if str(dump_path).endswith(".bz2"):
        f_in = bz2.open(raw_wrapper, "rt", encoding="utf-8", errors="replace")
    elif str(dump_path).endswith(".gz"):
        f_in = gzip.open(raw_wrapper, "rt", encoding="utf-8", errors="replace")
    else:
        log(f"  Unbekanntes Format: {dump_path.suffix}, übersprungen.")
        raw_wrapper.close()
        return

    count = 0
    current_title = None
    current_text = None
    in_page = False

    try:
        with f_in as f:
            for event, elem in ET.iterparse(f, events=("start", "end")):
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

                if event == "start":
                    if tag == "page":
                        in_page = True
                        current_title = None
                        current_text = None
                elif event == "end":
                    if tag == "title" and in_page:
                        current_title = elem.text or ""
                    elif tag == "text" and in_page:
                        current_text = elem.text or ""
                    elif tag == "page":
                        in_page = False
                        if current_title and current_text and len(current_text) > 100:
                            if not current_text.startswith("#REDIRECT") and not current_text.startswith("#WEITERLEITUNG"):
                                clean_text = wiki_to_text(current_text)
                                if len(clean_text) > 50:
                                    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in current_title)[:100]
                                    file_path = out_dir / f"{safe_name}.txt"
                                    if not file_path.exists():
                                        with open(file_path, "w", encoding="utf-8") as out:
                                            out.write(f"# {current_title}\n\n{clean_text}\n")
                                        count += 1
                                        if count % 1000 == 0:
                                            percent = (raw_wrapper.bytes_read / raw_wrapper.total_size) * 100
                                            log(f"  [{percent:.1f}%] {count} neue Artikel extrahiert...")
                        elem.clear()

    except Exception as e:
        log(f"  Fehler beim Parsen: {e}")
    finally:
        raw_wrapper.close()

    log(f"  ✅ {count} neue Artikel aus {dump_path.name} extrahiert.")
    return count

def process_pdf_dir(pdf_dir, out_dir):
    if not fitz:
        log("⚠️ pymupdf nicht installiert, PDFs werden übersprungen.")
        return

    log(f"Verarbeite PDFs in: {pdf_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    pdfs = list(pdf_dir.rglob("*.pdf"))
    if not pdfs:
        log("  Keine PDFs gefunden.")
        return

    count = 0
    for pdf_path in pdfs:
        try:
            safe_name = pdf_path.stem[:100]
            txt_path = out_dir / f"{safe_name}.txt"
            if txt_path.exists():
                continue

            doc = fitz.open(str(pdf_path))
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()

            full_text = "\n".join(text_parts)
            if len(full_text) > 100:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(f"# {pdf_path.stem}\n\n{full_text}\n")
                count += 1
                if count % 100 == 0:
                    log(f"  {count}/{len(pdfs)} PDFs konvertiert...")
        except Exception as e:
            pass  # Korrupte PDFs ignorieren

    log(f"  ✅ {count} PDFs zu Text konvertiert.")

def main():
    log("🔄 PREPROCESSING: Konvertiere Rohdaten → Klartext für LanceDB")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0

    # 1. Wikipedia/Wikimedia XML Dumps
    dump_dir = DATA_DIR / "dumps"
    if dump_dir.exists():
        for dump_file in sorted(dump_dir.glob("*.xml.bz2")) + sorted(dump_dir.glob("*.xml.gz")):
            prefix = dump_file.name.split("-")[0]  # z.B. "dewiki", "enwikibooks"
            out_sub = OUTPUT_DIR / prefix
            n = process_wiki_dump(dump_file, out_sub)
            total += (n or 0)

    # 2. PDFs (Archive.org Bücher)
    books_dir = DATA_DIR / "books"
    if books_dir.exists():
        process_pdf_dir(books_dir, OUTPUT_DIR / "books_txt")

    # 3. Gutenberg .txt Dateien (brauchen kein Preprocessing, nur symlink/copy-check)
    gut_dir = DATA_DIR / "gutenberg"
    if gut_dir.exists():
        gut_out = OUTPUT_DIR / "gutenberg"
        if not gut_out.exists():
            log("📚 Erstelle Symlink für Gutenberg-Texte...")
            gut_out.symlink_to(gut_dir)
            log("✅ Gutenberg verlinkt.")

    # 4. Linux Docs (.rst/.txt brauchen kein Preprocessing)
    linux_dir = DATA_DIR / "linux-docs"
    if linux_dir.exists():
        linux_out = OUTPUT_DIR / "linux-docs"
        if not linux_out.exists():
            linux_out.symlink_to(linux_dir)
            log("✅ Linux-Docs verlinkt.")

    log(f"\n✨ Preprocessing abgeschlossen. {total} neue Artikel verarbeitet.")
    log(f"Verarbeitete Daten liegen in: {OUTPUT_DIR}")
    log("Starte jetzt die Indizierung mit 1_build_index.py")

if __name__ == "__main__":
    main()
