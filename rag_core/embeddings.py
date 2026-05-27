"""Embedding-Erzeugung: GPU-beschleunigt, Mobile-NPU, lokaler Fallback.

Prioritäts-Kaskade:
1. GPU (CUDA) → 60× schneller als CPU bei Batch-Embedding
2. Mobile NPU → Smartphone-Offload
3. CPU Fallback → immer verfügbar
"""
from __future__ import annotations

import gc
import os
import threading
import requests
from langchain_huggingface import HuggingFaceEmbeddings

from .config import EMBEDDING_MODEL, MOBILE_EMBED_TIMEOUT, MOBILE_NODE_URL
from .quality import enhance_query
from .gui_resources import wait_for_ram, dynamic_batch_size

_local: HuggingFaceEmbeddings | None = None
_local_lock = threading.Lock()
_mobile_failed = False
_gpu_available: bool | None = None


def _detect_gpu() -> bool:
    global _gpu_available
    if _gpu_available is not None:
        return _gpu_available
    try:
        import torch
        _gpu_available = torch.cuda.is_available()
    except ImportError:
        _gpu_available = False
    return _gpu_available


def _local_model() -> HuggingFaceEmbeddings:
    global _local
    if _local is not None:
        return _local
    with _local_lock:
        if _local is not None:
            return _local
        wait_for_ram(floor_mb=800)
        model_kwargs = {}
        if _detect_gpu():
            model_kwargs["device"] = "cuda"
            try:
                import torch
                torch.backends.cudnn.benchmark = True
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
                model_kwargs["torch_dtype"] = torch.float16
            except Exception:
                pass
        _local = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs=model_kwargs,
        )
    return _local


def gpu_status() -> dict:
    gpu = _detect_gpu()
    info = {"available": gpu, "device": "cuda" if gpu else "cpu"}
    if gpu:
        try:
            import torch
            info["device_name"] = torch.cuda.get_device_name(0)
            info["vram_total_mb"] = round(torch.cuda.get_device_properties(0).total_mem / (1024 * 1024))
            info["vram_free_mb"] = round(
                (torch.cuda.get_device_properties(0).total_mem - torch.cuda.memory_allocated(0))
                / (1024 * 1024)
            )
        except Exception:
            pass
    return info


def embed_documents(texts: list[str], *, force: bool = False) -> list[list[float]] | None:
    global _mobile_failed
    if not texts:
        return []

    from .config import RAG_LAZY_EMBEDDING, VECTOR_DIM
    if RAG_LAZY_EMBEDDING and not force:
        return [[0.0] * VECTOR_DIM for _ in texts]

    prefer_local = os.environ.get("RAG_PREFER_LOCAL", "0") == "1"

    if not _mobile_failed and not prefer_local and MOBILE_NODE_URL:
        try:
            sub_batch_size = 32
            all_vectors = []
            for i in range(0, len(texts), sub_batch_size):
                sub_texts = texts[i:i + sub_batch_size]
                resp = requests.post(
                    MOBILE_NODE_URL,
                    json={"input": sub_texts},
                    timeout=MOBILE_EMBED_TIMEOUT,
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    vectors = [d["embedding"] for d in data]
                    if len(vectors) == len(sub_texts):
                        all_vectors.extend(vectors)
                    else:
                        raise ValueError("Mismatch in returned embedding count")
                else:
                    raise RuntimeError(f"Server returned status {resp.status_code}")
            
            if len(all_vectors) == len(texts):
                return all_vectors
        except Exception:
            _mobile_failed = True

    gpu = _detect_gpu()
    if gpu:
        batch_sz = min(len(texts), 512)
    else:
        batch_sz = dynamic_batch_size(len(texts))

    if len(texts) <= batch_sz:
        wait_for_ram(floor_mb=400 if gpu else 600)
        result = _local_model().embed_documents(texts)
        gc.collect()
        return result

    all_vecs = []
    for i in range(0, len(texts), batch_sz):
        wait_for_ram(floor_mb=400 if gpu else 600)
        chunk = texts[i:i + batch_sz]
        vecs = _local_model().embed_documents(chunk)
        all_vecs.extend(vecs)
        if not gpu:
            gc.collect()
    return all_vecs


def embed_query(query: str, *, enhance: bool = True) -> tuple[list[float], bool]:
    """Returns (vector, used_mobile)."""
    global _mobile_failed
    q = enhance_query(query) if enhance else query
    prefer_local = os.environ.get("RAG_PREFER_LOCAL", "0") == "1"

    if not _mobile_failed and not prefer_local and MOBILE_NODE_URL:
        try:
            resp = requests.post(
                MOBILE_NODE_URL,
                json={"input": [q]},
                timeout=min(MOBILE_EMBED_TIMEOUT, 8),
            )
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    return data[0]["embedding"], True
        except Exception:
            _mobile_failed = True

    return _local_model().embed_query(q), False


def embed_query_batched(query: str, *, enhance: bool = True) -> tuple[list[float], bool]:
    """GPU-optimiert: Routet durch den Batch-Aggregator unter Last."""
    try:
        from .batch_inference import get_embedding_batcher
        batcher = get_embedding_batcher()
        q = enhance_query(query) if enhance else query
        vector = batcher.embed_sync(q)
        return vector, False
    except Exception:
        return embed_query(query, enhance=enhance)
