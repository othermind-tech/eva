import io
import tempfile
from logging import getLogger
from typing import List

import docx2txt
import pandas as pd
from pypdf import PdfReader

logger = getLogger(__name__)


# File extensions accepted for RAG indexing (text-extractable formats).
# Binary-only formats (images, archives, executables) are rejected -- see
# every caller of process_file_bytes() for exactly how the rejection is
# enforced (this module itself doesn't check, it just extracts).
INDEXABLE_EXTENSIONS: frozenset = frozenset({
    # Documents
    "pdf", "docx", "doc",
    # Structured data
    "csv", "xls", "xlsx",
    # Plain text / markup
    "txt", "md", "rst", "rtf",
    # Data serialisation
    "json", "yaml", "yml", "toml", "xml",
    # Web
    "html", "htm",
    # Source code — any plain-text source file is indexable
    "py", "js", "ts", "jsx", "tsx", "java", "cpp", "c", "h", "cs",
    "go", "rs", "rb", "php", "sh", "bash", "bat", "ps1",
    "sql", "r", "swift", "kt", "scala", "lua", "pl",
    # Logs / config
    "log", "ini", "cfg", "conf", "env",
})


# -------------------------
# Text extractors
# -------------------------

def extract_text_from_pdf_bytes(raw_bytes: bytes) -> str:
    logger.debug("Extracting text from PDF", extra={"size_bytes": len(raw_bytes)})
    reader = PdfReader(io.BytesIO(raw_bytes))
    if reader.is_encrypted:
        # Many PDFs are "encrypted" with an empty user password; try that first.
        # A genuinely password-protected file raises, matching the previous
        # PyMuPDF behaviour, so callers report a failure instead of silently
        # indexing an empty document.
        try:
            unlocked = bool(reader.decrypt(""))
        except Exception:
            unlocked = False
        if not unlocked:
            raise ValueError("PDF is password-protected")
    logger.debug("PDF opened", extra={"page_count": len(reader.pages)})
    text = ""
    for index, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
            # pypdf does not end a page with a newline (PyMuPDF did), so the
            # last word of one page would fuse with the first of the next.
            if page_text and not page_text.endswith("\n"):
                page_text += "\n"
            text += page_text
        except Exception:
            # One malformed page should not discard the rest of the document.
            logger.debug("PDF page skipped", extra={"page_index": index}, exc_info=True)
    logger.debug("PDF extraction completed", extra={"text_length": len(text)})
    return text


def extract_text_from_docx_bytes(raw_bytes: bytes) -> str:
    logger.debug("Extracting text from DOCX", extra={"size_bytes": len(raw_bytes)})
    # docx2txt requires a file path → controlled temp file
    with tempfile.NamedTemporaryFile(delete=True, suffix=".docx") as tmp:
        tmp.write(raw_bytes)
        tmp.flush()
        text = docx2txt.process(tmp.name)
    logger.debug("DOCX extraction completed", extra={"text_length": len(text)})
    return text


def extract_text_from_table_bytes(raw_bytes: bytes, suffix: str) -> str:
    logger.debug(
        "Extracting text from table",
        extra={"format": suffix, "size_bytes": len(raw_bytes)},
    )
    buffer = io.BytesIO(raw_bytes)
    df = (
        pd.read_csv(buffer)
        if suffix == "csv"
        else pd.read_excel(buffer)
    )
    text = "\n".join(df.astype(str).apply(lambda r: " | ".join(r), axis=1))
    logger.debug(
        "Table extraction completed",
        extra={"format": suffix, "text_length": len(text)},
    )
    return text


# -------------------------
# Chunking (unchanged logic)
# -------------------------

def chunk_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 200,
) -> List[str]:
    logger.debug(
        "Chunking text",
        extra={
            "text_length": len(text),
            "chunk_size": chunk_size,
            "overlap": overlap,
        },
    )
    chunks = []
    start = 0
    length = len(text)

    while start < length:
        end = min(start + chunk_size, length)
        chunks.append(text[start:end])
        start += (chunk_size - overlap)

    logger.debug("Text chunked", extra={"chunk_count": len(chunks)})
    return chunks


# -------------------------
# Main processor (NEW API)
# -------------------------

def process_file_bytes(raw_bytes: bytes, filename: str) -> List[str]:
    """
    Pure file processing:
    bytes → text → chunks
    """
    logger.info(
        "Processing file",
        extra={"source_filename": filename, "size_bytes": len(raw_bytes)},
    )

    suffix = filename.split(".")[-1].lower()
    logger.debug("File format detected", extra={"format": suffix})

    if suffix == "pdf":
        text = extract_text_from_pdf_bytes(raw_bytes)

    elif suffix in {"docx", "doc"}:
        text = extract_text_from_docx_bytes(raw_bytes)

    elif suffix in {"csv", "xls", "xlsx"}:
        text = extract_text_from_table_bytes(raw_bytes, suffix)

    else:
        logger.debug("Using UTF-8 decoding", extra={"source_filename": filename})
        text = raw_bytes.decode("utf-8", errors="ignore")

    chunks = chunk_text(text)
    logger.info(
        "File processing completed",
        extra={
            "source_filename": filename,
            "chunk_count": len(chunks),
            "format": suffix,
        },
    )
    return chunks
