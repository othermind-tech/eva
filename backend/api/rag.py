import asyncio
import json
import uuid
from fastapi import APIRouter, Depends, UploadFile, HTTPException, Form
from logging import getLogger
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from core.auth.dependencies import get_current_auth
from utils.blob_store import save_blob, list_blobs, list_file_blobs
from utils.workspace import get_user_rag_dir, get_user_files_dir
from utils.file_utils import process_file_bytes
from core.rag.models import Chunk, DeleteFileRequest
from core.rag.embed import embed_text
from core.rag.ingestion import index_chunks
from core.rag.retriever import search_vectors
from core.rag.deletion  import delete_file
from config.settings import RAG_JOB_TTL_SECONDS
from core.execution.redis_registry import redis_execution_registry

logger = getLogger(__name__)
router = APIRouter(prefix="/rag", tags=["RAG"])

_RAG_JOB_TTL = RAG_JOB_TTL_SECONDS


class IngestFileResponse(BaseModel):
    job_id: str
    status: str
    filename: str


class IngestStatusResponse(BaseModel):
    job_id: str
    status: str
    filename: str
    chunks_done: int
    total_chunks: int
    error: Optional[str] = None


class RagSearchResultItem(BaseModel):
    text: str
    metadata: Dict[str, Any]
    score: float


class RagSearchResponse(BaseModel):
    query: str
    results: List[RagSearchResultItem]


class UserFileEntry(BaseModel):
    filename: str
    source: str  # "rag" | "library"
    rag_indexed: bool
    size_bytes: Optional[int] = None
    storage_path: str


class UserFilesListResponse(BaseModel):
    files: List[UserFileEntry]


class DeleteRagFileResponse(BaseModel):
    status: str
    file_name: str
    deleted: Dict[str, Any]


async def _set_rag_job_state(job_id: str, state: dict) -> None:
    """Write job state to the broker. Silent no-op on unexpected error."""
    try:
        client = await redis_execution_registry.connect()
        await client.set(f"rag_job:{job_id}", json.dumps(state), ex=_RAG_JOB_TTL)
    except Exception as exc:
        logger.warning("RAG job state write failed for job=%s: %s", job_id, exc)


async def _run_ingestion_job(
    job_id: str,
    user_id: str,
    filename: str,
    raw_bytes: bytes,
    blob_path: str,
    project_id: str | None = None,
) -> None:
    """
    Background coroutine that chunks, embeds, and indexes a document.
    Writes progress to the broker so GET /rag/ingest/{job_id}/status can report it.
    """
    await _set_rag_job_state(job_id, {
        "status": "processing",
        "filename": filename,
        "chunks_done": 0,
        "total_chunks": 0,
        "error": None,
    })

    try:
        # CPU-bound text extraction + chunking — run in thread pool.
        raw_chunks = await asyncio.to_thread(process_file_bytes, raw_bytes, filename)
        total = len(raw_chunks)
        logger.info("RAG job %s: chunked filename=%s → %d chunks", job_id, filename, total)

        await _set_rag_job_state(job_id, {
            "status": "processing",
            "filename": filename,
            "chunks_done": 0,
            "total_chunks": total,
            "error": None,
        })

        chunk_objs: list[Chunk] = []
        for i, text in enumerate(raw_chunks):
            # CPU-bound embedding — run in thread pool.
            vector = await asyncio.to_thread(embed_text, text)
            chunk_objs.append(Chunk(
                id=f"{user_id}:{filename}:{i}",
                text=text,
                vector=vector,
                metadata={
                    "source_filename": filename,
                    "chunk_index": i,
                    "blob_path": blob_path,
                    **({"project_id": project_id} if project_id else {}),
                },
            ))
            # Update progress every 5 chunks to throttle broker writes.
            if i % 5 == 4 or i == total - 1:
                await _set_rag_job_state(job_id, {
                    "status": "processing",
                    "filename": filename,
                    "chunks_done": i + 1,
                    "total_chunks": total,
                    "error": None,
                })

        # IO-bound ChromaDB add — run in thread pool.
        await asyncio.to_thread(index_chunks, user_id, chunk_objs, project_id)

        await _set_rag_job_state(job_id, {
            "status": "completed",
            "filename": filename,
            "chunks_done": total,
            "total_chunks": total,
            "error": None,
        })
        logger.info("RAG job %s completed: filename=%s chunks=%d", job_id, filename, total)

    except Exception as exc:
        logger.error("RAG job %s failed: %s", job_id, exc, exc_info=True)
        await _set_rag_job_state(job_id, {
            "status": "failed",
            "filename": filename,
            "chunks_done": 0,
            "total_chunks": 0,
            "error": str(exc),
        })


@router.post("/ingest", response_model=IngestFileResponse)
async def ingest_file(
    file: UploadFile,
    current_auth=Depends(get_current_auth),
):
    """
    Async user-level document ingestion (B-018).

    Accepts the file, saves it to the blob store, then immediately returns a
    job_id.  The actual chunking + embedding + ChromaDB indexing runs in the
    background.  Poll GET /rag/ingest/{job_id}/status for progress.

    For project-scoped knowledge (B-052), use POST /projects/{id}/knowledge
    instead — that endpoint stores blobs in the project knowledge/ directory
    and tags ChromaDB vectors with project_id for filtered retrieval.
    """
    user_id = current_auth["user_id"]
    filename = file.filename or "upload"
    logger.info("RAG ingest accepted: user=%s filename=%s", user_id, filename)

    raw_bytes = await file.read()

    # Save to blob store synchronously (fast local disk write).
    blob_path = save_blob(tenant_id=user_id, filename=filename, content=raw_bytes)

    job_id = str(uuid.uuid4())

    # Write initial queued state before launching the background task.
    await _set_rag_job_state(job_id, {
        "status": "queued",
        "filename": filename,
        "chunks_done": 0,
        "total_chunks": 0,
        "error": None,
    })

    # Fire-and-forget background task.
    asyncio.create_task(
        _run_ingestion_job(job_id, user_id, filename, raw_bytes, str(blob_path))
    )

    return {
        "job_id": job_id,
        "status": "queued",
        "filename": filename,
    }


@router.get("/ingest/{job_id}/status", response_model=IngestStatusResponse)
async def get_ingest_status(
    job_id: str,
    current_auth=Depends(get_current_auth),
):
    """
    Poll the status of an async ingestion job (B-018).

    Returns
    -------
    {job_id, status, filename, chunks_done, total_chunks, error}
    status: "queued" | "processing" | "completed" | "failed"
    """
    try:
        client = await redis_execution_registry.connect()
        raw = await client.get(f"rag_job:{job_id}")
    except Exception as exc:
        logger.warning("RAG job status read failed job=%s: %s", job_id, exc)
        raise HTTPException(status_code=503, detail="Job store unavailable")

    if raw is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    try:
        state = json.loads(raw)
    except Exception as exc:
        logger.error("RAG job state JSON parse failed for %s: %s — raw=%r", job_id, exc, raw[:200] if raw else None)
        raise HTTPException(status_code=500, detail="Malformed job state")

    return {"job_id": job_id, **state}


@router.post("/search", response_model=RagSearchResponse)
async def rag_search(
    query: str = Form(...),
    top_k: int = Form(10),
    file_name: str | None = Form(None),
    current_auth=Depends(get_current_auth),
):
    """
    Direct RAG search API (non-agent).
    """
    user_id = current_auth["user_id"]
    logger.info(
        "RAG search started",
        extra={
            "user_id": user_id,
            "query_length": len(query),
            "top_k": top_k,
            "file_name": file_name,
        },
    )

    query_vector = embed_text(query)
    logger.debug(
        "Query embedded",
        extra={
            "user_id": user_id,
            "vector_dim": len(query_vector),
        },
    )

    metadata_filter = []
    if file_name:
        metadata_filter.append(
            {"key": "source_filename", "match": {"value": file_name}}
        )

    results = search_vectors(
        tenant_id=current_auth["user_id"],
        query_vector=query_vector,
        top_k=top_k,
        metadata_filter=metadata_filter,
    )
    logger.info(
        "RAG search completed",
        extra={
            "user_id": user_id,
            "result_count": len(results),
        },
    )

    return {
        "query": query,
        "results": [
            {
                "text":     chunk.text,
                "metadata": chunk.metadata,
                "score":    chunk.score,   # B-030: similarity score (0–1, higher = more relevant)
            }
            for chunk in results
        ],
    }


@router.get("/files", response_model=UserFilesListResponse)
def list_user_files(
    include_library: bool = True,
    current_auth=Depends(get_current_auth),
):
    """
    List all files accessible to the user.

    Returns a unified list with a ``source`` field on each entry:
      - ``"rag"``     — files ingested into the RAG knowledge base (rag/ directory)
      - ``"library"`` — raw attachment files (files/ directory, B-066)

    Pass ``?include_library=false`` to return only RAG files (legacy behaviour).
    """
    user_id = current_auth["user_id"]
    logger.debug("Listing user files", extra={"user_id": user_id})

    # RAG blobs — list[dict] with filename + size_bytes
    rag_dir   = get_user_rag_dir(user_id)
    rag_files = list_blobs(user_id)
    result = [
        {
            "filename":     entry["filename"],
            "source":       "rag",
            "rag_indexed":  True,
            "size_bytes":   entry["size_bytes"],
            "storage_path": str(rag_dir / entry["filename"]),
        }
        for entry in rag_files
    ]

    # File library blobs (B-066 — returns dicts with filename + size_bytes)
    if include_library:
        files_dir     = get_user_files_dir(user_id)
        library_files = list_file_blobs(user_id)
        for entry in library_files:
            result.append({
                "filename":     entry["filename"],
                "source":       "library",
                "rag_indexed":  False,
                "size_bytes":   entry.get("size_bytes"),
                "storage_path": str(files_dir / entry["filename"]),
            })

    logger.debug(
        "User files listed",
        extra={"user_id": user_id, "total": len(result)},
    )
    return {"files": result}


@router.delete("/file", response_model=DeleteRagFileResponse)
def delete_rag_file(
    payload: DeleteFileRequest,
    current_auth=Depends(get_current_auth),
):
    user_id = current_auth["user_id"]
    logger.info(
        "File deletion requested",
        extra={
            "user_id": user_id,
            "file_name": payload.file_name,
        },
    )

    deleted = delete_file(
        tenant_id=current_auth["user_id"],
        file_name=payload.file_name,
    )
    logger.info(
        "File deleted",
        extra={
            "user_id": user_id,
            "file_name": payload.file_name,
            "deleted_vectors": deleted.get("vectors_deleted"),
            "deleted_blob": deleted.get("blob_deleted"),
        },
    )

    return {
        "status": "ok",
        "file_name": payload.file_name,
        "deleted": deleted,
    }
