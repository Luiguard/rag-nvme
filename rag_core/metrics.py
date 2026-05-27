"""Enterprise Observability: Prometheus-kompatible Metriken.

Text-Format ohne SDK-Dependency. Jeder Prometheus/Grafana/Datadog-Stack
kann diese Metriken scrapen.

Metriken:
- Durchsatz (Queries/sec, per Tenant)
- Latenz (Histogramm)
- Cache-Effizienz (Hit/Miss-Ratio)
- GPU-Auslastung
- RAM/VRAM
- Batch-Effizienz
- NVMe-I/O
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class HistogramBucket:
    le: float
    count: int = 0


class Histogram:
    """Prometheus-kompatibles Histogramm."""

    def __init__(self, name: str, buckets: list[float] | None = None):
        self.name = name
        self._buckets = [
            HistogramBucket(le=b)
            for b in (buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0])
        ]
        self._sum = 0.0
        self._count = 0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            for bucket in self._buckets:
                if value <= bucket.le:
                    bucket.count += 1

    def render(self) -> str:
        with self._lock:
            lines = []
            for b in self._buckets:
                lines.append(f'{self.name}_bucket{{le="{b.le}"}} {b.count}')
            lines.append(f'{self.name}_bucket{{le="+Inf"}} {self._count}')
            lines.append(f'{self.name}_sum {self._sum:.6f}')
            lines.append(f'{self.name}_count {self._count}')
            return "\n".join(lines)


class Counter:
    """Thread-safe Counter mit optionalen Labels."""

    def __init__(self, name: str):
        self.name = name
        self._values: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def inc(self, labels: str = "", amount: int = 1) -> None:
        with self._lock:
            self._values[labels] += amount

    def get(self, labels: str = "") -> int:
        with self._lock:
            return self._values.get(labels, 0)

    def render(self) -> str:
        with self._lock:
            lines = []
            for labels, value in sorted(self._values.items()):
                if labels:
                    lines.append(f"{self.name}{{{labels}}} {value}")
                else:
                    lines.append(f"{self.name} {value}")
            return "\n".join(lines) if lines else f"{self.name} 0"


class Gauge:
    """Thread-safe Gauge."""

    def __init__(self, name: str):
        self.name = name
        self._value: float = 0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def get(self) -> float:
        with self._lock:
            return self._value

    def render(self) -> str:
        with self._lock:
            return f"{self.name} {self._value}"


class MetricsRegistry:
    """Zentrale Metrik-Registry."""

    def __init__(self):
        self.queries_total = Counter("rag_queries_total")
        self.query_duration = Histogram("rag_query_duration_seconds")
        self.cache_hits = Counter("rag_cache_hits_total")
        self.cache_misses = Counter("rag_cache_misses_total")
        self.embedding_batch_size = Histogram(
            "rag_embedding_batch_size",
            buckets=[1, 2, 5, 10, 25, 50, 100, 256, 512],
        )
        self.active_connections = Gauge("rag_active_connections")
        self.nvme_reads = Counter("rag_nvme_reads_total")
        self.ram_bytes = Gauge("rag_ram_usage_bytes")
        self.gpu_util = Gauge("rag_gpu_utilization_percent")
        self.gpu_vram_used = Gauge("rag_gpu_vram_used_bytes")
        self.index_rows = Gauge("rag_index_rows")
        self.errors_total = Counter("rag_errors_total")

    def render_all(self) -> str:
        self._refresh_system_metrics()
        parts = [
            self.queries_total.render(),
            self.query_duration.render(),
            self.cache_hits.render(),
            self.cache_misses.render(),
            self.embedding_batch_size.render(),
            self.active_connections.render(),
            self.nvme_reads.render(),
            self.ram_bytes.render(),
            self.gpu_util.render(),
            self.gpu_vram_used.render(),
            self.index_rows.render(),
            self.errors_total.render(),
        ]

        try:
            from .query_cache import get_search_cache, get_chat_cache
            sc = get_search_cache().stats()
            cc = get_chat_cache().stats()
            parts.append(f'rag_search_cache_entries {sc["entries"]}')
            parts.append(f'rag_search_cache_hit_rate {sc["hit_rate"]:.4f}')
            parts.append(f'rag_chat_cache_entries {cc["entries"]}')
            parts.append(f'rag_chat_cache_hit_rate {cc["hit_rate"]:.4f}')
        except Exception:
            pass

        try:
            from .batch_inference import get_embedding_batcher
            bs = get_embedding_batcher().stats()
            parts.append(f'rag_batch_total {bs["total_batches"]}')
            parts.append(f'rag_batch_avg_size {bs["avg_batch_size"]:.1f}')
            parts.append(f'rag_batch_queue_size {bs["queue_size"]}')
            parts.append(f'rag_gpu_detected {1 if bs["gpu_available"] else 0}')
        except Exception:
            pass

        try:
            from .tenants import get_tenant_manager
            ts = get_tenant_manager().stats()
            parts.append(f'rag_tenants_total {ts["total_tenants"]}')
            parts.append(f'rag_tenants_active {ts["active_tenants"]}')
        except Exception:
            pass

        try:
            from .bloom_index import get_tiered_retrieval
            tr = get_tiered_retrieval().stats()
            parts.append(f'rag_bloom_year_filters {tr["year_filters"]}')
            parts.append(f'rag_binary_vectors {tr["binary_vectors"]}')
            parts.append(f'rag_inverted_terms {tr["inverted_index"]["total_terms"]}')
        except Exception:
            pass

        return "\n".join(parts) + "\n"

    def _refresh_system_metrics(self) -> None:
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        self.ram_bytes.set(int(line.split()[1]) * 1024)
                        break
        except Exception:
            pass

        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(",")
                if len(parts) >= 2:
                    self.gpu_util.set(float(parts[0].strip()))
                    self.gpu_vram_used.set(float(parts[1].strip()) * 1024 * 1024)
        except Exception:
            pass


_registry: MetricsRegistry | None = None


def get_metrics() -> MetricsRegistry:
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
    return _registry


class RequestTimer:
    """Context-Manager für Request-Timing."""

    def __init__(self, tenant: str = "default"):
        self._tenant = tenant
        self._start = 0.0

    def __enter__(self):
        self._start = time.monotonic()
        m = get_metrics()
        m.active_connections.set(m.active_connections.get() + 1)
        return self

    def __exit__(self, *args):
        elapsed = time.monotonic() - self._start
        m = get_metrics()
        m.query_duration.observe(elapsed)
        m.queries_total.inc(f'tenant="{self._tenant}"')
        m.active_connections.set(max(0, m.active_connections.get() - 1))
