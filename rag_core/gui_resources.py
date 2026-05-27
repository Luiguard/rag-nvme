"""Ressourcen-Limits für GUI und Hintergrund-Jobs (Kerne frei lassen)."""
from __future__ import annotations

import gc
import os
import shutil
import time
from typing import Sequence

CPU_COUNT = os.cpu_count() or 4
# Mindestens 2 Kerne für Desktop / System frei (anpassbar: RAG_RESERVE_CORES=3)
RESERVE_CORES = max(1, min(CPU_COUNT - 1, int(os.environ.get("RAG_RESERVE_CORES", "2"))))
WORKER_THREADS = max(1, CPU_COUNT - RESERVE_CORES)
NUM_THREADS = str(WORKER_THREADS)

# Max. gleichzeitige schwere Hintergrund-Tasks (Git, rsync, Gutenberg, …)
MAX_BG_TASKS = max(1, int(os.environ.get("RAG_MAX_BG_TASKS", "2")))

# Parallele Wikimedia-wget (1 = schonender)
MAX_WIKI_DOWNLOADS = max(1, int(os.environ.get("RAG_MAX_WIKI_DL", "1")))

# RAM-Guard: min. freier RAM bevor neue Tasks starten (MB)
RAM_FLOOR_MB = int(os.environ.get("RAG_RAM_FLOOR_MB", "1500"))
RAM_CHECK_INTERVAL = 5


def _avail_ram_mb() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 99999


def wait_for_ram(floor_mb: int = RAM_FLOOR_MB, log=None, timeout: int = 300) -> bool:
    avail = _avail_ram_mb()
    if avail >= floor_mb:
        return True
    if log:
        log(f"⏳ RAM knapp ({avail} MB frei, brauche {floor_mb} MB) – warte…")
    gc.collect()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        gc.collect()
        time.sleep(RAM_CHECK_INTERVAL)
        avail = _avail_ram_mb()
        if avail >= floor_mb:
            if log:
                log(f"✅ RAM frei: {avail} MB – weiter.")
            return True
    if log:
        log(f"⚠️ RAM-Timeout ({avail} MB frei nach {timeout}s)")
    return False


def dynamic_batch_size(base: int = 500) -> int:
    avail = _avail_ram_mb()
    if avail < 2000:
        return max(25, base // 8)
    if avail < 4000:
        return max(50, base // 4)
    if avail < 8000:
        return max(100, base // 2)
    return base


def child_env(extra: dict | None = None) -> dict:
    env = os.environ.copy()
    for key in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        env[key] = NUM_THREADS
    env["TOKENIZERS_PARALLELISM"] = "false"
    if extra:
        env.update(extra)
    return env


def low_priority_cmd(cmd: Sequence[str]) -> list[str]:
    """nice + ionice (idle) damit der PC reaktionsfähig bleibt."""
    base = ["nice", "-n", "19"]
    if shutil.which("ionice"):
        base.extend(["ionice", "-c", "3"])
    return base + list(cmd)


def resource_summary() -> str:
    ram = _avail_ram_mb()
    return (
        f"CPUs: {CPU_COUNT} gesamt | {RESERVE_CORES} reserviert fürs System | "
        f"Jobs nutzen max. {WORKER_THREADS} Threads, {MAX_BG_TASKS} schwere Tasks parallel | "
        f"RAM frei: ~{ram} MB"
    )
