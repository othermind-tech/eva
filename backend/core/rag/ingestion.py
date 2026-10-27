# core/rag/ingestion.py
"""
Index document chunks into ChromaDB.

Enforces tenant isolation via a 'tenant_id' metadata field on every point.
"""
from __future__ import annotations

import uuid
from logging import getLogger
from typing import List, Optional

from core.rag.client import get_chroma_client
from core.rag.models import Chunk
from config.settings import settings

logger = getLogger(__name__)


def index_chunks(
    tenant_id: str,
    chunks: List[Chunk],
    project_id: Optional[str] = None,
) -> None:
    """
    Index document chunks into ChromaDB.

    - Stores vector embeddings
    - Stores chunk text in metadata and documents
    - Enforces tenant isolation at DB level
    """
    if not chunks:
        return

    logger.info(
        "Indexing chunks",
        extra={
            "tenant_id": tenant_id,
            "chunk_count": len(chunks),
        },
    )

    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=settings.chroma.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    ids: List[str] = []
    embeddings: List[List[float]] = []
    documents: List[str] = []
    metadatas: List[dict] = []

    for chunk in chunks:
        ids.append(str(uuid.uuid4()))
        embeddings.append(chunk.vector)
        documents.append(chunk.text)
        metadata = {
            **chunk.metadata,
            "tenant_id": tenant_id,
            "text": chunk.text,
        }
        if project_id:
            metadata["project_id"] = project_id
        metadatas.append(metadata)

    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    logger.debug(
        "Chunks indexed successfully",
        extra={
            "tenant_id": tenant_id,
            "chunk_count": len(ids),
        },
    )
