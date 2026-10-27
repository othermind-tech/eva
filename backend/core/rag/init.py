# core/rag/init.py
"""
Ensure the ChromaDB collection exists at startup.
Uses get_or_create_collection so this is always idempotent.
"""
from core.rag.client import get_chroma_client
from config.settings import settings


def ensure_rag_collection() -> None:
    client = get_chroma_client()
    client.get_or_create_collection(
        name=settings.chroma.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
