#!/bin/bash
set -e
cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║     🧠 CustomRAG – Lokale KI-Plattform   ║${NC}"
echo -e "${CYAN}${BOLD}║         Installation für Linux            ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

check_cmd() {
    if command -v "$1" &>/dev/null; then
        echo -e "  ${GREEN}✅${NC} $1 gefunden"
        return 0
    else
        echo -e "  ${RED}❌${NC} $1 fehlt"
        return 1
    fi
}

echo -e "${BOLD}[1/5] Systemprüfung${NC}"
check_cmd python3
check_cmd pip3 || check_cmd pip
check_cmd git

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
echo -e "  Python-Version: ${BOLD}${PYTHON_VER}${NC}"

echo ""
echo -e "${BOLD}[2/5] GTK3-Abhängigkeiten${NC}"
if python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null; then
    echo -e "  ${GREEN}✅${NC} GTK3 + PyGObject bereits installiert"
else
    echo -e "  ${BLUE}📦${NC} Installiere GTK3-Abhängigkeiten…"
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3-gi python3-gi-cairo gir1.2-gtk-3.0 libgirepository1.0-dev
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3-gobject gtk3 gobject-introspection-devel
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm python-gobject gtk3
    else
        echo -e "  ${RED}⚠️  Unbekannter Paketmanager. Bitte manuell installieren:${NC}"
        echo "     python3-gi python3-gi-cairo gir1.2-gtk-3.0"
        exit 1
    fi
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
        MODELS=$(curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || echo "?")
        echo -e "  ${GREEN}✅${NC} Ollama läuft · Modelle: ${BOLD}${MODELS}${NC}"
    else
        echo -e "  ${BLUE}ℹ️${NC}  Ollama installiert, aber nicht gestartet."
        echo -e "     Starte mit: ${BOLD}ollama serve${NC}"
    fi
else
    echo -e "  ${BLUE}ℹ️${NC}  Ollama nicht installiert (optional, für KI-Antworten)."
    echo -e "     Installiere: ${BOLD}curl -fsSL https://ollama.ai/install.sh | sh${NC}"
fi

echo ""
echo -e "${GREEN}${BOLD}════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✅ Installation abgeschlossen!${NC}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════${NC}"
echo ""

# Update-Check (optional, überspringt bei Fehler)
if [ -f "update_client.py" ]; then
    echo -e "${BOLD}[Update] Prüfe auf neue Version…${NC}"
    .venv/bin/python update_client.py --check 2>/dev/null && echo "" || echo -e "  ${BLUE}ℹ️${NC}  Kein Update-Server erreichbar – überspringe."
fi

echo -e "  Starte die Anwendung…"
echo ""

.venv/bin/python gui_custom_collector.py
