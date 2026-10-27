# providers/capability/ollama/provider_api.py
#
# Ollama local-LLM provider capability module.
#
# Ollama is a self-hosted LLM inference server (https://ollama.com).
# Unlike cloud providers it requires NO API key — only a reachable host.
#
# Key differences vs cloud providers
# ------------------------------------
# - No API key: the host URL replaces it; resolution order:
#     explicit host param → OLLAMA_HOST env var → http://localhost:11434
# - Dynamic model list: models are installed/removed via `ollama pull/rm`.
#   list_models() always does a live fetch from the running Ollama instance.
# - No cloud rate limits: get_rate_limits() returns an empty RateLimitInfo.
# - validate_api_key() pings the server and returns True if reachable.
#
# The "api_key" field in the EVA session config is repurposed as an
# optional custom host URL for Ollama (e.g. "http://192.168.1.10:11434").

from __future__ import annotations

import logging
import os
from typing import Optional

from providers.capability.base import (
    LiveModel,
    ProviderAPIError,
    ProviderStatus,
    RateLimitInfo,
)

logger = logging.getLogger(__name__)

PROVIDER = "ollama"
ENV_HOST = "OLLAMA_HOST"


def _default_host() -> str:
    """
    Resolve OLLAMA_HOST in priority order:
      1. Shell environment variable (os.environ) — always wins
      2. Central settings object  (reads .env via pydantic-settings)
      3. .env file direct read    (fallback when settings CWD differs)
      4. Hard-coded default       (http://localhost:11434)
    """
    # 1. Shell env var — fastest and most explicit
    env_host = os.getenv(ENV_HOST)
    if env_host:
        return env_host

    # 2. Central settings (reads .env via absolute path — see config/settings.py)
    try:
        from config.settings import settings
        host = settings.ollama.OLLAMA_HOST
        if host != "http://localhost:11434":   # non-default means it was overridden
            return host
    except Exception:
        pass

    # 3. Direct .env read as last resort (CWD-independent via __file__ anchor)
    try:
        from pathlib import Path
        from dotenv import dotenv_values
        _root = Path(__file__).resolve().parent.parent.parent.parent
        vals = dotenv_values(_root / "eva.env")
        if vals.get(ENV_HOST):
            return vals[ENV_HOST]
    except Exception:
        pass

    return "http://localhost:11434"


# ---------------------------------------------------------------------------
# Host resolution
# ---------------------------------------------------------------------------

def _get_host(host: Optional[str] = None) -> str:
    """
    Resolve the Ollama host in order:
      1. Explicit host parameter (from session api_key field)
      2. OLLAMA_HOST via config/settings.py  (reads .env automatically)
      3. OLLAMA_HOST environment variable (bare fallback)
      4. http://localhost:11434 (hardcoded last resort)
    """
    return host or _default_host()


# ---------------------------------------------------------------------------
# Public provider API functions
# ---------------------------------------------------------------------------

async def list_models(
    api_key: Optional[str] = None,
    host: Optional[str] = None,
) -> list[LiveModel]:
    """
    List all models currently installed on the Ollama instance.

    Parameters
    ----------
    api_key : Optional[str]
        Ignored for Ollama — included for interface compatibility only.
    host : Optional[str]
        Custom Ollama host URL. Falls back to OLLAMA_HOST or localhost.

    Returns
    -------
    list[LiveModel]
        One LiveModel per installed model. context_window is None (Ollama
        does not expose it in the list endpoint; we rely on the capability
        registry defaults).
    """
    try:
        from ollama import AsyncClient
        client = AsyncClient(host=_get_host(host))
        response = await client.list()
        models: list[LiveModel] = []
        for m in response.models:
            name: str = m.model or ""
            if not name:
                continue
            # Derive a friendlier display name (strip :latest tag)
            display = name.removesuffix(":latest") if name.endswith(":latest") else name
            # parameter_size is a string like "7B", "70B" etc — store as display hint only
            models.append(
                LiveModel(
                    model_id=name,
                    display_name=display,
                    context_window=None,          # not surfaced in list endpoint
                    owned_by="ollama",
                    active=True,
                    created_at=None,
                )
            )
        return models
    except Exception as exc:
        raise ProviderAPIError(f"Failed to list Ollama models: {exc}") from exc


async def get_model_info(
    model_id: str,
    api_key: Optional[str] = None,
    host: Optional[str] = None,
) -> Optional[LiveModel]:
    """Return a single LiveModel if the model is installed, else None."""
    try:
        models = await list_models(host=host)
        for m in models:
            if m.model_id == model_id:
                return m
        return None
    except ProviderAPIError:
        return None


async def validate_api_key(
    api_key: Optional[str] = None,
    host: Optional[str] = None,
) -> bool:
    """
    'Validate' for Ollama means: is the server reachable?
    Returns True if at least one model is listed (server is up).
    api_key is interpreted as a custom host URL if provided.
    """
    try:
        models = await list_models(host=host or api_key or None)
        return True  # reachable even with zero models installed
    except Exception:
        return False


async def get_rate_limits(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    host: Optional[str] = None,
) -> RateLimitInfo:
    """Ollama is local — no cloud rate limits."""
    return RateLimitInfo()


async def get_status(
    api_key: Optional[str] = None,
    host: Optional[str] = None,
) -> ProviderStatus:
    """
    Return provider status.
    api_key is treated as an optional custom host URL (same as validate_api_key).
    """
    effective_host = host or api_key or None
    try:
        models = await list_models(host=effective_host)
        return ProviderStatus(
            api_key_configured=True,   # no key needed
            key_valid=True,
            live_model_count=len(models),
            error=None,
        )
    except ProviderAPIError as exc:
        return ProviderStatus(
            api_key_configured=True,
            key_valid=False,
            live_model_count=0,
            error=str(exc),
        )
