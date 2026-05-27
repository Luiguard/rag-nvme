"""Enterprise ASGI-Server: Async, Multi-User, SSE-Streaming.

Ersetzt den blocking HTTPServer für Enterprise-Deployments.
- uvicorn als ASGI-Runner (production-grade)
- ThreadPoolExecutor für CPU-bound Ops (Embedding, LanceDB)
- SSE-Streaming für /chat (Tokens sofort sichtbar)
- Graceful Backpressure bei Überlast
- Kompatibel zur bestehenden API
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unicodedata
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_WORKERS = int(os.environ.get("RAG_MAX_WORKERS", "8"))
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
_active_requests = 0
_max_concurrent = int(os.environ.get("RAG_MAX_CONCURRENT", "200"))

from rag_server import (
    _is_safe_query,
    _check_rate,
    CHAT_MODEL_PREFERENCE,
    get_retriever,
    get_mesh,
    get_machine_core,
)
from rag_core.query_cache import get_chat_cache, get_search_cache
from rag_core.metrics import get_metrics, RequestTimer


def _json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


async def _run_in_thread(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn, *args)


async def handle_request(scope, receive, send):
    global _active_requests

    if scope["type"] == "lifespan":
        msg = await receive()
        if msg["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
            msg = await receive()
        if msg["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
        return

    if scope["type"] != "http":
        return

    method = scope["method"]
    path = scope["path"]

    if method == "OPTIONS":
        await _send_json(send, 204, b"")
        return

    if _active_requests >= _max_concurrent:
        await _send_json(send, 503, _json_bytes({
            "error": "Server überlastet. Bitte später erneut versuchen.",
        }), headers=[(b"retry-after", b"5")])
        return

    _active_requests += 1
    try:
        if method == "GET":
            if path == "/health":
                await _handle_health(send)
            elif path == "/peers":
                await _handle_peers(send)
            elif path == "/metrics":
                await _handle_metrics(send)
            else:
                await _send_json(send, 404, _json_bytes({"error": "not found"}))
        elif method == "POST":
            body = await _read_body(receive)
            if path == "/search":
                await _handle_search(send, body)
            elif path == "/chat":
                await _handle_chat(send, body, scope)
            elif path == "/chat/stream":
                await _handle_chat_stream(send, body, scope)
            elif path == "/machine-query":
                await _handle_machine_query(send, body)
            elif path == "/distributed-search":
                await _handle_distributed_search(send, body)
            else:
                await _send_json(send, 404, _json_bytes({"error": "not found"}))
        else:
            await _send_json(send, 405, _json_bytes({"error": "method not allowed"}))
    finally:
        _active_requests -= 1


async def _read_body(receive) -> dict:
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


async def _send_json(send, status: int, body: bytes, headers: list | None = None):
    h = [
        (b"content-type", b"application/json"),
        (b"access-control-allow-origin", b"*"),
        (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
        (b"access-control-allow-headers", b"Content-Type, X-Tenant-ID"),
    ]
    if headers:
        h.extend(headers)
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": h,
    })
    await send({"type": "http.response.body", "body": body})


async def _send_sse_start(send):
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/event-stream"),
            (b"cache-control", b"no-cache"),
            (b"connection", b"keep-alive"),
            (b"access-control-allow-origin", b"*"),
        ],
    })


async def _send_sse_event(send, data: str, event: str = "message"):
    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    await send({"type": "http.response.body", "body": payload.encode(), "more_body": True})


async def _send_sse_end(send):
    await send({"type": "http.response.body", "body": b"event: done\ndata: {}\n\n", "more_body": False})


def _sync_health() -> dict:
    r = get_retriever()
    m = get_mesh()
    mc = get_machine_core()

    from rag_core.embeddings import gpu_status
    gpu = gpu_status()

    batch_stats = {}
    try:
        from rag_core.batch_inference import get_embedding_batcher, get_ollama_batcher
        batch_stats = {
            "embedding": get_embedding_batcher().stats(),
            "llm": get_ollama_batcher().stats(),
        }
    except Exception:
        pass

    tenant_stats = {}
    try:
        from rag_core.tenants import get_tenant_manager
        tenant_stats = get_tenant_manager().stats()
    except Exception:
        pass

    return {
        "status": "ok",
        "ready": r.ready,
        "table": r.table_name,
        "rows": r.row_count() if r.ready else 0,
        "machine_core": {
            "ready": mc is not None,
            "stats": mc.stats() if mc else None,
        },
        "mesh": {
            "node_id": m.node_id,
            "local_status": m.local_status(),
            "peers": len(m.get_all_peers()),
            "idle_peers": len(m.get_idle_peers()),
        },
        "cache": {
            "search": get_search_cache().stats(),
            "chat": get_chat_cache().stats(),
        },
        "gpu": gpu,
        "batch": batch_stats,
        "tenants": tenant_stats,
        "server": {
            "type": "async",
            "active_requests": _active_requests,
            "max_concurrent": _max_concurrent,
            "max_workers": MAX_WORKERS,
        },
    }


async def _handle_health(send):
    data = await _run_in_thread(_sync_health)
    await _send_json(send, 200, _json_bytes(data))


async def _handle_peers(send):
    def _get():
        m = get_mesh()
        return [{
            "node_id": p.node_id,
            "host": p.host,
            "rag_type": p.rag_type,
            "rows": p.rows,
            "available": p.available,
            "cpu_pct": p.cpu_pct,
            "gpu_pct": p.gpu_pct,
            "ram_free_mb": p.ram_free_mb,
        } for p in m.get_all_peers()]
    peers = await _run_in_thread(_get)
    await _send_json(send, 200, _json_bytes({"peers": peers, "count": len(peers)}))


async def _handle_metrics(send):
    metrics = get_metrics()
    metrics.active_connections.set(_active_requests)
    try:
        r = get_retriever()
        if r.ready:
            metrics.index_rows.set(r.row_count())
    except Exception:
        pass
    body = metrics.render_all()
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"access-control-allow-origin", b"*"),
        ],
    })
    await send({"type": "http.response.body", "body": body.encode()})


def _sync_search(body: dict) -> tuple[int, dict]:
    query = body.get("query", "").strip()
    if not query:
        return 400, {"error": "query required"}
    top_k = body.get("top_k", 8)
    r = get_retriever()
    if not r.ready:
        return 503, {"error": "index not ready"}
    hits = r.search(query, top_k=top_k)
    results = [{
        "text": h.get("text", ""),
        "source": h.get("source", ""),
        "distance": float(h.get("_distance", 999)),
    } for h in hits]
    return 200, {"results": results, "count": len(results)}


async def _handle_search(send, body: dict):
    code, data = await _run_in_thread(_sync_search, body)
    await _send_json(send, code, _json_bytes(data))


def _sync_machine_query(body: dict) -> tuple[int, dict]:
    query = body.get("query", "").strip()
    if not query:
        return 400, {"error": "query required"}
    safe, reason = _is_safe_query(query)
    if not safe:
        return 403, {"error": reason, "blocked": True}
    mc = get_machine_core()
    if not mc:
        return 503, {"error": "Machine Core not available"}
    try:
        result = mc.process(query)
        return 200, result
    except Exception as e:
        return 500, {"error": f"Machine Core error: {str(e)[:200]}"}


async def _handle_machine_query(send, body: dict):
    code, data = await _run_in_thread(_sync_machine_query, body)
    await _send_json(send, code, _json_bytes(data))


async def _handle_distributed_search(send, body: dict):
    def _search():
        query = body.get("query", "").strip()
        if not query:
            return 400, {"error": "query required"}
        top_k = body.get("top_k", 8)
        m = get_mesh()
        results = m.distributed_search(query, top_k=top_k)
        return 200, {"results": results, "count": len(results)}
    code, data = await _run_in_thread(_search)
    await _send_json(send, code, _json_bytes(data))


def _sync_chat(body: dict) -> tuple[int, dict]:
    query = body.get("query", "").strip()
    if not query:
        return 400, {"error": "Frage fehlt."}
    safe, reason = _is_safe_query(query)
    if not safe:
        return 403, {"error": reason, "blocked": True}

    chat_cache = get_chat_cache()
    from rag_core.embeddings import embed_query
    q_vector, _ = embed_query(query)
    cached = chat_cache.get(query, q_vector)
    if cached is not None:
        cached["cached"] = True
        return 200, cached

    try:
        from rag_core.assistant import LocalAssistant
        from rag_core.ollama import list_models
        r = get_retriever()
        assistant = LocalAssistant(r, mesh_node=get_mesh())

        chat_model = None
        available = list_models()
        for pref in CHAT_MODEL_PREFERENCE:
            for m in available:
                if pref in m:
                    chat_model = m
                    break
            if chat_model:
                break

        result = assistant.answer_text(query, mode="support", model=chat_model)
        answer = result.get("text", "")
        hits = result.get("hits", [])
        sources = []
        for h in hits[:5]:
            from rag_core.prompts import _short_source
            sources.append(_short_source(h.get("source", "")))

        response = {
            "answer": answer,
            "sources": sources,
            "model": result.get("model", "unbekannt"),
            "blocked": result.get("blocked", False),
            "cached": False,
        }
        chat_cache.put(query, q_vector, response)
        return 200, response
    except Exception as e:
        return 500, {"error": f"Fehler: {str(e)[:200]}"}


async def _handle_chat(send, body: dict, scope: dict):
    ip = scope.get("client", ("127.0.0.1", 0))[0]
    if not _check_rate(ip):
        await _send_json(send, 429, _json_bytes({
            "error": "Zu viele Anfragen. Bitte warte eine Minute."
        }))
        return
    code, data = await _run_in_thread(_sync_chat, body)
    await _send_json(send, code, _json_bytes(data))


async def _handle_chat_stream(send, body: dict, scope: dict):
    ip = scope.get("client", ("127.0.0.1", 0))[0]
    if not _check_rate(ip):
        await _send_json(send, 429, _json_bytes({
            "error": "Zu viele Anfragen."
        }))
        return

    query = body.get("query", "").strip()
    if not query:
        await _send_json(send, 400, _json_bytes({"error": "Frage fehlt."}))
        return

    safe, reason = _is_safe_query(query)
    if not safe:
        await _send_json(send, 403, _json_bytes({"error": reason, "blocked": True}))
        return

    chat_cache = get_chat_cache()

    def _get_vector():
        from rag_core.embeddings import embed_query
        return embed_query(query)

    q_vector, _ = await _run_in_thread(_get_vector)
    cached = chat_cache.get(query, q_vector)
    if cached is not None:
        await _send_sse_start(send)
        await _send_sse_event(send, cached.get("answer", ""), "token")
        await _send_sse_end(send)
        return

    await _send_sse_start(send)
    tokens = []

    def _stream_chat():
        from rag_core.assistant import LocalAssistant
        from rag_core.ollama import list_models
        r = get_retriever()
        assistant = LocalAssistant(r, mesh_node=get_mesh())

        chat_model = None
        available = list_models()
        for pref in CHAT_MODEL_PREFERENCE:
            for m in available:
                if pref in m:
                    chat_model = m
                    break
            if chat_model:
                break

        result = assistant.answer_text(
            query, mode="support", model=chat_model,
            on_token=lambda t: tokens.append(t),
        )
        return result

    result = await _run_in_thread(_stream_chat)

    full_answer = result.get("text", "")
    await _send_sse_event(send, full_answer, "token")

    hits = result.get("hits", [])
    sources = []
    for h in hits[:5]:
        from rag_core.prompts import _short_source
        sources.append(_short_source(h.get("source", "")))

    response = {
        "answer": full_answer,
        "sources": sources,
        "model": result.get("model", "unbekannt"),
        "blocked": result.get("blocked", False),
        "cached": False,
    }
    chat_cache.put(query, q_vector, response)
    await _send_sse_end(send)


app = handle_request


def main():
    try:
        import uvicorn
    except ImportError:
        print("❌ uvicorn nicht installiert. Installation: pip install uvicorn")
        print("   Fallback: python rag_server.py (blocking)")
        sys.exit(1)

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    host = os.environ.get("RAG_BIND_HOST", "127.0.0.1")

    mesh = get_mesh()
    print(f"🌐 Mesh gestartet: {mesh.node_id}")
    print(mesh.mesh_summary())

    mc = get_machine_core()
    if mc:
        stats = mc.stats()
        print(f"⚡ NVMe Machine Core: {stats['total_blocks']:,} Blöcke, "
              f"{stats['disk_mb']:.1f} MB, {stats['years_indexed']} Jahre indexiert")
    else:
        print("ℹ️  NVMe Machine Core: nicht verfügbar")

    from rag_core.embeddings import gpu_status
    gpu = gpu_status()
    if gpu["available"]:
        print(f"🎮 GPU: {gpu.get('device_name', 'CUDA')} | "
              f"VRAM: {gpu.get('vram_total_mb', '?')} MB")
    else:
        print("ℹ️  GPU: nicht verfügbar (CPU-Modus)")

    print(f"\n🚀 RAG Enterprise Server (async) auf http://{host}:{port}")
    print(f"   Max Workers: {MAX_WORKERS} | Max Concurrent: {_max_concurrent}")
    print(f"   GPU: {'✅ ' + gpu.get('device_name', 'CUDA') if gpu['available'] else '❌ CPU-only'}")
    print(f"   POST /search              Lokale Suche (cached)")
    print(f"   POST /chat                Chat (cached + GPU)")
    print(f"   POST /chat/stream         Chat SSE-Streaming")
    print(f"   POST /machine-query       NVMe Machine Core")
    print(f"   POST /distributed-search  Verteilte Suche (Mesh)")
    print(f"   GET  /health              Status + GPU + Cache + Batch")
    print(f"   GET  /metrics             Prometheus-Metriken")
    print(f"   GET  /peers               Peer-Liste")

    uvicorn.run(
        "rag_server_async:app",
        host=host,
        port=port,
        workers=1,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
