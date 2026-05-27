"""Einheitlicher Assistent: NVMe-Kern + RAG + Ollama + Sicherheitsprüfung.

Architektur:
  Layer 1: MachineCore (NVMe) → strukturierte Fakten/Events
  Layer 2: Adapter (Ollama) → menschliche Sprache
  Fallback: Klassische RAG-Pipeline
"""
from __future__ import annotations

from typing import Callable

from .ollama import resolve_model
from .prompts import MODE_AUDIT, MODE_CODE, MODE_REVIEW, MODE_SUPPORT, build_fix_prompt
from .retrieval import KnowledgeRetriever
from .secure_coding import audit_code_blocks, format_findings, is_safe_user_intent


class LocalAssistant:
    def __init__(self, retriever: KnowledgeRetriever | None = None, mesh_node = None):
        self.retriever = retriever or KnowledgeRetriever()
        self.mesh_node = mesh_node
        self._machine_core = None

    @property
    def machine_core(self):
        if self._machine_core is None:
            try:
                from .machine_core import MachineCore
                core = MachineCore()
                if core.ready:
                    self._machine_core = core
                else:
                    self._machine_core = False
            except Exception:
                self._machine_core = False
        return self._machine_core if self._machine_core else None

    def _machine_context(self, query: str) -> str:
        """Get structured context from NVMe Machine Core."""
        core = self.machine_core
        if not core:
            return ""
        try:
            result = core.process(query)
            parts = []

            if result.get("events"):
                parts.append("=== Ereignisse ===")
                for ev in result["events"][:15]:
                    parts.append(f"  {ev.get('year', '?')}: {ev.get('description', '')}")

            if result.get("facts"):
                parts.append("=== Fakten ===")
                for f in result["facts"][:10]:
                    parts.append(f"  {f.get('subject', '?')}: {f.get('object', '')}")

            if result.get("articles"):
                parts.append("=== Artikel ===")
                for a in result["articles"][:5]:
                    text = a.get("text", "")[:500]
                    parts.append(f"  [{a.get('source', '?')}] {text}")

            if parts:
                return (
                    f"[NVMe-Kern: {result.get('total_results', 0)} Treffer in "
                    f"{result.get('elapsed_ms', 0)}ms]\n" + "\n".join(parts)
                )
        except Exception:
            pass
        return ""

    def parse_mode(self, query: str) -> tuple[str, str]:
        q = query.strip()
        for prefix, mode in (
            ("/code ", MODE_CODE),
            ("/review ", MODE_REVIEW),
            ("/audit ", MODE_AUDIT),
            ("/support ", MODE_SUPPORT),
        ):
            if q.lower().startswith(prefix):
                return q[len(prefix) :].strip(), mode
        if any(w in q.lower() for w in ("sicherheit", "schwachstelle", "owasp", "audit", "backdoor")):
            return q, MODE_AUDIT
        if any(w in q.lower() for w in ("review", "prüfe", "code review", "refactor")):
            return q, MODE_REVIEW
        if any(w in q.lower() for w in ("schreibe", "implementier", "code ", "funktion ", "class ")):
            return q, MODE_CODE
        return q, MODE_SUPPORT

    def answer_text(
        self,
        query: str,
        *,
        mode: str | None = None,
        model: str | None = None,
        user_context: str = "",
        retry_on_unsafe: bool = True,
        on_token: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> dict:
        """Wie answer(), aber ohne print – optional Streaming-Callback pro Token."""
        if not is_safe_user_intent(query):
            return {
                "blocked": True,
                "text": "Anfrage aus Sicherheitsgründen blockiert (destruktiv/unsicher).",
                "hits": [],
            }

        q, mode = (query, mode) if mode else self.parse_mode(query)

        # Layer 1: NVMe Machine Core
        machine_ctx = self._machine_context(q)
        if machine_ctx:
            user_context = machine_ctx + ("\n" + user_context if user_context else "")

        if self.mesh_node:
            hits = self.mesh_node.distributed_search(q)
        else:
            hits = self.retriever.search(q)

        try:
            from .live_fetch import should_live_fetch, fetch_and_index, enqueue_related, start_background_expansion
            if should_live_fetch(hits):
                status_fn = on_status or (lambda x: None)
                status_fn("🔍 Wissen wird live nachgeladen…")
                chunks, sources = fetch_and_index(
                    q,
                    table=self.retriever.table,
                    max_articles=3,
                    log_fn=status_fn,
                )
                if chunks > 0:
                    hits = self.retriever.search(q)
                    status_fn(f"✅ {chunks} Chunks nachgeladen, Suche aktualisiert")

                enqueue_related(q)
                start_background_expansion()
        except Exception:
            pass

        if self.mesh_node:
            from .prompts import build_context_block, build_rag_prompt
            context, sources = build_context_block(hits)
            if not user_context:
                user_context = self.retriever.get_user_context(q)
            messages = build_rag_prompt(q, context, sources, mode=mode, user_context=user_context)
        else:
            messages, _ = self.retriever.get_messages_for_ollama(
                q, mode=mode, user_context=user_context
            )
        if not resolve_model(model):
            if self.mesh_node:
                from .prompts import build_context_block
                ctx, _ = build_context_block(hits)
            else:
                ctx, _, _ = self.retriever.get_context(q)
            return {"blocked": False, "text": ctx, "hits": hits, "model": None}

        from .ollama import chat_stream

        resolved = resolve_model(model)

        def _stream(msgs: list[dict]) -> str:
            parts: list[str] = []
            for chunk in chat_stream(msgs, model=resolved):
                parts.append(chunk)
                if on_token:
                    on_token(chunk)
            return "".join(parts)

        answer = _stream(messages)
        findings = audit_code_blocks(answer)
        if findings and retry_on_unsafe:
            fix_msgs = build_fix_prompt(answer, format_findings(findings))
            answer = _stream(fix_msgs)
            findings = audit_code_blocks(answer)

        report = format_findings(findings)
        if report:
            answer += "\n" + report

        return {
            "blocked": False,
            "text": answer,
            "hits": hits,
            "findings": findings,
            "model": resolved,
            "mode": mode,
        }

    def answer(
        self,
        query: str,
        *,
        mode: str | None = None,
        user_context: str = "",
        retry_on_unsafe: bool = True,
    ) -> dict:
        def _on_token(chunk: str) -> None:
            print(chunk, end="", flush=True)

        print("\n--- ANTWORT ---\n", end="", flush=True)
        result = self.answer_text(
            query,
            mode=mode,
            user_context=user_context,
            retry_on_unsafe=retry_on_unsafe,
            on_token=_on_token,
        )
        print("\n" + "-" * 40)
        return result
