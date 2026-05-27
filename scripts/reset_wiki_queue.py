#!/usr/bin/env python3
"""Setzt Wikipedia-Warteschlange auf reine IT-Startthemen zurück."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / "collector_state.json"

IT_TOPICS = [
    "Informatik", "Softwarearchitektur", "Python_(Programmiersprache)",
    "Linux", "Künstliche_Intelligenz", "Netzwerkprotokoll",
    "Maschinelles_Lernen", "Betriebssystem", "Datenbank", "Kryptographie",
    "C++", "Java_(Programmiersprache)", "JavaScript", "HTML", "CSS",
    "Docker_(Software)", "Kubernetes", "Git", "Agile_Softwareentwicklung",
    "Algorithmus", "Datenstruktur", "Compiler", "API", "REST", "GraphQL",
    "Microservices", "Cloud-Computing", "Cybersecurity", "DevOps",
    "Nginx", "PostgreSQL", "Redis_(Software)", "Elasticsearch",
    "TensorFlow", "PyTorch", "React_(JavaScript-Bibliothek)",
    "Betriebssystemkern", "Virtualisierung", "TCP/IP",
]


def main():
    data = {}
    if STATE.exists():
        with open(STATE, encoding="utf-8") as f:
            data = json.load(f)
    data["wiki_queue"] = IT_TOPICS.copy()
    # Datenschutz-Artikel aus done entfernen (optional behalten)
    done = data.get("wiki_done", [])
    data["wiki_done"] = [t for t in done if "Datenschutz" not in t and "daten" not in t.lower()[:8]]
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ Wiki-Queue zurückgesetzt: {len(IT_TOPICS)} IT-Themen")
    print(f"   State: {STATE}")


if __name__ == "__main__":
    main()
