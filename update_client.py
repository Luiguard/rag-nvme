#!/usr/bin/env python3
"""Auto-Updater-Client: Prüft auf neue Versionen und installiert Updates."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
VERSION_FILE = BASE_DIR / "version.json"

UPDATE_SERVER = os.environ.get(
    "RAG_UPDATE_SERVER", "http://192.168.68.100:9720"
)


def current_version() -> str:
    if VERSION_FILE.exists():
        return json.loads(VERSION_FILE.read_text()).get("version", "0.0.0")
    return "0.0.0"


def check_for_update(server: str = UPDATE_SERVER) -> dict | None:
    try:
        r = requests.post(
            f"{server}/check",
            json={"current_version": current_version()},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("update_available"):
                return data
        return None
    except Exception as e:
        print(f"⚠️ Update-Check fehlgeschlagen: {e}", file=sys.stderr)
        return None


def download_update(url: str, expected_sha256: str) -> Path | None:
    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / "update.zip"
    try:
        print(f"⬇️  Lade Update herunter…")
        r = requests.get(url, stream=True, timeout=300)
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = downloaded * 100 // total
                    print(f"\r   {pct}% ({downloaded // 1024 // 1024} MB)", end="", flush=True)
        print()

        sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        if sha != expected_sha256:
            print(f"❌ SHA256-Mismatch!\n   Erwartet: {expected_sha256}\n   Erhalten: {sha}")
            return None
        print(f"✅ SHA256 verifiziert")
        return zip_path
    except Exception as e:
        print(f"❌ Download fehlgeschlagen: {e}")
        return None


def apply_update(zip_path: Path) -> bool:
    backup_dir = BASE_DIR / ".update_backup"
    try:
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.mkdir()

        update_files = [
            "rag_core",
            "scripts",
            "gui_chat.py",
            "gui_collector.py",
            "gui_custom_collector.py",
            "rag_gui.py",
            "rag_server.py",
            "update_server.py",
            "update_client.py",
            "requirements.txt",
            "version.json",
            "Start-Linux.sh",
            "Start-Mac.command",
            "Start-Windows.bat",
            "index.html",
        ]

        for name in update_files:
            src = BASE_DIR / name
            if src.exists():
                dst = backup_dir / name
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

        with zipfile.ZipFile(zip_path) as zf:
            tmp_extract = Path(tempfile.mkdtemp())
            zf.extractall(tmp_extract)
            extracted_dirs = list(tmp_extract.iterdir())
            if len(extracted_dirs) == 1 and extracted_dirs[0].is_dir():
                src_root = extracted_dirs[0]
            else:
                src_root = tmp_extract

            for item in src_root.iterdir():
                dst = BASE_DIR / item.name
                if item.name in (".venv", "data", "lancedb_index", "models",
                                  "processed", "downloads", "dist", "index",
                                  "collector_state.json", ".knowledge_size_cache.json"):
                    continue
                if dst.exists():
                    if dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                if item.is_dir():
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)

            shutil.rmtree(tmp_extract)

        print(f"✅ Update angewendet auf v{current_version()}")
        print(f"   Backup in: {backup_dir}")
        return True

    except Exception as e:
        print(f"❌ Update fehlgeschlagen: {e}")
        print(f"   Stelle Backup wieder her…")
        try:
            for item in backup_dir.iterdir():
                dst = BASE_DIR / item.name
                if dst.exists():
                    if dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                if item.is_dir():
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)
            print(f"✅ Backup wiederhergestellt")
        except Exception as e2:
            print(f"❌ Backup-Restore fehlgeschlagen: {e2}")
        return False


def auto_update(server: str = UPDATE_SERVER, interactive: bool = True) -> bool:
    print(f"🔄 CustomRAG v{current_version()} — Prüfe auf Updates…")
    update = check_for_update(server)
    if not update:
        print("✅ Bereits die neueste Version.")
        return False

    print(f"\n📢 Update verfügbar: v{update['latest_version']}")
    if update.get("changelog"):
        print("   Änderungen:")
        for entry in update["changelog"]:
            print(f"     • {entry}")

    if interactive:
        answer = input("\n   Jetzt installieren? [j/N] ").strip().lower()
        if answer not in ("j", "ja", "y", "yes"):
            print("   Übersprungen.")
            return False

    url = update.get("download_url")
    sha = update.get("sha256")
    if not url or not sha:
        print("❌ Keine Download-URL oder SHA256 im Update-Response.")
        return False

    zip_path = download_update(url, sha)
    if not zip_path:
        return False

    success = apply_update(zip_path)
    shutil.rmtree(zip_path.parent, ignore_errors=True)
    return success


def main():
    server = sys.argv[1] if len(sys.argv) > 1 else UPDATE_SERVER
    if "--check" in sys.argv:
        update = check_for_update(server)
        if update:
            print(json.dumps(update, indent=2, ensure_ascii=False))
        else:
            print("Keine Updates verfügbar.")
        return

    auto_update(server)


if __name__ == "__main__":
    main()
