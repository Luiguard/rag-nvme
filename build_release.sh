#!/bin/bash
set -e
cd "$(dirname "$0")"

VERSION=$(python3 -c "import json; print(json.load(open('version.json'))['version'])")
DATE=$(date +%Y-%m-%d)
DIST_DIR="$(pwd)/dist"
ZIP_NAME="CustomRAG-${VERSION}.zip"

echo "📦 CustomRAG Release v${VERSION} bauen…"
echo ""

mkdir -p "$DIST_DIR"

STAGING=$(mktemp -d)
DEST="$STAGING/rag-custom-knowledge"
mkdir -p "$DEST"

# Code-Dateien
cp -r rag_core "$DEST/"
cp -r scripts "$DEST/"
cp gui_chat.py gui_collector.py gui_custom_collector.py rag_gui.py "$DEST/"
cp rag_server.py update_server.py "$DEST/"
cp requirements.txt version.json "$DEST/"
cp Start-Linux.sh Start-Mac.command Start-Windows.bat "$DEST/"
cp index.html "$DEST/"

# Updater-Client
cp update_client.py "$DEST/" 2>/dev/null || true

# Bereinigen
find "$DEST" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$DEST" -name "*.pyc" -delete 2>/dev/null || true
find "$DEST" -name ".DS_Store" -delete 2>/dev/null || true

# ZIP erstellen
cd "$STAGING"
zip -r -9 "$ZIP_NAME" rag-custom-knowledge/
mv "$ZIP_NAME" "$DIST_DIR/"

# Cleanup
cd - >/dev/null
rm -rf "$STAGING"

SIZE=$(du -sh "$DIST_DIR/$ZIP_NAME" | cut -f1)
SHA=$(sha256sum "$DIST_DIR/$ZIP_NAME" | cut -d' ' -f1)

# Auch in downloads/ für die Landing Page
mkdir -p downloads
cp "$DIST_DIR/$ZIP_NAME" downloads/CustomRAG.zip

echo ""
echo "✅ Release erstellt:"
echo "   $DIST_DIR/$ZIP_NAME"
echo "   Größe: $SIZE"
echo "   SHA256: $SHA"
echo ""
echo "   Auch kopiert nach: downloads/CustomRAG.zip"
echo ""
echo "🔄 Update-Server starten:"
echo "   python3 update_server.py"
