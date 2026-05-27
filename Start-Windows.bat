@echo off
chcp 65001 >nul 2>&1
title CustomRAG – Lokale KI-Plattform

echo.
echo ╔══════════════════════════════════════════╗
echo ║     🧠 CustomRAG – Lokale KI-Plattform   ║
echo ║         Installation für Windows          ║
echo ╚══════════════════════════════════════════╝
echo.

echo [1/4] Systemprüfung
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo   ❌ Python nicht gefunden!
    echo   Bitte installiere Python 3.10+ von https://python.org
    echo   WICHTIG: Haken bei "Add Python to PATH" setzen!
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PYVER=%%i
echo   ✅ Python %PYVER% gefunden

echo.
echo [2/4] GTK3 für Windows
python -c "import gi; gi.require_version('Gtk','3.0')" >nul 2>&1
if %errorlevel% neq 0 (
    echo   ⚠️  GTK3/PyGObject nicht gefunden.
    echo.
    echo   Empfohlene Installation via MSYS2:
    echo     1. MSYS2 installieren: https://www.msys2.org
    echo     2. In MSYS2-Terminal:
    echo        pacman -S mingw-w64-x86_64-gtk3 mingw-w64-x86_64-python-gobject
    echo.
    echo   Alternative: pip install PyGObject (erfordert GTK3-Runtime)
    echo   GTK3-Runtime: https://github.com/nickvdyck/gtk3-for-windows
    echo.
    pause
) else (
    echo   ✅ GTK3 + PyGObject installiert
)

echo.
echo [3/4] Python-Umgebung
if not exist ".venv" (
    echo   📦 Erstelle virtuelle Umgebung…
    python -m venv .venv
) else (
    echo   ✅ .venv existiert
)

echo   📦 Installiere Abhängigkeiten…
.venv\Scripts\pip install --quiet --upgrade pip
.venv\Scripts\pip install --quiet -r requirements.txt

echo.
echo [4/4] Ollama prüfen
where ollama >nul 2>&1
if %errorlevel% equ 0 (
    echo   ✅ Ollama installiert
) else (
    echo   ℹ️  Ollama nicht installiert (optional, für KI-Antworten^)
    echo      Download: https://ollama.ai/download/windows
)

echo.
echo ════════════════════════════════════════════
echo   ✅ Installation abgeschlossen!
echo ════════════════════════════════════════════
echo.
echo   Starte die Anwendung…
echo.

.venv\Scripts\python gui_custom_collector.py
pause
