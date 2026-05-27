#!/usr/bin/env bash
# Doppelklick-Start – kein Terminal nötig (Desktop: Terminal=false)
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" "$ROOT/rag_gui.py"
