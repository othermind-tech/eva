# api/secrets.py
#
# Secrets Management API  (V4.4 — Provider Key Configuration)
#
# Design principles:
#   • Write-only for secrets — no endpoint ever returns a key value or masked key.
#   • Provider API keys are written to the OS credential store (Windows
#     Credential Manager) through providers.credentials and are live at once --
#     every reader resolves from that store, so no restart is needed. They are
#     never copied into os.environ.  Non-secret config (OLLAMA_HOST) is
#     written to eva.env.
#   • After writing, each key is validated against the live provider API.
#   • Ollama is a local provider (no API key) — it uses a host URL instead.
#
# Endpoints:
#   POST /secrets/{provider}          — upsert API key for a cloud provider
#   DELETE /secrets/{provider}        — remove the stored API key (revoke a leaked/abandoned key)
#   GET  /secrets/ollama/base-url     — read current Ollama host (not a secret)
#   POST /secrets/ollama/base-url     — update OLLAMA_HOST in .env + os.environ
#
# Supported providers for POST /secrets/{provider}:
#   every provider in providers/registry.py that takes a key
#
# Auth is required on every request.

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth.dependencies import get_current_auth
from core.rate_limit import SlidingWindowRateLimiter
from config.settings import SECRETS_RATE_LIMIT_WINDOW_SECONDS, SECRETS_RATE_LIMIT_MAX_CALLS
from providers import credentials as _credentials
from providers import registry as _registry
from providers.capability.base import (
    OLLAMA_DEFAULT_HOST,
    OLLAMA_HOST_ENV,
    PROVIDER_KEY_MAP,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/secrets", tags=["Secrets"])

# B2-1.6 (2026-08-18): every call does a live provider-API validation
# round-trip -- rate limit it like the auth endpoints (core/rate_limit.py),
# keyed per-user since this endpoint already requires a valid JWT.
_secrets_rate_limiter = SlidingWindowRateLimiter(
    window_seconds=SECRETS_RATE_LIMIT_WINDOW_SECONDS,
    max_calls=SECRETS_RATE_LIMIT_MAX_CALLS,
    message="Too many provider key requests. Please wait before trying again.",
)

# eva.env file in the project root (one level up from this api/ directory)
_ENV_FILE = Path(__file__).parent.parent / "eva.env"

# Cloud providers — those that require an API key (all except ollama)
_CLOUD_PROVIDERS: frozenset = frozenset(
    k for k, v in PROVIDER_KEY_MAP.items() if v is not None
)


# ---------------------------------------------------------------------------
# Secret and config persistence helpers
# ---------------------------------------------------------------------------

def _write_secret(provider: str, value: str) -> str:
    """
    Store a provider API key in the OS credential store (Windows Credential
    Manager, service "eva") via providers.credentials -- the single key store
    every handler and capability call resolves from. Returns the entry name.

    The key is NOT copied into os.environ: nothing reads provider keys from
    there any more, and a copy would be inherited by every child process the
    agent starts. It is live immediately because readers consult the store
    (the credential cache is invalidated on write).
    """
    key_name = _credentials.set_api_key(provider, value)
    logger.info("[SECRETS] %s stored in the OS credential store", key_name)
    return key_name


def _write_config(key_name: str, value: str) -> None:
    """
    Write a non-secret configuration value (e.g. OLLAMA_HOST) to eva.env
    and activate it immediately in the running process via os.environ.

    Non-secret config values are not stored in Windows Credential Manager.
    """
    from dotenv import set_key
    _ENV_FILE.touch()
    set_key(str(_ENV_FILE), key_name, value)
    os.environ[key_name] = value
    logger.info("[SECRETS] %s written to eva.env and activated", key_name)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class SecretWriteRequest(BaseModel):
    api_key: str


class SecretWriteResponse(BaseModel):
    provider: str
    key_name: str
    configured: bool             # True = key is now present in the credential store
    valid: Optional[bool]        # True = validated OK; False = rejected; None = skipped
    message: str


class SecretInUse(BaseModel):
    """What still points at a provider whose key was just removed."""
    session_primary: bool = False       # the caller's session provider
    fallback_entries: int = 0           # entries in the caller's fallback chain
    agents: List[str] = []              # the caller's sub-agents pinned to it
    daemons: List[str] = []             # the caller's daemons pinned to it


class SecretDeleteResponse(BaseModel):
    provider: str
    key_name: str
    deleted: bool                # False = there was no stored key (idempotent)
    configured: bool             # always False after a delete
    in_use: SecretInUse
    warnings: List[str]
    message: str


class OllamaBaseUrlRequest(BaseModel):
    base_url: str


class OllamaBaseUrlResponse(BaseModel):
    base_url: str
    reachable: Optional[bool]   # None if reachability probe failed unexpectedly


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/{provider}",
    response_model=SecretWriteResponse,
    summary="Set or update the API key for a provider",
)
async def upsert_provider_secret(
    provider: str,
    body: SecretWriteRequest,
    auth=Depends(get_current_auth),
):
    """
    Write the API key for the given cloud provider to the OS credential store
    (Windows Credential Manager); it is live immediately (no restart required).

    **Security:**
    - The key is **never** echoed back in any response field.
    - After writing, the key is validated against the provider's live API.
    - `valid: false` → key was saved but rejected by the provider.
    - `valid: null` → validation was skipped (provider API unreachable).

    **Ollama** has no API key — use `POST /secrets/ollama/base-url` instead.

    **Supported providers:** every keyed provider in providers/registry.py.
    """
    await _secrets_rate_limiter.check_and_record(auth["user_id"])

    p = _registry.canonical_name(provider)

    # Ollama is a local provider — redirect callers to the correct endpoint.
    if p == "ollama":
        raise HTTPException(
            status_code=400,
            detail=(
                "Ollama is a local provider and does not use an API key. "
                "Use POST /secrets/ollama/base-url to configure the host URL."
            ),
        )

    # Unknown / unsupported provider.
    if p not in _CLOUD_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown provider '{p}'. "
                f"Supported: {sorted(_CLOUD_PROVIDERS)}. "
                f"For local providers use the dedicated base-url endpoint."
            ),
        )

    raw_key = body.api_key.strip() if body.api_key else ""
    if not raw_key:
        raise HTTPException(status_code=422, detail="api_key must not be empty.")

    key_name = _write_secret(p, raw_key)   # e.g. "GROQ_API_KEY"

    # Validate the key against the provider's live API (best-effort).
    valid: Optional[bool] = None
    validation_note = ""
    try:
        valid = await _validate_key(p, raw_key)
        if valid:
            validation_note = " Key validated successfully."
        else:
            validation_note = (
                " Warning: key was saved but was rejected by the provider API. "
                "Check that the key is correct and has the required permissions."
            )
    except Exception as exc:
        validation_note = f" Validation skipped ({type(exc).__name__}: {exc})."
        logger.debug("[SECRETS] Key validation skipped for %s: %s", p, exc)

    return SecretWriteResponse(
        provider=p,
        key_name=key_name,
        configured=True,
        valid=valid,
        message=f"{key_name} stored in the OS credential store and active in the running server.{validation_note}",
    )


async def _provider_dependents(user_id: str, provider: str) -> SecretInUse:
    """
    What of the caller's own configuration still names `provider`: session
    primary, fallback-chain entries, and agents/daemons pinned to it. Used to
    warn after a key is removed -- the key store is machine-wide but these
    references are per-user, so only the caller's are reported.
    """
    from sqlalchemy import select
    from config.settings import get_user_session
    from core.db.models import DaemonConfig, SubagentConfig
    from core.db.session import AsyncSessionLocal

    session = get_user_session(user_id)
    canon = _registry.canonical_name
    in_use = SecretInUse(
        session_primary=canon(session.get("provider") or "") == provider,
        fallback_entries=sum(
            1 for e in (session.get("fallback_chain") or [])
            if canon(e.get("provider") or "") == provider
        ),
    )
    async with AsyncSessionLocal() as db:
        for model, attr in ((SubagentConfig, "agents"), (DaemonConfig, "daemons")):
            rows = (await db.execute(
                select(model.name, model.provider)
                .where(model.user_id == user_id, model.provider.is_not(None))
            )).all()
            setattr(in_use, attr, sorted(n for n, p in rows if canon(p) == provider))
    return in_use


@router.delete(
    "/{provider}",
    response_model=SecretDeleteResponse,
    summary="Remove the stored API key for a provider",
)
async def delete_provider_secret(
    provider: str,
    auth=Depends(get_current_auth),
):
    """
    Remove the API key for the given cloud provider from the OS credential
    store (Windows Credential Manager) -- the way to revoke a leaked or
    abandoned key from EVA. It takes effect immediately: the provider stops
    being `chat_ready` on the next `GET /providers/list`.

    - **Idempotent:** deleting a provider with no stored key returns 200 with
      `deleted: false`.
    - Nothing is revoked *at the provider*; rotate/revoke the key in the
      provider's own console as well if it leaked.
    - The credential store is machine-wide, so this removes the key for every
      local EVA account.
    - **`in_use`/`warnings`** list what in the caller's own configuration still
      uses this provider (session provider, fallback entries, sub-agents,
      daemons). They are NOT changed -- re-point them, or add the key again.
    - **Ollama** has no API key (400).
    """
    await _secrets_rate_limiter.check_and_record(auth["user_id"])

    p = _registry.canonical_name(provider)

    if p == "ollama":
        raise HTTPException(
            status_code=400,
            detail="Ollama is a local provider and has no API key to delete.",
        )
    if p not in _CLOUD_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{p}'. Supported: {sorted(_CLOUD_PROVIDERS)}.",
        )

    try:
        deleted = _credentials.delete_api_key(p)
    except Exception as exc:   # credential store unavailable/locked
        logger.error("[SECRETS] could not delete the key for %s: %s", p, exc)
        raise HTTPException(
            status_code=503,
            detail="The OS credential store could not be reached; the key was not removed.",
        )
    key_name = _registry.PROVIDERS[p].env_key

    # The cached live model list was fetched with the removed key.
    from core.providers.resolver import resolver
    resolver.invalidate_cache(p)

    logger.info("[SECRETS] %s %s by user %s", key_name,
                "deleted" if deleted else "delete requested (no stored key)", auth["user_id"])

    in_use = await _provider_dependents(auth["user_id"], p)
    label = _registry.PROVIDERS[p].display_name
    warnings: List[str] = []
    if in_use.session_primary:
        warnings.append(f"Your session's provider is {label}; chat will fail until you choose another provider.")
    if in_use.fallback_entries:
        n = in_use.fallback_entries
        warnings.append(f"{n} fallback entr{'y uses' if n == 1 else 'ies use'} {label} and will be skipped.")
    if in_use.agents:
        warnings.append(f"Sub-agents pinned to {label}: {', '.join(in_use.agents)}.")
    if in_use.daemons:
        warnings.append(f"Daemons pinned to {label}: {', '.join(in_use.daemons)}.")

    return SecretDeleteResponse(
        provider=p,
        key_name=key_name,
        deleted=deleted,
        configured=False,
        in_use=in_use,
        warnings=warnings,
        message=(f"{key_name} removed from the OS credential store." if deleted
                 else f"No stored key for {label}; nothing to remove.")
                + " Revoke the key at the provider too if it was exposed.",
    )


@router.get(
    "/ollama/base-url",
    response_model=OllamaBaseUrlResponse,
    summary="Get the current Ollama base URL",
)
async def get_ollama_base_url(auth=Depends(get_current_auth)):
    """
    Return the currently active Ollama base URL.

    Resolution order:
    1. `OLLAMA_HOST` environment variable (set via `POST /secrets/ollama/base-url`)
    2. `OLLAMA_HOST` in `config/settings.py`
    3. Default: `http://localhost:11434`

    The base URL is **not** a secret — it is safe to expose.
    Also performs a lightweight reachability probe (`GET /api/tags`).
    """
    try:
        from config.settings import settings as _cfg
        cfg_host = getattr(_cfg, "OLLAMA_HOST", None)
    except Exception:
        cfg_host = None

    base_url = (
        os.getenv(OLLAMA_HOST_ENV)
        or cfg_host
        or OLLAMA_DEFAULT_HOST
    )

    reachable: Optional[bool] = None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{base_url.rstrip('/')}/api/tags")
            reachable = r.status_code < 500
    except Exception:
        reachable = False

    return OllamaBaseUrlResponse(base_url=base_url, reachable=reachable)


@router.post(
    "/ollama/base-url",
    response_model=OllamaBaseUrlResponse,
    summary="Set or update the Ollama base URL",
)
async def set_ollama_base_url(
    body: OllamaBaseUrlRequest,
    auth=Depends(get_current_auth),
):
    """
    Update `OLLAMA_HOST` in the `.env` file and activate it immediately.

    After saving, a reachability probe (`GET /api/tags`) is performed.
    - `reachable: true`  → Ollama server responded.
    - `reachable: false` → URL was saved, but the server is not currently reachable.

    The URL is **not** a secret and is returned in the response.
    """
    url = body.base_url.strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=422, detail="base_url must not be empty.")
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(
            status_code=422,
            detail="base_url must start with http:// or https://",
        )

    _write_config(OLLAMA_HOST_ENV, url)

    reachable: Optional[bool] = None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{url}/api/tags")
            reachable = r.status_code < 500
    except Exception:
        reachable = False

    return OllamaBaseUrlResponse(base_url=url, reachable=reachable)


# ---------------------------------------------------------------------------
# Internal — per-provider key validation dispatcher
# ---------------------------------------------------------------------------

async def _validate_key(provider: str, api_key: str) -> bool:
    """
    Call the provider-specific ``validate_api_key()`` function.

    Returns True if the key is accepted, False if rejected.
    Raises on unexpected errors (caller catches and treats as None).
    """
    try:
        if provider == "anthropic":
            from providers.capability.anthropic.provider_api import validate_api_key
            return await validate_api_key(api_key=api_key)
        elif provider == "openai":
            from providers.capability.openai.provider_api import validate_api_key
            return await validate_api_key(api_key=api_key)
        elif provider == "groq":
            from providers.capability.groq.provider_api import validate_api_key
            return await validate_api_key(api_key=api_key)
        else:
            # For providers without a dedicated validate_api_key() (cohere, mistral,
            # together_ai, gemini, etc.) we cannot do live validation yet.
            # Return None so callers know it was skipped.
            return None   # type: ignore[return-value]
    except Exception:
        return False
