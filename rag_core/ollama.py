"""Ollama-Integration für lokale KI-Antworten."""
from __future__ import annotations

import json
import sys
from typing import Iterator

import requests

from .config import (
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_MODEL_PREFERENCE,
    OLLAMA_NUM_CTX,
    OLLAMA_TEMPERATURE,
)


def list_models() -> list[str]:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []

def pull_model(name: str) -> None:
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/pull", json={"name": name}, stream=False, timeout=3600)
        r.raise_for_status()
    except Exception as e:
        print(f"Fehler beim Download des Modells '{name}': {e}", file=sys.stderr)
        raise

def resolve_model(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if OLLAMA_MODEL:
        return OLLAMA_MODEL
    available = list_models()
    if not available:
        return None
    for pref in OLLAMA_MODEL_PREFERENCE:
        for name in available:
            if pref in name.lower():
                return name
    return available[0]


def chat_stream(
    messages: list[dict],
    model: str | None = None,
    *,
    temperature: float | None = None,
) -> Iterator[str]:
    model = resolve_model(model)
    if not model:
        raise RuntimeError(
            "Kein Ollama-Modell gefunden. Starte: ollama serve && ollama pull qwen2.5-coder"
        )

    # VRAM-Guard für Laptops: Reduziert num_ctx bei größeren Modellen dynamisch zur Absicherung
    num_ctx = OLLAMA_NUM_CTX
    try:
        m_lower = model.lower()
        if "32b" in m_lower or "70b" in m_lower:
            num_ctx = min(num_ctx, 2048)
        elif "14b" in m_lower:
            num_ctx = min(num_ctx, 3072)
        elif "7b" in m_lower or "8b" in m_lower:
            num_ctx = min(num_ctx, 4096)
    except Exception:
        pass

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": temperature if temperature is not None else OLLAMA_TEMPERATURE,
            "num_ctx": num_ctx,
        },
    }
    with requests.post(
        f"{OLLAMA_HOST}/api/chat", json=payload, stream=True, timeout=600
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            chunk = data.get("message", {}).get("content", "")
            if chunk:
                yield chunk
            if data.get("done"):
                break


def chat_print(messages: list[dict], model: str | None = None) -> str:
    print("\n--- ANTWORT ---\n", end="", flush=True)
    full: list[str] = []
    try:
        for chunk in chat_stream(messages, model=model):
            print(chunk, end="", flush=True)
            full.append(chunk)
    except requests.RequestException as e:
        print(f"\n[Ollama] Verbindungsfehler: {e}", file=sys.stderr)
        print("  → ollama serve  |  ollama pull qwen2.5-coder:32b", file=sys.stderr)
        raise
    print("\n" + "-" * 40)
    return "".join(full)
