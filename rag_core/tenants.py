"""Multi-Tenant-Isolation: Shared Infrastructure, isolierte Daten.

Jeder Konzern-Tenant bekommt:
- Eigene LanceDB-Tabelle (shared DB, separate Tables)
- Eigene Quota-Limits (Requests/min, Max Chunks)
- Isolierte Cache-Namespaces
- API-Key-basierte Authentifizierung

Enterprise-Pattern: Shared-Nothing auf Datenebene, Shared-Everything auf Infrastruktur.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MULTI_TENANT = os.environ.get("RAG_MULTI_TENANT", "0") == "1"
TENANT_CONFIG_PATH = Path(os.environ.get(
    "RAG_TENANT_CONFIG",
    str(Path(__file__).resolve().parent.parent / "tenants.json"),
))


@dataclass
class TenantConfig:
    tenant_id: str
    name: str
    api_key_hash: str
    max_requests_per_min: int = 60
    max_chunks: int = 500_000
    max_concurrent: int = 50
    enabled: bool = True
    created: str = ""
    table_suffix: str = ""

    @property
    def table_name(self) -> str:
        from .config import TABLE_NAME
        suffix = self.table_suffix or self.tenant_id
        return f"{TABLE_NAME}_{suffix}"


@dataclass
class TenantRateState:
    timestamps: list[float] = field(default_factory=list)
    active_requests: int = 0


class TenantManager:
    """Verwaltet Tenants, API-Keys, Quotas, Rate-Limits."""

    def __init__(self):
        self._tenants: dict[str, TenantConfig] = {}
        self._key_to_tenant: dict[str, str] = {}
        self._rates: dict[str, TenantRateState] = {}
        self._lock = threading.Lock()
        self._load_config()

    def _load_config(self) -> None:
        if not TENANT_CONFIG_PATH.exists():
            return
        try:
            data = json.loads(TENANT_CONFIG_PATH.read_text("utf-8"))
            for t in data.get("tenants", []):
                cfg = TenantConfig(**t)
                self._tenants[cfg.tenant_id] = cfg
                self._key_to_tenant[cfg.api_key_hash] = cfg.tenant_id
        except Exception:
            pass

    def reload(self) -> None:
        with self._lock:
            self._tenants.clear()
            self._key_to_tenant.clear()
            self._load_config()

    def save_config(self) -> None:
        data = {
            "tenants": [
                {
                    "tenant_id": t.tenant_id,
                    "name": t.name,
                    "api_key_hash": t.api_key_hash,
                    "max_requests_per_min": t.max_requests_per_min,
                    "max_chunks": t.max_chunks,
                    "max_concurrent": t.max_concurrent,
                    "enabled": t.enabled,
                    "created": t.created,
                    "table_suffix": t.table_suffix,
                }
                for t in self._tenants.values()
            ]
        }
        TENANT_CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")

    @staticmethod
    def hash_api_key(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def create_tenant(
        self,
        tenant_id: str,
        name: str,
        api_key: str,
        *,
        max_requests_per_min: int = 60,
        max_chunks: int = 500_000,
    ) -> TenantConfig:
        cfg = TenantConfig(
            tenant_id=tenant_id,
            name=name,
            api_key_hash=self.hash_api_key(api_key),
            max_requests_per_min=max_requests_per_min,
            max_chunks=max_chunks,
            created=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        with self._lock:
            self._tenants[tenant_id] = cfg
            self._key_to_tenant[cfg.api_key_hash] = tenant_id
        self.save_config()
        return cfg

    def authenticate(self, api_key: str) -> TenantConfig | None:
        if not MULTI_TENANT:
            return None
        key_hash = self.hash_api_key(api_key)
        tenant_id = self._key_to_tenant.get(key_hash)
        if not tenant_id:
            return None
        cfg = self._tenants.get(tenant_id)
        if cfg and cfg.enabled:
            return cfg
        return None

    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        return self._tenants.get(tenant_id)

    def check_rate_limit(self, tenant_id: str) -> bool:
        cfg = self._tenants.get(tenant_id)
        if not cfg:
            return False
        with self._lock:
            state = self._rates.setdefault(tenant_id, TenantRateState())
            now = time.time()
            state.timestamps = [t for t in state.timestamps if now - t < 60]
            if len(state.timestamps) >= cfg.max_requests_per_min:
                return False
            if state.active_requests >= cfg.max_concurrent:
                return False
            state.timestamps.append(now)
            state.active_requests += 1
        return True

    def release_request(self, tenant_id: str) -> None:
        with self._lock:
            state = self._rates.get(tenant_id)
            if state:
                state.active_requests = max(0, state.active_requests - 1)

    def list_tenants(self) -> list[dict]:
        return [
            {
                "tenant_id": t.tenant_id,
                "name": t.name,
                "enabled": t.enabled,
                "max_requests_per_min": t.max_requests_per_min,
                "max_chunks": t.max_chunks,
                "table": t.table_name,
            }
            for t in self._tenants.values()
        ]

    def stats(self) -> dict:
        with self._lock:
            active = {
                tid: {
                    "active_requests": s.active_requests,
                    "requests_last_min": len([
                        t for t in s.timestamps if time.time() - t < 60
                    ]),
                }
                for tid, s in self._rates.items()
            }
        return {
            "multi_tenant_enabled": MULTI_TENANT,
            "total_tenants": len(self._tenants),
            "active_tenants": len(active),
            "per_tenant": active,
        }


_manager: TenantManager | None = None


def get_tenant_manager() -> TenantManager:
    global _manager
    if _manager is None:
        _manager = TenantManager()
    return _manager


def resolve_tenant_from_headers(headers: dict) -> TenantConfig | None:
    if not MULTI_TENANT:
        return None
    api_key = headers.get("x-api-key", "") or headers.get("authorization", "").removeprefix("Bearer ")
    if not api_key:
        return None
    return get_tenant_manager().authenticate(api_key)
