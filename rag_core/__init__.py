"""Lokales IT-RAG – einheitliche API für Indizierung und Abfrage."""
from .config import BASE_DIR, LANCE_DB_PATH, TABLE_NAME
from .retrieval import KnowledgeRetriever
from .quality import is_indexable_content, classify_source

__all__ = [
    "BASE_DIR",
    "LANCE_DB_PATH",
    "TABLE_NAME",
    "KnowledgeRetriever",
    "is_indexable_content",
    "classify_source",
]
