# core/rag/deletion.py
"""
Delete all ChromaDB vectors associated with a specific file, then remove
the raw file blob from the blob store.

Public API:
    delete_file(tenant_id, file_name)
        User-level RAG: vectors filtered by tenant_id + source_filename.
        Deletes blob from ~/eva_workspace/{user_id}/rag/.

    delete_project_knowledge_file(tenant_id, project_id, filename)
        Project knowledge: vectors filtered by tenant_id + source_filename + project_id.
        Deletes blob from ~/eva_workspace/{user_id}/projects/{project_id}/knowledge/.
"""
from __future__ import annotations

from logging import getLogger

from core.rag.client import get_chroma_client
from utils.blob_store import delete_blob
from config.settings import settings

logger = getLogger(__name__)


def delete_file(tenant_id: str, file_name: str) -> dict:
    logger.info(
        "Deleting RAG file",
        extra={
            "tenant_id": tenant_id,
            "file_name": file_name,
        },
    )

    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=settings.chroma.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    # 1️⃣ Delete vectors — both conditions must match
    collection.delete(
        where={
            "$and": [
                {"tenant_id": {"$eq": tenant_id}},
                {"source_filename": {"$eq": file_name}},
            ]
        }
    )

    logger.debug(
        "Vectors deleted",
        extra={
            "tenant_id": tenant_id,
            "file_name": file_name,
        },
    )

    # 2️⃣ Delete blob
    blob_deleted = delete_blob(tenant_id, file_name)

    logger.info(
        "File deleted successfully",
        extra={
            "tenant_id": tenant_id,
            "file_name": file_name,
            "blob_deleted": blob_deleted,
        },
    )

    return {
        "vectors_deleted": True,
        "blob_deleted": blob_deleted,
    }


def delete_project_knowledge_file(
    tenant_id: str,
    project_id: str,
    filename: str,
) -> dict:
    """
    Delete a project-scoped knowledge file: removes ChromaDB vectors tagged with
    this project_id + source_filename, then deletes the blob from the knowledge/
    directory (NOT from the user-level rag/ directory).

    Used by DELETE /projects/{id}/knowledge/{filename}.
    """
    logger.info(
        "Deleting project knowledge file",
        extra={
            "tenant_id":  tenant_id,
            "project_id": project_id,
            "file_name":  filename,
        },
    )

    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=settings.chroma.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    # Delete vectors — all three conditions must match
    collection.delete(
        where={
            "$and": [
                {"tenant_id":        {"$eq": tenant_id}},
                {"source_filename":  {"$eq": filename}},
                {"project_id":       {"$eq": project_id}},
            ]
        }
    )

    logger.debug(
        "Project knowledge vectors deleted",
        extra={"tenant_id": tenant_id, "project_id": project_id, "file_name": filename},
    )

    # Delete blob from projects/{id}/knowledge/
    from utils.workspace import get_project_knowledge_dir
    knowledge_dir = get_project_knowledge_dir(tenant_id, project_id)
    blob_path = knowledge_dir / filename
    blob_deleted = False
    if blob_path.exists():
        blob_path.unlink()
        blob_deleted = True

    logger.info(
        "Project knowledge file deleted",
        extra={
            "tenant_id":   tenant_id,
            "project_id":  project_id,
            "file_name":   filename,
            "blob_deleted": blob_deleted,
        },
    )

    return {
        "vectors_deleted": True,
        "blob_deleted":    blob_deleted,
    }
