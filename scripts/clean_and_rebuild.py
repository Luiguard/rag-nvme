#!/usr/bin/env python3
"""
Bereinigt alle alten Datenbanken, heruntergeladenen Daten und States.
Baut den Prime-Index von Grund auf neu auf.
"""
import os
import sys
import shutil
import json
from pathlib import Path
import subprocess

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
STATE_FILE = BASE_DIR / "collector_state.json"
MIGRATION_FILE = BASE_DIR / "nvme_migration_state.json"
SIZE_CACHE = BASE_DIR / ".knowledge_size_cache.json"

def log(msg: str):
    print(f"🧹 [Rebuild] {msg}", flush=True)

def main():
    log("Starte vollständige Bereinigung und Neuaufbau...")

    # 1. Sicherstellen, dass wir die data_dir aus dem alten State retten, falls vorhanden
    saved_data_dir = ""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    saved_data_dir = saved.get("data_dir", "")
                    if saved_data_dir:
                        log(f"Gefundener benutzerdefinierter Speicherpfad: {saved_data_dir}")
        except Exception as e:
            log(f"Warnung beim Lesen von collector_state.json: {e}")

    # Bestimme effektive DATA_DIR
    data_dir_path = Path(saved_data_dir) if saved_data_dir else BASE_DIR / "data"
    lancedb_path = BASE_DIR / "lancedb_index"
    nvme_file = BASE_DIR / "nvme_knowledge.dat"

    # 2. Lösche alte Datenbanken und heruntergeladene Daten
    log(f"Lösche Datenverzeichnis: {data_dir_path}")
    if data_dir_path.exists():
        try:
            shutil.rmtree(data_dir_path)
            log("Datenverzeichnis erfolgreich gelöscht.")
        except Exception as e:
            log(f"Fehler beim Löschen des Datenverzeichnisses: {e}")

    log(f"Lösche LanceDB Index: {lancedb_path}")
    if lancedb_path.exists():
        try:
            shutil.rmtree(lancedb_path)
            log("LanceDB Index erfolgreich gelöscht.")
        except Exception as e:
            log(f"Fehler beim Löschen von LanceDB Index: {e}")

    log(f"Lösche kompilierte NVMe-Datenbank: {nvme_file}")
    if nvme_file.exists():
        try:
            nvme_file.unlink()
            log("NVMe-Datenbank erfolgreich gelöscht.")
        except Exception as e:
            log(f"Fehler beim Löschen der NVMe-Datenbank: {e}")

    log("Lösche Größe-Cache...")
    if SIZE_CACHE.exists():
        try:
            SIZE_CACHE.unlink()
        except Exception:
            pass

    # 3. States zurücksetzen
    log("Setze collector_state.json zurück...")
    try:
        from rag_core.quality import get_current_domains
        from rag_core.domains import DOMAIN_IT
        domains = get_current_domains()
        seeds = []
        seen = set()
        for d in domains:
            for t in d.wiki_seed_topics:
                if t not in seen:
                    seen.add(t)
                    seeds.append(t)
        if not seeds:
            seeds = list(DOMAIN_IT.wiki_seed_topics)
    except Exception:
        seeds = [
            "Informatik", "Softwarearchitektur", "Python_(Programmiersprache)",
            "Linux", "Künstliche_Intelligenz", "Netzwerkprotokoll",
            "Maschinelles_Lernen", "Betriebssystem", "Datenbank", "Kryptographie",
        ]

    so_tags = [
        "python", "javascript", "linux", "bash", "docker", "reactjs", "c++",
        "java", "sql", "git", "php", "c#", "ruby", "rust", "swift", "go",
        "kotlin", "typescript", "kubernetes", "aws", "terraform", "ansible",
        "nginx", "mongodb", "postgresql", "redis", "elasticsearch", "graphql",
        "rest-api", "microservices", "security", "devops", "machine-learning",
        "tensorflow", "pytorch", "pandas", "numpy", "django", "flask",
        "spring-boot", "node.js", "express", "vue.js", "angular", "tailwind-css",
        "security", "authentication", "authorization", "encryption", "owasp",
        "xss", "sql-injection", "csrf", "jwt", "oauth-2.0", "penetration-testing",
    ]

    new_state = {
        "wiki_queue": seeds.copy(),
        "wiki_done": [],
        "so_progress": {tag: 1 for tag in so_tags},
        "man_done": [],
        "mdn_done": False,
        "so_key": "",
        "processed_dumps": [],
        "processed_se": [],
        "data_dir": saved_data_dir,
    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(new_state, f, ensure_ascii=False)
    log("collector_state.json erfolgreich zurückgesetzt.")

    log("Lösche nvme_migration_state.json...")
    if MIGRATION_FILE.exists():
        try:
            MIGRATION_FILE.unlink()
            log("nvme_migration_state.json erfolgreich gelöscht.")
        except Exception as e:
            log(f"Fehler beim Löschen von nvme_migration_state.json: {e}")

    # 4. Verzeichnisse neu erstellen
    log("Erstelle Verzeichnisse neu...")
    data_dir_path.mkdir(parents=True, exist_ok=True)
    lancedb_path.mkdir(parents=True, exist_ok=True)

    # 5. Prime-Index von Grund auf neu aufbauen
    venv_python = BASE_DIR / ".venv" / "bin" / "python"
    build_script = SCRIPTS_DIR / "build_prime_index.py"

    if not build_script.exists():
        log(f"Fehler: {build_script} nicht gefunden!")
        return 1

    log("Starte build_prime_index.py --fresh...")
    try:
        # Führe build_prime_index.py --fresh aus
        result = subprocess.run(
            [str(venv_python), str(build_script), "--fresh"],
            check=True
        )
        if result.returncode == 0:
            log("Prime-Index erfolgreich von Grund auf neu aufgebaut!")
        else:
            log(f"Fehler beim Aufbauen des Prime-Index. Code: {result.returncode}")
            return result.returncode
    except Exception as e:
        log(f"Kritischer Fehler bei der Index-Erstellung: {e}")
        return 1

    log("Vollständige Bereinigung und Neuaufbau erfolgreich abgeschlossen.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
