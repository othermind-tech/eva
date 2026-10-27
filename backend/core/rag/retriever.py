# core/rag/retriever.py
"""
Vector search against ChromaDB.

ChromaDB returns cosine *distance* (0 = identical, 2 = opposite).
We convert to a similarity score in [0, 1]:
    score = max(0.0, 1.0 - distance)
so callers always receive a score where higher = more relevant.
"""
from __future__ import annotations

from logging import getLogger
from typing import List, Optional

from core.rag.client import get_chroma_client
from core.rag.models import Chunk
from config.settings import settings

logger = getLogger(__name__)


def search_vectors(
    tenant_id: str,
    query_vector: List[float],
    top_k: int = 10,
    metadata_filter: Optional[list] = None,
) -> List[Chunk]:
    logger.debug(
        "Searching vectors",
        extra={
            "tenant_id": tenant_id,
            "top_k": top_k,
            "has_metadata_filter": metadata_filter is not None,
        },
    )

    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=settings.chroma.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    # Build ChromaDB where clause
    # Single tenant filter — if more filters present, wrap in $and
    filters = [{"tenant_id": tenant_id}]
    if metadata_filter:
        for f in metadata_filter:
            filters.append({f["key"]: f["match"]["value"]})

    where_clause = filters[0] if len(filters) == 1 else {"$and": filters}

    try:
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where=where_clause,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        logger.warning(
            "ChromaDB query failed",
            exc_info=exc,
            extra={"tenant_id": tenant_id},
        )
        return []

    ids        = (results.get("ids")       or [[]])[0]
    documents  = (results.get("documents") or [[]])[0]
    metadatas  = (results.get("metadatas") or [[]])[0]
    distances  = (results.get("distances") or [[]])[0]

    chunks: List[Chunk] = []
    for i, doc_id in enumerate(ids):
        text = documents[i] if i < len(documents) else ""
        meta = metadatas[i] if i < len(metadatas) else {}
        distance = distances[i] if i < len(distances) else 1.0
        # ChromaDB cosine distance ∈ [0, 2]; typical range [0, 1]
        # distance=0 → identical → score=1.0
        score = max(0.0, 1.0 - distance)
        chunks.append(
            Chunk(
                id=doc_id,
                text=text,
                metadata=meta,
                vector=[],
                score=score,
            )
        )

    logger.debug(
        "Vector search completed",
        extra={
            "tenant_id": tenant_id,
            "results_count": len(chunks),
        },
    )
    return chunks
