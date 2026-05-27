#!/usr/bin/env python3
"""HTTP-API für RAG Custom Knowledge mit Mesh-Netzwerk + Demo-Chat."""
import json
import re
import sys
import time
import unicodedata
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ─── Multi-Layer Safety Filter ───

_LEET_MAP = str.maketrans({
    '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's',
    '7': 't', '8': 'b', '@': 'a', '$': 's', '!': 'i',
    '|': 'l', '+': 't', '(': 'c', ')': 'o',
})

_BLOCKED_PATTERNS = [
    # Waffen & Gewalt
    r'\b(bombe|sprengstoff|explosive?|waffe|schusswaffe|gift|zyankali|rizin|anthrax|sarin)\b',
    r'\b(bomb|weapon|explosive|gun|poison|cyanide|ricin|nerve.?agent|napalm)\b',
    r'\b(anschlag|attentat|terroris|amok|massaker|töt|mord|ermord|umbring|erschieß)\b',
    r'\b(kill|murder|assassin|terroris|shoot|attack|massacre)\b',
    # Drogen
    r'\b(meth|heroin|kokain|cocaine|fentanyl|droge.*herstell|drug.*synth|lsd.*herstell)\b',
    r'\b(crystal.?meth|crack.?kokain|mdma.*synth)\b',
    # Hacking/Malware (destruktiv)
    r'\b(ransomware|keylogger|rootkit|trojan|exploit.*schreib|malware.*erstell)\b',
    r'\b(ddos.*anleitung|phishing.*erstell|botnet.*bau|zero.?day.*exploit)\b',
    r'\b(reverse.?shell|bind.?shell|payload.*generier|remote.*access.*trojan)\b',
    # Illegale Aktivitäten
    r'\b(geldwäsche|money.?launder|betrug.*anleitung|fraud.*instruc|identitätsdiebstahl)\b',
    r'\b(kinderporn|child.?porn|csam|pädophil|pedophil)\b',
    r'\b(dark.?net.*kauf|darkweb.*buy|silk.?road|illegale.*marktplatz)\b',
    # Selbstverletzung
    r'\b(suizid.*methode|suicide.*method|selbstmord.*anleitung|selbstverletz)\b',
    # Prompt Injection / Jailbreak
    r'(ignore.*instruc|vergiss.*regeln|ignore.*rules|bypass.*filter|umgeh.*sicher)',
    r'(du.?bist.?jetzt|you.?are.?now|act.?as|spiel.*rolle.*böse|pretend.*evil)',
    r'(DAN|do.?anything.?now|jailbreak|no.?restrictions|keine.*einschränk)',
]
_BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in _BLOCKED_PATTERNS]

_BLOCKED_COMPACT = [
    r'(bombe|sprengstoff|explosive|waffe|schusswaffe|gift|zyankali|rizin|anthrax|sarin)',
    r'(bomb|weapon|explosive|gun|poison|cyanide|ricin|nerveagent|napalm)',
    r'(anschlag|attentat|terroris|amok|massaker|mord|ermord|umbring)',
    r'(kill|murder|assassin|terroris|shoot|massacre)',
    r'(heroin|kokain|cocaine|fentanyl|drogeherstell|drugsynth|lsdherstell)',
    r'(crystalmeth|crackkokain|mdmasynth)',
    r'(ransomware|keylogger|rootkit|trojan|exploitschreib|malwareerstell)',
    r'(ddosanleitung|phishingerstell|botnetbau|zerodayexploit)',
    r'(reverseshell|bindshell|payloadgenerier|remoteaccesstrojan)',
    r'(geldwasche|geldwaesche|moneylaunder|betruganleitung|fraudinstruc)',
    r'(kinderporn|childporn|csam|padophil|paedophil|pedophil)',
    r'(darknetkauf|darkwebbuy|silkroad)',
    r'(suizidmethode|suicidemethod|selbstmordanleitung|selbstverletz)',
    r'(ignoreinstruc|vergissregeln|ignorerules|bypassfilter|umgehsicher)',
    r'(dubistjetzt|youarenow|actas|spielrollebose|pretendevil)',
    r'(doanythingnow|jailbreak|norestrictions|keineeinschrank)',
]
_BLOCKED_COMPACT_RE = [re.compile(p, re.IGNORECASE) for p in _BLOCKED_COMPACT]


def _normalize_text(text: str) -> str:
    t = unicodedata.normalize('NFKD', text)
    t = ''.join(c if c.isascii() else ' ' for c in t)
    t = t.translate(_LEET_MAP)
    t = re.sub(r'([a-zA-Z])[-_.\s*]+(?=[a-zA-Z])', r'\1', t)
    return re.sub(r'\s+', ' ', t).strip().lower()


def _normalize_compact(text: str) -> str:
    return re.sub(r'[^a-z]', '', _normalize_text(text))


def _is_safe_query(text: str) -> tuple[bool, str]:
    if not text or len(text) > 2000:
        return False, 'Ungültige Anfrage.'
    _BLOCK_MSG = 'Diese Anfrage kann aus Sicherheitsgründen nicht bearbeitet werden.'
    for rx in _BLOCKED_RE:
        if rx.search(text):
            return False, _BLOCK_MSG
    normalized = _normalize_text(text)
    for rx in _BLOCKED_RE:
        if rx.search(normalized):
            return False, _BLOCK_MSG
    compact = _normalize_compact(text)
    for rx in _BLOCKED_COMPACT_RE:
        if rx.search(compact):
            return False, _BLOCK_MSG
    special = sum(1 for c in text if not c.isalnum() and not c.isspace())
    if len(text) > 20 and special / len(text) > 0.35:
        return False, 'Anfrage enthält zu viele Sonderzeichen.'
    return True, ''


# Rate limiting (IP -> [timestamps])
_rate_limits: dict[str, list[float]] = {}
_RATE_WINDOW = 60
_RATE_MAX = 10

def _check_rate(ip: str) -> bool:
    now = time.time()
    times = _rate_limits.get(ip, [])
    times = [t for t in times if now - t < _RATE_WINDOW]
    if len(times) >= _RATE_MAX:
        _rate_limits[ip] = times
        return False
    times.append(now)
    _rate_limits[ip] = times
    return True

from rag_core.retrieval import KnowledgeRetriever
from rag_core.distributed import get_mesh_node
from rag_core.query_cache import get_chat_cache, get_search_cache

_retriever = None
_mesh = None
_machine_core = None

def get_retriever():
    global _retriever
    if _retriever is None:
        _retriever = KnowledgeRetriever()
    return _retriever

def get_mesh():
    global _mesh
    if _mesh is None:
        _mesh = get_mesh_node(rag_type="custom-knowledge", retriever=get_retriever())
    return _mesh

def get_machine_core():
    global _machine_core
    if _machine_core is None:
        try:
            from rag_core.machine_core import MachineCore
            core = MachineCore()
            if core.ready:
                _machine_core = core
        except Exception:
            pass
    return _machine_core


class RAGHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            r = get_retriever()
            m = get_mesh()
            mc = get_machine_core()
            self._json(200, {
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
            })
        elif self.path == "/peers":
            m = get_mesh()
            peers = []
            for p in m.get_all_peers():
                peers.append({
                    "node_id": p.node_id,
                    "host": p.host,
                    "rag_type": p.rag_type,
                    "rows": p.rows,
                    "available": p.available,
                    "cpu_pct": p.cpu_pct,
                    "gpu_pct": p.gpu_pct,
                    "ram_free_mb": p.ram_free_mb,
                })
            self._json(200, {"peers": peers, "count": len(peers)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == '/search':
            self._handle_search()
        elif self.path == '/chat':
            self._handle_chat()
        elif self.path == '/machine-query':
            self._handle_machine_query()
        elif self.path == '/distributed-search':
            self._handle_distributed_search()
        else:
            self._json(404, {'error': 'not found'})

    def _handle_machine_query(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        query = body.get('query', '').strip()
        if not query:
            self._json(400, {'error': 'query required'})
            return

        safe, reason = _is_safe_query(query)
        if not safe:
            self._json(403, {'error': reason, 'blocked': True})
            return

        mc = get_machine_core()
        if not mc:
            self._json(503, {'error': 'Machine Core not available (NVMe store missing)'})
            return

        try:
            result = mc.process(query)
            self._json(200, result)
        except Exception as e:
            self._json(500, {'error': f'Machine Core error: {str(e)[:200]}'})

    def _handle_search(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        query = body.get("query", "").strip()
        if not query:
            self._json(400, {"error": "query required"})
            return
        top_k = body.get("top_k", 8)
        r = get_retriever()
        if not r.ready:
            self._json(503, {"error": "index not ready"})
            return
        hits = r.search(query, top_k=top_k)
        results = []
        for h in hits:
            results.append({
                "text": h.get("text", ""),
                "source": h.get("source", ""),
                "distance": float(h.get("_distance", 999)),
            })
        self._json(200, {"results": results, "count": len(results)})

    def _handle_distributed_search(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        query = body.get("query", "").strip()
        if not query:
            self._json(400, {"error": "query required"})
            return
        top_k = body.get("top_k", 8)
        m = get_mesh()
        results = m.distributed_search(query, top_k=top_k)
        self._json(200, {'results': results, 'count': len(results)})

    def _handle_chat(self):
        ip = self.client_address[0]
        if not _check_rate(ip):
            self._json(429, {'error': 'Zu viele Anfragen. Bitte warte eine Minute.'})
            return

        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        query = body.get('query', '').strip()

        if not query:
            self._json(400, {'error': 'Frage fehlt.'})
            return

        safe, reason = _is_safe_query(query)
        if not safe:
            self._json(403, {'error': reason, 'blocked': True})
            return

        chat_cache = get_chat_cache()
        from rag_core.embeddings import embed_query
        q_vector, _ = embed_query(query)
        cached = chat_cache.get(query, q_vector)
        if cached is not None:
            cached['cached'] = True
            self._json(200, cached)
            return

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

            result = assistant.answer_text(
                query, mode='support', model=chat_model,
            )

            answer = result.get('text', '')
            hits = result.get('hits', [])
            sources = []
            for h in hits[:5]:
                src = h.get('source', '')
                from rag_core.prompts import _short_source
                sources.append(_short_source(src))

            response = {
                'answer': answer,
                'sources': sources,
                'model': result.get('model', 'unbekannt'),
                'blocked': result.get('blocked', False),
                'cached': False,
            }
            chat_cache.put(query, q_vector, response)
            self._json(200, response)
        except Exception as e:
            self._json(500, {'error': f'Fehler: {str(e)[:200]}'})


CHAT_MODEL_PREFERENCE = [
    'qwen3:4b',
    'qwen2.5:3b',
    'llama3.2:3b',
    'phi4-mini',
    'gemma3:4b',
    'llama3.2:1b',
    'qwen2.5:1.5b',
]


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    mesh = get_mesh()
    print(f"🌐 Mesh gestartet: {mesh.node_id}")
    print(mesh.mesh_summary())

    mc = get_machine_core()
    if mc:
        stats = mc.stats()
        print(f"⚡ NVMe Machine Core: {stats['total_blocks']:,} Blöcke, "
              f"{stats['disk_mb']:.1f} MB, {stats['years_indexed']} Jahre indexiert")
    else:
        print("ℹ️  NVMe Machine Core: nicht verfügbar (nvme_knowledge.dat fehlt)")

    server = HTTPServer(("127.0.0.1", port), RAGHandler)
    print(f"\nRAG Custom Server auf http://127.0.0.1:{port}")
    print(f"  POST /search              Lokale Suche")
    print(f"  POST /chat                Demo-Chat (3B Reasoning)")
    print(f"  POST /machine-query       NVMe Machine Core Abfrage")
    print(f"  POST /distributed-search  Verteilte Suche über Mesh")
    print(f"  GET  /health              Status + Mesh + Core")
    print(f"  GET  /peers               Peer-Liste")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        mesh.stop()
        if mc:
            mc.close()
    server.server_close()

if __name__ == "__main__":
    main()
