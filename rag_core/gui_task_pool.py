"""Begrenzte Parallelität für GUI-Hintergrundaufgaben."""
from __future__ import annotations

import gc
import queue
import threading
from typing import Callable

from .gui_resources import MAX_BG_TASKS, wait_for_ram

LogFn = Callable[[str], None]
RunningFn = Callable[[], bool]


class BackgroundTaskPool:
    def __init__(self, log: LogFn, is_running: RunningFn, max_workers: int = MAX_BG_TASKS):
        self._log = log
        self._is_running = is_running
        self._queue: queue.Queue = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._max = max_workers
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        for i in range(self._max):
            t = threading.Thread(target=self._worker, name=f"rag-bg-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def submit(self, fn: Callable[[], None], name: str = "task"):
        self._queue.put((name, fn))

    def _worker(self):
        while True:
            name, fn = self._queue.get()
            try:
                if self._is_running():
                    wait_for_ram(floor_mb=800, log=self._log, timeout=120)
                    fn()
                    gc.collect()
            except Exception as e:
                self._log(f"⚠️ {name}: {e}")
            finally:
                self._queue.task_done()

    def wait(self, timeout: float | None = None):
        self._queue.join()
