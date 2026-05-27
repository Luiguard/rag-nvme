#!/bin/bash
set -e
cd "$(dirname "$0")"

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║     🧠 CustomRAG – Lokale KI-Plattform   ║${NC}"
echo -e "${CYAN}${BOLD}║         Installation für macOS            ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

echo -e "${BOLD}[1/5] Systemprüfung${NC}"
if ! command -v python3 &>/dev/null; then
    echo -e "  ${RED}❌${NC} Python3 nicht gefunden."
    echo -e "     ${BOLD}brew install python3${NC}  oder  https://python.org"
    exit 1
fi
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "  ${GREEN}✅${NC} Python ${BOLD}${PYVER}${NC}"

echo ""
echo -e "${BOLD}[2/5] Homebrew + GTK3${NC}"
if ! command -v brew &>/dev/null; then
    echo -e "  ${RED}❌${NC} Homebrew nicht gefunden."
    echo -e "     Installiere: ${BOLD}/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"${NC}"
    exit 1
fi
echo -e "  ${GREEN}✅${NC} Homebrew vorhanden"

if python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null; then
    echo -e "  ${GREEN}✅${NC} GTK3 + PyGObject installiert"
else
    echo -e "  ${BLUE}📦${NC} Installiere GTK3…"
    brew install pygobject3 gtk+3 adwaita-icon-theme 2>/dev/null || true
fi

echo ""
echo -e "${BOLD}[3/5] Python-Umgebung${NC}"
if [ ! -d ".venv" ]; then
    echo -e "  ${BLUE}📦${NC} Erstelle virtuelle Umgebung…"
    python3 -m venv --system-site-packages .venv
else
    echo -e "  ${GREEN}✅${NC} .venv existiert"
fi

echo ""
echo -e "${BOLD}[4/5] Python-Pakete${NC}"
echo -e "  ${BLUE}📦${NC} Installiere Abhängigkeiten…"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo ""
echo -e "${BOLD}[5/5] Ollama prüfen${NC}"
if command -v ollama &>/dev/null; then
    echo -e "  ${GREEN}✅${NC} Ollama installiert"
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        echo -e "  ${GREEN}✅${NC} Ollama läuft"
    else
        echo -e "  ${BLUE}ℹ️${NC}  Starte mit: ${BOLD}ollama serve${NC}"
    fi
else
    echo -e "  ${BLUE}ℹ️${NC}  Ollama nicht installiert (optional)"
    echo -e "     Download: ${BOLD}https://ollama.ai/download/mac${NC}"
fi

echo ""
echo -e "${GREEN}${BOLD}════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✅ Installation abgeschlossen!${NC}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════${NC}"
echo ""

.venv/bin/python gui_custom_collector.py
