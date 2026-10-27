# providers/capability/groq/provider_api.py
#
# Live provider functions for Groq.
#
# All functions require an API key.  Resolution order:
#   1. Explicit api_key parameter     (from user session: config["api_key"])
#   2. Key in the OS credential store (providers.credentials; never os.environ)
#   3. Raises ProviderKeyError
#
# Uses the official groq SDK (AsyncGroq) which is already in requirements.
# Groq exposes the same rate-limit header format as OpenAI.

from __future__ import annotations

from typing import List, Optional

from providers import credentials as _credentials

from providers.capability.base import (
    LiveModel,
    ProviderAPIError,
    ProviderKeyError,
    ProviderStatus,
    RateLimitInfo,
    _safe_int,
)

PROVIDER = "groq"
ENV_KEY  = "GROQ_API_KEY"
BASE_URL = "https://api.groq.com/openai/v1"


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def is_available(api_key: Optional[str] = None) -> bool:
    """Return True if a Groq API key is present (session or env)."""
    return _credentials.is_configured(PROVIDER, api_key)


def _resolve_key(api_key: Optional[str] = None) -> str:
    key = _credentials.resolve_api_key(PROVIDER, api_key)
    if not key:
        raise ProviderKeyError(
            f"Groq API key not configured. "
            f"Add it under Profile → API Keys (POST /secrets/{PROVIDER})."
        )
    return key


# ---------------------------------------------------------------------------
# Live functions
# ---------------------------------------------------------------------------

async def list_models(api_key: Optional[str] = None) -> List[LiveModel]:
    """
    Fetch the live list of models accessible for this API key via Groq.

    Groq's models endpoint returns the model list in OpenAI-compatible format.
    Each model includes a context_window field.
    """
    from groq import AsyncGroq
    client = AsyncGroq(api_key=_resolve_key(api_key))
    try:
        response = await client.models.list()
        return [
            LiveModel(
                model_id=m.id,
                owned_by=getattr(m, "owned_by", None),
                context_window=getattr(m, "context_window", None),
                active=getattr(m, "active", True),
                created_at=str(m.created) if hasattr(m, "created") else None,
            )
            for m in sorted(response.data, key=lambda m: m.id)
        ]
    except Exception as exc:
        raise ProviderAPIError(f"Groq list_models failed: {exc}") from exc


async def get_model_info(
    model_id: str,
    api_key: Optional[str] = None,
) -> Optional[LiveModel]:
    """
    Retrieve a single model record from Groq.
    Returns None if the model is not found or not accessible.
    """
    from groq import AsyncGroq
    client = AsyncGroq(api_key=_resolve_key(api_key))
    try:
        m = await client.models.retrieve(model_id)
        return LiveModel(
            model_id=m.id,
            owned_by=getattr(m, "owned_by", None),
            context_window=getattr(m, "context_window", None),
            active=getattr(m, "active", True),
            created_at=str(m.created) if hasattr(m, "created") else None,
        )
    except Exception as exc:
        # Groq SDK raises a generic APIError for 404
        if "404" in str(exc) or "not found" in str(exc).lower():
            return None
        raise ProviderAPIError(f"Groq get_model_info({model_id}) failed: {exc}") from exc


async def validate_api_key(api_key: Optional[str] = None) -> bool:
    """
    Return True if the API key is valid (can list models successfully).
    """
    try:
        models = await list_models(api_key)
        return len(models) > 0
    except ProviderKeyError:
        return False
    except Exception:
        return False


async def get_rate_limits(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> RateLimitInfo:
    """
    Probe the Groq API and parse the rate-limit headers from the response.

    Groq rate-limit headers (same format as OpenAI):
      x-ratelimit-limit-requests
      x-ratelimit-remaining-requests
      x-ratelimit-reset-requests
      x-ratelimit-limit-tokens
      x-ratelimit-remaining-tokens
      x-ratelimit-reset-tokens

    Groq also returns per-day request limits:
      x-ratelimit-limit-requests-day  (if applicable to the tier)
    """
    import httpx
    key = _resolve_key(api_key)
    url = f"{BASE_URL}/models/{model}" if model else f"{BASE_URL}/models"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
        h = resp.headers
        return RateLimitInfo(
            requests_limit=_safe_int(h.get("x-ratelimit-limit-requests")),
            requests_remaining=_safe_int(h.get("x-ratelimit-remaining-requests")),
            requests_reset=h.get("x-ratelimit-reset-requests"),
            tokens_limit=_safe_int(h.get("x-ratelimit-limit-tokens")),
            tokens_remaining=_safe_int(h.get("x-ratelimit-remaining-tokens")),
            tokens_reset=h.get("x-ratelimit-reset-tokens"),
            requests_limit_day=_safe_int(h.get("x-ratelimit-limit-requests-day")),
            requests_remaining_day=_safe_int(h.get("x-ratelimit-remaining-requests-day")),
            source="headers",
        )
    except Exception as exc:
        raise ProviderAPIError(f"Groq get_rate_limits failed: {exc}") from exc


async def get_status(api_key: Optional[str] = None) -> ProviderStatus:
    """
    Return a ProviderStatus snapshot.
    Never raises; errors are captured in ProviderStatus.error.
    """
    if not is_available(api_key):
        return ProviderStatus(provider=PROVIDER, api_key_configured=False)
    try:
        models = await list_models(api_key)
        return ProviderStatus(
            provider=PROVIDER,
            api_key_configured=True,
            key_valid=True,
            live_model_count=len(models),
        )
    except Exception as exc:
        return ProviderStatus(
            provider=PROVIDER,
            api_key_configured=True,
            key_valid=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Phase-2 stubs
# ---------------------------------------------------------------------------
# async def count_tokens(text: str, model: str) -> int:
#     """Groq doesn't have a standalone tokenise endpoint; use tiktoken."""
#
# async def check_model_access(model_id: str, api_key) -> bool:
#     """Verify model is in the list returned by list_models()."""
