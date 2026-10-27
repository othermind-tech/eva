"""
core/files/parser.py — B-066: Conversation Attachment File Parser

Parses an uploaded file into:
  - Extracted text (for inline context injection or RAG ingestion)
  - Base64 image data (for vision-capable models)
  - Token estimate (for inline vs RAG routing)

Reuses existing utils/file_utils.py extractors (PDF, DOCX, CSV/Excel).
Images are returned as base64 and forwarded to vision models as-is;
the caller must verify model.supports_vision before accepting an image attachment.
"""
import base64
import mimetypes
from logging import getLogger
from typing import Optional

from utils.file_utils import (
    extract_text_from_pdf_bytes,
    extract_text_from_docx_bytes,
    extract_text_from_table_bytes,
)

logger = getLogger(__name__)

# MIME types treated as images (vision model input)
IMAGE_MIME_TYPES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
})

IMAGE_SUFFIXES: frozenset[str] = frozenset({
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "tif",
})

# Text/code suffixes decoded directly as UTF-8
TEXT_SUFFIXES: frozenset[str] = frozenset({
    "txt", "md", "rst", "json", "yaml", "yml", "toml", "xml",
    "html", "htm", "css", "py", "js", "ts", "jsx", "tsx",
    "go", "rs", "java", "c", "cpp", "cc", "h", "hpp", "cs",
    "rb", "php", "sh", "bash", "bat", "ps1", "sql", "graphql",
    "proto", "ini", "cfg", "conf", "env", "log",
})

# Inline-vs-RAG threshold in tokens.
# Files with a token estimate below this are injected inline into the
# conversation context window.  Files at or above this are pushed to RAG
# (chunked + vector-indexed) and retrieved on demand.
# Default: 32 000 tokens (~24 000 words, ~90 pages of text).
INLINE_TOKEN_THRESHOLD = 32_000


def detect_mime_type(filename: str, raw_bytes: bytes) -> str:
    """Best-effort MIME detection: stdlib guess → magic bytes fallback."""
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        return mime
    # Magic bytes fallback
    if raw_bytes[:4] == b"%PDF":
        return "application/pdf"
    if raw_bytes[:2] == b"PK":
        # Could be DOCX, XLSX, PPTX — return generic zip-based Office MIME
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if raw_bytes[:3] in (b"\xff\xd8\xff",):
        return "image/jpeg"
    if raw_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def _estimate_tokens(text: str) -> int:
    """
    Rough token estimate.  GPT-style tokenisers average ~0.75 tokens/word
    (i.e. ~1.33 tokens/word).  We use a slightly conservative 1.3× multiplier.
    """
    word_count = len(text.split())
    return max(1, int(word_count * 1.3))


def parse_file(filename: str, raw_bytes: bytes) -> dict:
    """
    Parse file bytes into a structured result dict:

    {
        "attachment_type": "file" | "image",
        "mime_type":       str,
        "text":            str | None,   # extracted text (file type)
        "base64_data":     str | None,   # base64-encoded image (image type)
        "token_estimate":  int,
        "size_bytes":      int,
        "use_rag":         bool,         # True if token_estimate >= INLINE_TOKEN_THRESHOLD
    }

    Raises
    ------
    ValueError  — if the file type cannot be parsed/read.
    """
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = detect_mime_type(filename, raw_bytes)
    size = len(raw_bytes)

    logger.debug(
        "Parsing attachment",
        extra={"file_name": filename, "mime": mime, "suffix": suffix, "size": size},
    )

    # ── Image ─────────────────────────────────────────────────────────────────
    if mime in IMAGE_MIME_TYPES or suffix in IMAGE_SUFFIXES:
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        # Images: token estimate is a rough proxy (vision models charge by pixels,
        # not characters — use 85 tokens per ~750px tile as a practical default).
        # For attachment routing we treat all images as "inline" (no RAG path).
        token_est = max(85, size // 500)
        return {
            "attachment_type": "image",
            "mime_type": mime if mime in IMAGE_MIME_TYPES else f"image/{suffix}",
            "text": None,
            "base64_data": b64,
            "token_estimate": token_est,
            "size_bytes": size,
            "use_rag": False,   # images are always injected inline (vision models)
        }

    # ── PDF ───────────────────────────────────────────────────────────────────
    if suffix == "pdf" or mime == "application/pdf":
        try:
            text = extract_text_from_pdf_bytes(raw_bytes)
        except Exception as exc:
            raise ValueError(f"PDF extraction failed: {exc}") from exc

    # ── DOCX / DOC ────────────────────────────────────────────────────────────
    elif suffix in {"docx", "doc"}:
        try:
            text = extract_text_from_docx_bytes(raw_bytes)
        except Exception as exc:
            raise ValueError(f"DOCX extraction failed: {exc}") from exc

    # ── Spreadsheets ──────────────────────────────────────────────────────────
    elif suffix in {"csv", "xls", "xlsx"}:
        try:
            text = extract_text_from_table_bytes(raw_bytes, suffix)
        except Exception as exc:
            raise ValueError(f"Spreadsheet extraction failed: {exc}") from exc

    # ── Plain text / code ─────────────────────────────────────────────────────
    elif suffix in TEXT_SUFFIXES or mime.startswith("text/"):
        text = raw_bytes.decode("utf-8", errors="replace")

    # ── Fallback: try UTF-8 decode ────────────────────────────────────────────
    else:
        decoded = raw_bytes.decode("utf-8", errors="replace")
        # If > 5% replacement characters, likely binary — reject it.
        replacement_ratio = decoded.count("\ufffd") / max(len(decoded), 1)
        if replacement_ratio > 0.05:
            raise ValueError(
                f"Unsupported or unreadable file type '{suffix or mime}'. "
                "Upload a PDF, DOCX, TXT, CSV, XLSX, image, or code file."
            )
        text = decoded

    token_est = _estimate_tokens(text)
    use_rag = token_est >= INLINE_TOKEN_THRESHOLD

    logger.debug(
        "Attachment parsed",
        extra={
            "file_name": filename,
            "token_estimate": token_est,
            "use_rag": use_rag,
        },
    )

    return {
        "attachment_type": "file",
        "mime_type": mime,
        "text": text,
        "base64_data": None,
        "token_estimate": token_est,
        "size_bytes": size,
        "use_rag": use_rag,
    }
