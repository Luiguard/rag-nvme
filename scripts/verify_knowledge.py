#!/usr/bin/env python3
"""Prüft lokale RAG-Wissensbasis und Ollama."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # noqa: F401

from rag_core.system_check import run_system_check


def main():
    print("=== RAG Wissensbasis – Systemcheck ===\n")
    result = run_system_check()
    for line in result["lines"]:
        print(line)
    if not result.get("ok"):
        print("\n⚠️  Starte die GUI: start-rag.sh")
        return 1
    print("\n✅ Bereit – start-rag.sh oder Assistent in der GUI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
