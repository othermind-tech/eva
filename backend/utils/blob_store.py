import pathlib
from logging import getLogger
from typing import Optional

from utils.workspace import get_user_rag_dir, get_user_files_dir

logger = getLogger(__name__)


def get_user_blob_dir(tenant_id: str) -> pathlib.Path:
    path = get_user_rag_dir(tenant_id)
    logger.debug(
        "User blob directory ready",
        extra={"tenant_id": tenant_id, "path": str(path)},
    )
    return path


def save_blob(tenant_id: str, filename: str, content: bytes) -> pathlib.Path:
    logger.debug(
        "Saving blob",
        extra={
            "tenant_id": tenant_id,
            "file_name": filename,
            "size_bytes": len(content),
        },
    )
    user_dir = get_user_blob_dir(tenant_id)
    file_path = user_dir / filename
    file_path.write_bytes(content)
    logger.debug(
        "Blob saved",
        extra={"tenant_id": tenant_id, "file_name": filename},
    )
    return file_path


def delete_blob(tenant_id: str, filename: str) -> bool:
    logger.debug(
        "Deleting blob",
        extra={"tenant_id": tenant_id, "file_name": filename},
    )
    file_path = get_user_blob_dir(tenant_id) / filename
    if file_path.exists():
        file_path.unlink()
        logger.debug(
            "Blob deleted",
            extra={"tenant_id": tenant_id, "file_name": filename},
        )
        return True
    logger.debug(
        "Blob not found for deletion",
        extra={"tenant_id": tenant_id, "file_name": filename},
    )
    return False


def list_blobs(tenant_id: str) -> list[dict]:
    logger.debug("Listing blobs", extra={"tenant_id": tenant_id})
    user_dir = get_user_blob_dir(tenant_id)
    blobs = [
        {"filename": f.name, "size_bytes": f.stat().st_size}
        for f in user_dir.iterdir()
        if f.is_file()
    ]
    logger.debug(
        "Blobs listed",
        extra={"tenant_id": tenant_id, "count": len(blobs)},
    )
    return blobs


# ---------------------------------------------------------------------------
# File library helpers (B-066) — stores raw files in files/ (not rag/)
# ---------------------------------------------------------------------------

def save_file_blob(
    tenant_id: str,
    filename: str,
    content: bytes,
    project_id: Optional[str] = None,
) -> pathlib.Path:
    """Save a raw attachment to the files/ blob store."""
    files_dir = get_user_files_dir(tenant_id, project_id=project_id)
    file_path = files_dir / filename
    file_path.write_bytes(content)
    logger.debug(
        "File blob saved",
        extra={"tenant_id": tenant_id, "file_name": filename, "project_id": project_id, "size": len(content)},
    )
    return file_path


def read_file_blob(
    tenant_id: str,
    filename: str,
    project_id: Optional[str] = None,
) -> Optional[bytes]:
    """Read raw bytes from the files/ blob store. Returns None if not found."""
    files_dir = get_user_files_dir(tenant_id, project_id=project_id)
    file_path = files_dir / filename
    if file_path.exists():
        return file_path.read_bytes()
    return None


def delete_file_blob(
    tenant_id: str,
    filename: str,
    project_id: Optional[str] = None,
) -> bool:
    """Delete a file from the files/ blob store. Returns True if deleted."""
    files_dir = get_user_files_dir(tenant_id, project_id=project_id)
    file_path = files_dir / filename
    if file_path.exists():
        file_path.unlink()
        logger.debug(
            "File blob deleted",
            extra={"tenant_id": tenant_id, "file_name": filename},
        )
        return True
    return False


def list_file_blobs(
    tenant_id: str,
    project_id: Optional[str] = None,
) -> list[dict]:
    """List raw files in the files/ blob store with metadata."""
    files_dir = get_user_files_dir(tenant_id, project_id=project_id)
    result = []
    for f in files_dir.iterdir():
        if f.is_file():
            result.append({
                "filename": f.name,
                "size_bytes": f.stat().st_size,
            })
    return result
