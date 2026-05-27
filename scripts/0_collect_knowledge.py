import os
import time
import requests
import subprocess
from pathlib import Path
import wikipediaapi

DATA_DIR = Path(os.path.join(os.path.dirname(__file__), "../data")).resolve()

def init_dirs():
    for d in ["rfcs", "wikipedia", "stackoverflow", "github"]:
        (DATA_DIR / d).mkdir(parents=True, exist_ok=True)

def collect_wikipedia():
    print("\n--- Sammle Wikipedia IT-Wissen (langsam) ---")
    # Wikipedia API erfordert einen User-Agent
    wiki = wikipediaapi.Wikipedia(user_agent='IT-RAG-Collector/1.0 (benjamin@localhost)', language='de')
    
    # Eine Liste von Seed-Themen. Bei jedem Durchlauf laden wir diese und greifen später tiefer
    topics = [
        "Informatik", "Softwarearchitektur", "Python_(Programmiersprache)", 
        "Linux", "Künstliche_Intelligenz", "Netzwerkprotokoll", 
        "Maschinelles_Lernen", "Betriebssystem", "Datenbank", "Kryptographie"
    ]
    
    wiki_dir = DATA_DIR / "wikipedia"
    
    for topic in topics:
        file_path = wiki_dir / f"{topic.replace('/', '_')}.txt"
        if file_path.exists():
            continue # Überspringe bereits geladene
            
        page = wiki.page(topic)
        if page.exists():
            print(f"Lade Wiki-Artikel: {topic}...")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"# {page.title}\n\n{page.text}\n")
            time.sleep(2) # Rate Limiting respektieren

def collect_stackoverflow():
    print("\n--- Sammle StackOverflow Top-Antworten (langsam) ---")
    so_dir = DATA_DIR / "stackoverflow"
    tags = ["python", "javascript", "linux", "bash", "docker", "reactjs"]
    
    for tag in tags:
        file_path = so_dir / f"top_questions_{tag}.txt"
        if file_path.exists():
            continue
            
        print(f"Lade Top Fragen für Tag: {tag}...")
        # Lade die 20 am höchsten bewerteten Fragen inkl. Body
        url = f"https://api.stackexchange.com/2.3/questions?order=desc&sort=votes&tagged={tag}&site=stackoverflow&filter=withbody&pagesize=20"
        
        try:
            resp = requests.get(url)
            data = resp.json()
            if "items" in data:
                with open(file_path, "w", encoding="utf-8") as f:
                    for item in data["items"]:
                        f.write(f"Frage: {item.get('title', '')}\n")
                        f.write(f"Tags: {', '.join(item.get('tags', []))}\n")
                        f.write(f"Inhalt:\n{item.get('body', '')}\n\n")
                        f.write("="*80 + "\n\n")
            time.sleep(3) # API Limits einhalten
        except Exception as e:
            print(f"Fehler bei SO API: {e}")

def collect_rfcs():
    print("\n--- Synchronisiere RFC Archive (Inkrementell) ---")
    rfc_dir = DATA_DIR / "rfcs"
    print("Verwende Rsync, um RFCs im Hintergrund zu ziehen (lädt nur fehlende/neue).")
    try:
        # Lade nur .txt Dateien, ignoriere riesige PDFs und begrenze Timeout, damit es nicht ewig blockiert
        subprocess.run([
            "rsync", "-avz", "--timeout=30", "--max-size=500k", "--include=*.txt", "--exclude=*",
            "rsync.rfc-editor.org::rfcs-text-only", str(rfc_dir)
        ], stdout=subprocess.DEVNULL) # Wir verstecken den riesigen Output
        print("RFC-Sync abgeschlossen (teilweise oder komplett).")
    except Exception as e:
        print(f"RFC Sync übersprungen/fehlgeschlagen: {e}")

def main():
    print("Starte RAG Wissenskollektor...")
    init_dirs()
    
    collect_wikipedia()
    collect_stackoverflow()
    collect_rfcs()
    
    print("\nKollektor-Durchlauf beendet.")
    print("Führe 'python3 scripts/1_build_index.py' aus, um die neuen Daten in dein RAG einzubauen!")

if __name__ == "__main__":
    main()
