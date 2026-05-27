#!/usr/bin/env bash
# Optional: für Skripte/CI. Standard-Nutzung: start-rag.sh oder Desktop-Verknüpfung.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="${ROOT}/.venv/bin/python"

case "${1:-gui}" in
  gui|"")   exec "${PY}" "${ROOT}/rag_gui.py" ;;
  hub)      exec "${PY}" "${ROOT}/rag_gui.py" ;;
  collector) exec "${PY}" "${ROOT}/gui_collector.py" ;;
  chat)     exec "${PY}" "${ROOT}/gui_chat.py" ;;
  chat-tui) exec "${PY}" "${ROOT}/scripts/2_query_rag.py" ;;
  check)    exec "${PY}" "${ROOT}/scripts/verify_knowledge.py" ;;
  prime)    exec "${PY}" "${ROOT}/scripts/build_prime_index.py" "${@:2}" ;;
  projects) exec "${PY}" "${ROOT}/scripts/index_my_projects.py" ;;
  download) exec "${PY}" "${ROOT}/scripts/download_all_sources.py" "${@:2}" ;;
  se)       exec "${PY}" "${ROOT}/scripts/ingest_stackexchange.py" ;;
  index)    exec "${PY}" "${ROOT}/scripts/1_build_index.py" ;;
  reset)    exec "${PY}" "${ROOT}/scripts/reset_wiki_queue.py" ;;
  *)
    echo "Nutze die GUI: ./start-rag.sh  oder  IT-KI.start.desktop"
    echo "Optional: $0 {gui|collector|chat|check|prime|projects|download|se|chat-tui}"
    exit 1
    ;;
esac
