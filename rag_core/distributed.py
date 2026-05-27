"""Verteiltes RAG-Mesh: LAN-Discovery, Load-Monitoring, Arbeitsverteilung."""
from __future__ import annotations

import gc
import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable

import requests

from .config import BASE_DIR

MESH_UDP_PORT = int(os.environ.get("RAG_MESH_UDP_PORT", "9710"))
MESH_HTTP_PORT = int(os.environ.get("RAG_MESH_HTTP_PORT", "9711"))
MESH_BEACON_INTERVAL = int(os.environ.get("RAG_MESH_BEACON_SEC", "10"))
MESH_PEER_TIMEOUT = int(os.environ.get("RAG_MESH_PEER_TIMEOUT", "35"))
MESH_MAGIC = b"RAGMESH1"

CPU_IDLE_THRESHOLD = float(os.environ.get("RAG_MESH_CPU_IDLE", "25"))
GPU_IDLE_THRESHOLD = float(os.environ.get("RAG_MESH_GPU_IDLE", "20"))
RAM_IDLE_FLOOR_MB = int(os.environ.get("RAG_MESH_RAM_IDLE_MB", "3000"))

GAMING_PROCESSES = frozenset({
    "steam", "gamescope", "lutris", "wine-preloader", "wine64-preloader",
    "proton", "mangohud", "gamemode", "heroic", "bottles",
    "cs2", "dota2", "hl2_linux", "valheim", "factorio",
    "java",  # Minecraft
    "Xwayland",  # oft Gaming-Indikator bei Vollbild
})

HEAVY_WORKLOAD_PROCESSES = frozenset({
    "blender", "kdenlive", "davinci", "obs", "handbrake",
    "ffmpeg", "gcc", "g++", "rustc", "cargo", "make", "ninja",
    "webpack", "vite", "esbuild", "tsc",
})


@dataclass
class PeerInfo:
    host: str
    http_port: int
    node_id: str
    rag_type: str
    rows: int = 0
    cpu_pct: float = 100.0
    gpu_pct: float = 100.0
    ram_free_mb: int = 0
    available: bool = False
    last_seen: float = field(default_factory=time.monotonic)


def _get_node_id() -> str:
    try:
        base = platform.node() or socket.gethostname()
    except Exception:
        base = "unknown"
    return f"{base}_{MESH_HTTP_PORT}"


def _get_broadcast_ips() -> list[str]:
    results = []
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-j", "addr", "show"], timeout=3
        ).decode()
        for iface in json.loads(out):
            for info in iface.get("addr_info", []):
                brd = info.get("broadcast")
                if brd and not brd.startswith("127."):
                    results.append(brd)
    except Exception:
        results.append("255.255.255.255")
    return results or ["255.255.255.255"]


def _get_local_ips() -> set[str]:
    ips = {"127.0.0.1"}
    try:
        out = subprocess.check_output(
            ["hostname", "-I"], timeout=3
        ).decode().strip()
        ips.update(out.split())
    except Exception:
        pass
    return ips


class SystemMonitor:

    def __init__(self):
        self._gpu_cmd = shutil.which("nvidia-smi")
        self._proc_cache: list[str] = []
        self._proc_cache_ts = 0.0
        self._cached_status = {
            "cpu_pct": 0.0,
            "gpu_pct": 0.0,
            "ram_free_mb": 0,
            "gaming": False,
            "heavy_workload": False,
            "idle": True,
        }
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._monitor_loop, name="sys-monitor", daemon=True)
        self._thread.start()

    def _monitor_loop(self):
        while True:
            try:
                cpu = self._calc_cpu_percent()
                gpu = self._calc_gpu_percent()
                ram = self._calc_ram_free_mb()
                gaming = self._calc_is_gaming(gpu)
                heavy = self._calc_is_heavy_workload()
                idle = (
                    not gaming
                    and not heavy
                    and cpu < CPU_IDLE_THRESHOLD
                    and gpu < GPU_IDLE_THRESHOLD
                    and ram > RAM_IDLE_FLOOR_MB
                )
                with self._lock:
                    self._cached_status = {
                        "cpu_pct": cpu,
                        "gpu_pct": gpu,
                        "ram_free_mb": ram,
                        "gaming": gaming,
                        "heavy_workload": heavy,
                        "idle": idle,
                    }
            except Exception:
                pass
            time.sleep(4.0)

    def cpu_percent(self) -> float:
        with self._lock:
            return self._cached_status["cpu_pct"]

    def gpu_percent(self) -> float:
        with self._lock:
            return self._cached_status["gpu_pct"]

    def ram_free_mb(self) -> int:
        with self._lock:
            return self._cached_status["ram_free_mb"]

    def is_gaming(self) -> bool:
        with self._lock:
            return self._cached_status["gaming"]

    def is_heavy_workload(self) -> bool:
        with self._lock:
            return self._cached_status["heavy_workload"]

    def is_idle(self) -> bool:
        with self._lock:
            return self._cached_status["idle"]

    def status_dict(self) -> dict:
        with self._lock:
            return dict(self._cached_status)

    def _calc_cpu_percent(self) -> float:
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            parts = line.split()[1:]
            total = sum(int(x) for x in parts)
            idle = int(parts[3]) + int(parts[4])
            time.sleep(0.15)
            with open("/proc/stat") as f:
                line2 = f.readline()
            parts2 = line2.split()[1:]
            total2 = sum(int(x) for x in parts2)
            idle2 = int(parts2[3]) + int(parts2[4])
            dt = total2 - total
            di = idle2 - idle
            if dt == 0:
                return 0.0
            return round((1 - di / dt) * 100, 1)
        except Exception:
            return 100.0

    def _calc_gpu_percent(self) -> float:
        if not self._gpu_cmd:
            return 0.0
        try:
            out = subprocess.check_output(
                [self._gpu_cmd, "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                timeout=3,
            ).decode().strip()
            vals = [float(x.strip()) for x in out.split("\n") if x.strip()]
            return max(vals) if vals else 0.0
        except Exception:
            return 0.0

    def _calc_ram_free_mb(self) -> int:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024
        except Exception:
            pass
        return 0

    def _refresh_procs(self) -> list[str]:
        now = time.monotonic()
        if now - self._proc_cache_ts < 5:
            return self._proc_cache
        try:
            out = subprocess.check_output(
                ["ps", "-eo", "comm", "--no-headers"], timeout=3
            ).decode()
            self._proc_cache = [l.strip().lower() for l in out.splitlines() if l.strip()]
        except Exception:
            self._proc_cache = []
        self._proc_cache_ts = now
        return self._proc_cache

    def _calc_is_gaming(self, gpu_pct: float) -> bool:
        procs = set(self._refresh_procs())
        gaming_hits = procs & GAMING_PROCESSES
        if not gaming_hits:
            return False
        if gaming_hits == {"java"}:
            return gpu_pct > 40
        if gaming_hits == {"Xwayland"}:
            return gpu_pct > 60
        return True

    def _calc_is_heavy_workload(self) -> bool:
        procs = set(self._refresh_procs())
        return bool(procs & HEAVY_WORKLOAD_PROCESSES)


_monitor = SystemMonitor()


class MeshDiscovery:

    def __init__(self, rag_type: str = "custom", row_count_fn: Callable[[], int] | None = None):
        self.rag_type = rag_type
        self.node_id = _get_node_id()
        self._row_count_fn = row_count_fn or (lambda: 0)
        self._peers: dict[str, PeerInfo] = {}
        self._lock = threading.Lock()
        self._local_ips = _get_local_ips()
        self._running = False
        self._threads: list[threading.Thread] = []

    def start(self):
        if self._running:
            return
        self._running = True
        for target, name in [
            (self._beacon_loop, "mesh-beacon"),
            (self._listen_loop, "mesh-listen"),
            (self._cleanup_loop, "mesh-cleanup"),
        ]:
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._running = False

    def get_idle_peers(self) -> list[PeerInfo]:
        with self._lock:
            now = time.monotonic()
            return [
                p for p in self._peers.values()
                if p.available and (now - p.last_seen) < MESH_PEER_TIMEOUT
            ]

    def get_all_peers(self) -> list[PeerInfo]:
        with self._lock:
            return list(self._peers.values())

    def _build_beacon(self) -> bytes:
        status = _monitor.status_dict()
        payload = {
            "node_id": self.node_id,
            "rag_type": self.rag_type,
            "http_port": MESH_HTTP_PORT,
            "rows": self._row_count_fn(),
            "cpu_pct": status["cpu_pct"],
            "gpu_pct": status["gpu_pct"],
            "ram_free_mb": status["ram_free_mb"],
            "available": status["idle"],
        }
        return MESH_MAGIC + json.dumps(payload).encode()

    def _beacon_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(2)
        bcast_ips = _get_broadcast_ips()
        while self._running:
            try:
                data = self._build_beacon()
                for bip in bcast_ips:
                    sock.sendto(data, (bip, MESH_UDP_PORT))
            except Exception:
                pass
            time.sleep(MESH_BEACON_INTERVAL)
        sock.close()

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        sock.bind(("0.0.0.0", MESH_UDP_PORT))
        sock.settimeout(3)
        while self._running:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                continue
            if not data.startswith(MESH_MAGIC):
                continue
            try:
                payload = json.loads(data[len(MESH_MAGIC):])
            except Exception:
                continue
            host = addr[0]
            if host in self._local_ips and payload.get("node_id") == self.node_id:
                continue
            peer = PeerInfo(
                host=host,
                http_port=payload.get("http_port", MESH_HTTP_PORT),
                node_id=payload.get("node_id", "?"),
                rag_type=payload.get("rag_type", "?"),
                rows=payload.get("rows", 0),
                cpu_pct=payload.get("cpu_pct", 100),
                gpu_pct=payload.get("gpu_pct", 100),
                ram_free_mb=payload.get("ram_free_mb", 0),
                available=payload.get("available", False),
                last_seen=time.monotonic(),
            )
            with self._lock:
                self._peers[f"{host}:{peer.http_port}"] = peer

    def _cleanup_loop(self):
        while self._running:
            time.sleep(MESH_PEER_TIMEOUT)
            now = time.monotonic()
            with self._lock:
                expired = [
                    k for k, p in self._peers.items()
                    if (now - p.last_seen) > MESH_PEER_TIMEOUT
                ]
                for k in expired:
                    del self._peers[k]


class MeshWorkerHandler(BaseHTTPRequestHandler):

    retriever = None

    def log_message(self, fmt, *args):
        pass

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/mesh/health":
            status = _monitor.status_dict()
            self._json(200, {
                "node_id": _get_node_id(),
                "status": status,
            })
        elif self.path == "/mesh/status":
            self._json(200, _monitor.status_dict())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/mesh/search":
            self._handle_search()
        elif self.path == "/mesh/embed":
            self._handle_embed()
        else:
            self._json(404, {"error": "not found"})

    def _handle_search(self):
        if not _monitor.is_idle():
            self._json(503, {"error": "node busy", "reason": "not idle"})
            return
        if not self.retriever or not self.retriever.ready:
            self._json(503, {"error": "no index"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        query = body.get("query", "").strip()
        if not query:
            self._json(400, {"error": "query required"})
            return
        top_k = body.get("top_k", 8)
        hits = self.retriever.search(query, top_k=top_k)
        results = [
            {
                "text": h.get("text", ""),
                "source": h.get("source", ""),
                "distance": float(h.get("_distance", 999)),
            }
            for h in hits
        ]
        gc.collect()
        self._json(200, {"results": results, "count": len(results), "node": _get_node_id()})

    def _handle_embed(self):
        if not _monitor.is_idle():
            self._json(503, {"error": "node busy"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        texts = body.get("texts", [])
        if not texts or len(texts) > 256:
            self._json(400, {"error": "texts required (max 256)"})
            return
        from .embeddings import embed_documents
        vectors = embed_documents(texts)
        gc.collect()
        self._json(200, {"vectors": vectors, "count": len(vectors or []), "node": _get_node_id()})


class MeshNode:

    def __init__(self, rag_type: str = "custom", retriever=None, row_count_fn=None):
        self.rag_type = rag_type
        self.retriever = retriever
        self._discovery = MeshDiscovery(
            rag_type=rag_type,
            row_count_fn=row_count_fn or (lambda: retriever.row_count() if retriever and retriever.ready else 0),
        )
        self._server: HTTPServer | None = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._discovery.start()
        MeshWorkerHandler.retriever = self.retriever
        self._server = HTTPServer(("0.0.0.0", MESH_HTTP_PORT), MeshWorkerHandler)
        t = threading.Thread(target=self._server.serve_forever, name="mesh-http", daemon=True)
        t.start()

    def stop(self):
        self._running = False
        self._discovery.stop()
        if self._server:
            self._server.shutdown()

    @property
    def node_id(self) -> str:
        return self._discovery.node_id

    def get_idle_peers(self) -> list[PeerInfo]:
        return self._discovery.get_idle_peers()

    def get_all_peers(self) -> list[PeerInfo]:
        return self._discovery.get_all_peers()

    def is_local_idle(self) -> bool:
        return _monitor.is_idle()

    def local_status(self) -> dict:
        return _monitor.status_dict()

    def remote_search(self, query: str, top_k: int = 8, rag_type: str | None = None) -> list[dict] | None:
        peers = self.get_idle_peers()
        if rag_type:
            peers = [p for p in peers if p.rag_type == rag_type]
        peers.sort(key=lambda p: p.cpu_pct)
        for peer in peers:
            try:
                r = requests.post(
                    f"http://{peer.host}:{peer.http_port}/mesh/search",
                    json={"query": query, "top_k": top_k},
                    timeout=15,
                )
                if r.status_code == 200:
                    data = r.json()
                    return data.get("results", [])
            except Exception:
                continue
        return None

    def remote_embed(self, texts: list[str]) -> list[list[float]] | None:
        peers = self.get_idle_peers()
        peers.sort(key=lambda p: p.ram_free_mb, reverse=True)
        for peer in peers:
            try:
                r = requests.post(
                    f"http://{peer.host}:{peer.http_port}/mesh/embed",
                    json={"texts": texts},
                    timeout=30,
                )
                if r.status_code == 200:
                    return r.json().get("vectors")
            except Exception:
                continue
        return None

    def distributed_search(self, query: str, top_k: int = 8) -> list[dict]:
        local_hits = []
        if self.retriever and self.retriever.ready:
            local_hits = self.retriever.search(query, top_k=top_k)
            local_hits = [
                {
                    "text": h.get("text", ""),
                    "source": h.get("source", ""),
                    "distance": float(h.get("_distance", 999)),
                    "node": self.node_id,
                }
                for h in local_hits
            ]

        remote_hits = []
        for peer in self.get_idle_peers():
            try:
                r = requests.post(
                    f"http://{peer.host}:{peer.http_port}/mesh/search",
                    json={"query": query, "top_k": top_k},
                    timeout=15,
                )
                if r.status_code == 200:
                    data = r.json()
                    for h in data.get("results", []):
                        h["node"] = data.get("node", peer.node_id)
                        remote_hits.append(h)
            except Exception:
                continue

        merged = local_hits + remote_hits
        merged.sort(key=lambda h: h.get("distance", 999))
        seen = set()
        deduped = []
        for h in merged:
            key = (h.get("source", ""), (h.get("text", "") or "")[:120])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(h)
        return deduped[:top_k]

    def mesh_summary(self) -> str:
        peers = self.get_all_peers()
        lines = [f"🌐 Mesh-Node: {self.node_id} ({self.rag_type})"]
        status = _monitor.status_dict()
        lines.append(
            f"   Lokal: CPU {status['cpu_pct']:.0f}% | GPU {status['gpu_pct']:.0f}% | "
            f"RAM frei {status['ram_free_mb']} MB | "
            f"{'🎮 Gaming' if status['gaming'] else '🟢 Idle' if status['idle'] else '🔴 Busy'}"
        )
        if peers:
            lines.append(f"   {len(peers)} Peer(s) im Netzwerk:")
            for p in peers:
                state = "🟢" if p.available else "🔴"
                lines.append(
                    f"     {state} {p.node_id} ({p.host}) | {p.rag_type} | "
                    f"{p.rows:,} Chunks | CPU {p.cpu_pct:.0f}%"
                )
        else:
            lines.append("   Keine Peers gefunden.")
        return "\n".join(lines)


_mesh_node: MeshNode | None = None


def get_mesh_node(rag_type: str = "custom", retriever=None) -> MeshNode:
    global _mesh_node
    if _mesh_node is None:
        _mesh_node = MeshNode(rag_type=rag_type, retriever=retriever)
        _mesh_node.start()
    return _mesh_node
