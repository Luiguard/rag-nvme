"""Zentrale Konfiguration für lokales IT-RAG."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

NVME_MOUNT = Path("/media/benjamin/36424239-0023-4f7b-b608-d21302f9a053")

if NVME_MOUNT.exists() and os.path.ismount(str(NVME_MOUNT)):
    DATA_DIR = Path(os.environ.get("RAG_DATA_DIR", str(NVME_MOUNT / "data")))
    LANCE_DB_PATH = NVME_MOUNT / "lancedb_index"
    NVME_KNOWLEDGE_PATH = NVME_MOUNT / "nvme_knowledge.dat"
else:
    DATA_DIR = Path(os.environ.get("RAG_DATA_DIR", str(BASE_DIR / "data")))
    LANCE_DB_PATH = BASE_DIR / "lancedb_index"
    NVME_KNOWLEDGE_PATH = BASE_DIR / "nvme_knowledge.dat"

TABLE_NAME_LEGACY = "it_knowledge"
TABLE_NAME_PRIME = "it_prime"
TABLE_NAME = os.environ.get("RAG_TABLE", TABLE_NAME_PRIME)

PRIORITY_DATA_ROOTS = tuple(
    os.environ.get(
        "RAG_PRIORITY_ROOTS",
        "custom_docs"
    ).split(",")
)

# Optional: Limit beim Prime-Build (Tests)
INDEX_FILE_LIMIT = int(os.environ.get("RAG_INDEX_FILE_LIMIT", "0"))  # 0 = alle

# L2-Distanz: kleiner = besser (LanceDB default)
MAX_L2_DISTANCE = float(os.environ.get("RAG_MAX_L2_DISTANCE", "1.35"))

EMBEDDING_MODEL = os.environ.get(
    "RAG_EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)
VECTOR_DIM = 384

RAG_LAZY_EMBEDDING = os.environ.get("RAG_LAZY_EMBEDDING", "1") == "1"

CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "200"))

# Retrieval
TOP_K_FETCH = int(os.environ.get("RAG_TOP_K_FETCH", "32"))
TOP_K_CONTEXT = int(os.environ.get("RAG_TOP_K", "8"))
TOP_K_USER = int(os.environ.get("RAG_TOP_K_USER", "4"))  # eigene Projekte
MIN_SIMILARITY = float(os.environ.get("RAG_MIN_SCORE", "0.42"))  # nur für Anzeige
MAX_CONTEXT_CHARS = int(os.environ.get("RAG_CONTEXT_MAX_CHARS", "14000"))

# Ollama (lokale KI)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("RAG_OLLAMA_MODEL", "")  # leer = Auto-Detect
OLLAMA_NUM_CTX = int(os.environ.get("RAG_OLLAMA_CTX", "8192"))
OLLAMA_TEMPERATURE = float(os.environ.get("RAG_OLLAMA_TEMP", "0.15"))

# Optional: Smartphone-NPU für Embeddings
MOBILE_NODE_URL = os.environ.get(
    "RAG_MOBILE_EMBED_URL", "http://192.168.68.58:5000/v1/embeddings"
)
MOBILE_EMBED_TIMEOUT = int(os.environ.get("RAG_MOBILE_TIMEOUT", "15"))

# Indizierung
DELETE_SOURCE_AFTER_INDEX = os.environ.get("RAG_DELETE_SOURCES", "1") == "1"
INDEX_BATCH_SIZE = int(os.environ.get("RAG_INDEX_BATCH", "5000"))

SKIP_DIRS = frozenset({
    "dumps", ".git", "__pycache__", "node_modules", "tests", ".venv",
    "processed", "github", "Documentation", "drivers", "tools",
})

TEXT_EXTENSIONS = frozenset({".txt", ".rst", ".md"})

# Deine Projekte indexieren („KI kennt deinen Code“)
USER_WORKSPACE_ROOTS = tuple(
    Path(p.strip()).expanduser()
    for p in os.environ.get("RAG_USER_ROOTS", str(Path.home() / "projects")).split(",")
    if p.strip()
)
CODE_EXTENSIONS = frozenset({
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".cpp", ".h", ".hpp",
    ".sh", ".bash", ".zsh", ".yaml", ".yml", ".toml", ".json", ".md", ".txt",
    ".sql", ".graphql", ".vue", ".svelte",
})
USER_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    "lancedb_index", "data/processed", "data/dumps", "data/mdn-web-docs",
    ".cursor", "target", "vendor", ".next", ".cache",
})
MAX_USER_FILE_BYTES = int(os.environ.get("RAG_MAX_USER_FILE_KB", "512")) * 1024

# Bevorzugte Ollama-Modelle (Reihenfolge)
OLLAMA_MODEL_PREFERENCE = [
    "qwen2.5-coder:32b",
    "qwen2.5-coder",
    "deepseek-coder-v2",
    "deepseek-coder",
    "codellama",
    "llama3.1",
    "llama3",
    "mistral",
    "gemma2",
]
