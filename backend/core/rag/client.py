# core/rag/client.py
"""
ChromaDB persistent client — lazy singleton.

The client is created on first call so that tests can patch
config.settings before the client is instantiated.
"""
from __future__ import annotations

import logging
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaClientSettings

from config.settings import settings

logger = logging.getLogger(__name__)

_chroma_client: Optional[chromadb.PersistentClient] = None


def get_chroma_client() -> chromadb.PersistentClient:
    """Return the module-level ChromaDB PersistentClient, creating it on first call."""
    global _chroma_client
    if _chroma_client is None:
        path = settings.chroma.CHROMA_PATH
        logger.info("[chroma] opening PersistentClient at %s", path)
        # Telemetry is off explicitly: ChromaDB's default is on, and the version
        # we lock (1.5.x) happens to ship a no-op sender, but requirements.txt
        # allows older releases (>=0.5) that report usage. EVA's privacy page
        # says nothing leaves the machine, so do not rely on the default.
        _chroma_client = chromadb.PersistentClient(
            path=path,
            settings=ChromaClientSettings(anonymized_telemetry=False),
        )
    return _chroma_client
