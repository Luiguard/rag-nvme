#!/usr/bin/env python3
"""Update-Server für CustomRAG – verteilt Versionen an alle Clients."""
import hashlib
import json
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
VERSION_FILE = BASE_DIR / "version.json"
UPDATE_PORT = int(os.environ.get("RAG_UPDATE_PORT", "9720"))


def _load_version() -> dict:
    if VERSION_FILE.exists():
        return json.loads(VERSION_FILE.read_text())
    return {"version": "0.0.0", "date": "unknown"}


def _get_zip_info() -> dict | None:
    zips = sorted(DIST_DIR.glob("CustomRAG-*.zip"), reverse=True)
    if not zips:
        legacy = BASE_DIR / "downloads" / "CustomRAG.zip"
        if legacy.exists():
            zips = [legacy]
    if not zips:
        return None
    latest = zips[0]
    sha256 = hashlib.sha256(latest.read_bytes()).hexdigest()
    return {
        "filename": latest.name,
        "size": latest.stat().st_size,
        "sha256": sha256,
        "path": str(latest),
    }


class UpdateHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {args[0] if args else fmt}")

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/version":
            self._handle_version()
        elif self.path == "/check":
            self._handle_check()
        elif self.path.startswith("/download"):
            self._handle_download()
        elif self.path == "/health":
            self._json(200, {"status": "ok", "service": "rag-update-server"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/check":
            self._handle_check_post()
        else:
            self._json(404, {"error": "not found"})

    def _handle_version(self):
        ver = _load_version()
        zip_info = _get_zip_info()
        resp = {
            "version": ver.get("version", "0.0.0"),
            "codename": ver.get("codename", ""),
            "date": ver.get("date", ""),
            "changelog": ver.get("changelog", []),
        }
        if zip_info:
            resp["download"] = {
                "filename": zip_info["filename"],
                "size": zip_info["size"],
                "sha256": zip_info["sha256"],
                "url": f"http://{self.headers.get('Host', 'localhost')}/download",
            }
        self._json(200, resp)

    def _handle_check(self):
        client_ver = self.headers.get("X-Current-Version", "0.0.0")
        self._do_check(client_ver)

    def _handle_check_post(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        client_ver = body.get("current_version", "0.0.0")
        self._do_check(client_ver)

    def _do_check(self, client_ver: str):
        server_ver = _load_version().get("version", "0.0.0")
        update_available = self._compare_versions(client_ver, server_ver)
        resp = {
            "current_version": client_ver,
            "latest_version": server_ver,
            "update_available": update_available,
        }
        if update_available:
            ver = _load_version()
            resp["changelog"] = ver.get("changelog", [])
            zip_info = _get_zip_info()
            if zip_info:
                resp["download_url"] = f"http://{self.headers.get('Host', 'localhost')}/download"
                resp["sha256"] = zip_info["sha256"]
                resp["size"] = zip_info["size"]
        self._json(200, resp)

    def _handle_download(self):
        zip_info = _get_zip_info()
        if not zip_info:
            self._json(404, {"error": "no distribution available"})
            return
        filepath = Path(zip_info["path"])
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(zip_info["size"]))
        self.send_header("Content-Disposition", f'attachment; filename="{zip_info["filename"]}"')
        self.send_header("X-SHA256", zip_info["sha256"])
        self.end_headers()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)

    @staticmethod
    def _compare_versions(client: str, server: str) -> bool:
        def parse(v):
            return tuple(int(x) for x in v.split(".")[:3]) if v else (0, 0, 0)
        return parse(server) > parse(client)


def main():
    DIST_DIR.mkdir(exist_ok=True)
    ver = _load_version()
    print(f"🔄 RAG Update-Server v{ver.get('version', '?')}")
    print(f"   Port: {UPDATE_PORT}")
    print(f"   Dist: {DIST_DIR}")
    zip_info = _get_zip_info()
    if zip_info:
        print(f"   ZIP:  {zip_info['filename']} ({zip_info['size'] // 1024 // 1024} MB)")
        print(f"   SHA:  {zip_info['sha256'][:16]}…")
    else:
        print("   ⚠️  Keine ZIP gefunden — erst build_release.sh ausführen")
    print()
    print("   Endpoints:")
    print(f"   GET  /version   — Aktuelle Version + Changelog")
    print(f"   POST /check     — {{\"current_version\": \"1.0.0\"}} → Update-Check")
    print(f"   GET  /download  — ZIP herunterladen")
    print(f"   GET  /health    — Server-Status")

    server = HTTPServer(("0.0.0.0", UPDATE_PORT), UpdateHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
