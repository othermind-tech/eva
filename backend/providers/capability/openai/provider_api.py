# providers/capability/openai/provider_api.py
#
# Live provider functions for OpenAI.
#
# All functions require an API key.  Resolution order:
#   1. Explicit api_key parameter     (from user session: config["api_key"])
#   2. Key in the OS credential store (providers.credentials; never os.environ)
#   3. Raises ProviderKeyError
#
# Uses the official openai SDK (AsyncOpenAI) which is already in requirements.
# Rate-limit headers are captured from the raw httpx response underneath the SDK.

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

PROVIDER = "openai"
ENV_KEY  = "OPENAI_API_KEY"
BASE_URL = "https://api.openai.com/v1"


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def is_available(api_key: Optional[str] = None) -> bool:
    """Return True if an OpenAI API key is present (session or env)."""
    return _credentials.is_configured(PROVIDER, api_key)


def _resolve_key(api_key: Optional[str] = None) -> str:
    key = _credentials.resolve_api_key(PROVIDER, api_key)
    if not key:
        raise ProviderKeyError(
            f"OpenAI API key not configured. "
            f"Add it under Profile → API Keys (POST /secrets/{PROVIDER})."
        )
    return key


# ---------------------------------------------------------------------------
# Live functions
# ---------------------------------------------------------------------------

async def list_models(api_key: Optional[str] = None) -> List[LiveModel]:
    """
    Fetch the live list of models available for this API key from OpenAI.

    Returns only models whose IDs are returned by GET /v1/models.
    Note: the list reflects what the key can access, which may differ from
    the public model catalogue.
    """
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=_resolve_key(api_key))
    try:
        response = await client.models.list()
        return [
            LiveModel(
                model_id=m.id,
                owned_by=m.owned_by,
                created_at=str(m.created),
            )
            for m in sorted(response.data, key=lambda m: m.id)
        ]
    except Exception as exc:
        raise ProviderAPIError(f"OpenAI list_models failed: {exc}") from exc


async def get_model_info(
    model_id: str,
    api_key: Optional[str] = None,
) -> Optional[LiveModel]:
    """
    Retrieve a single model record from OpenAI.
    Returns None if the model is not found or not accessible.
    """
    from openai import AsyncOpenAI, NotFoundError
    client = AsyncOpenAI(api_key=_resolve_key(api_key))
    try:
        m = await client.models.retrieve(model_id)
        return LiveModel(
            model_id=m.id,
            owned_by=m.owned_by,
            created_at=str(m.created),
        )
    except NotFoundError:
        return None
    except Exception as exc:
        raise ProviderAPIError(f"OpenAI get_model_info({model_id}) failed: {exc}") from exc


async def validate_api_key(api_key: Optional[str] = None) -> bool:
    """
    Return True if the API key is valid (can list models successfully).
    Uses a lightweight GET /v1/models call.
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
    Probe the OpenAI API and parse the rate-limit headers from the response.

    OpenAI rate-limit headers (returned on every response):
      x-ratelimit-limit-requests        – max requests per minute / day
      x-ratelimit-remaining-requests    – requests left in current window
      x-ratelimit-reset-requests        – seconds until window resets
      x-ratelimit-limit-tokens          – max tokens per minute
      x-ratelimit-remaining-tokens      – tokens left in current window
      x-ratelimit-reset-tokens          – seconds until token window resets

    We capture them from a cheap GET /v1/models (or /v1/models/{model}) call.
    """
    import httpx
    key  = _resolve_key(api_key)
    url  = f"{BASE_URL}/models/{model}" if model else f"{BASE_URL}/models"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {key}"})
            resp.raise_for_status()
        h = resp.headers
        return RateLimitInfo(
            requests_limit=_safe_int(h.get("x-ratelimit-limit-requests")),
            requests_remaining=_safe_int(h.get("x-ratelimit-remaining-requests")),
            requests_reset=h.get("x-ratelimit-reset-requests"),
            tokens_limit=_safe_int(h.get("x-ratelimit-limit-tokens")),
            tokens_remaining=_safe_int(h.get("x-ratelimit-remaining-tokens")),
            tokens_reset=h.get("x-ratelimit-reset-tokens"),
            source="headers",
        )
    except Exception as exc:
        raise ProviderAPIError(f"OpenAI get_rate_limits failed: {exc}") from exc


async def get_status(api_key: Optional[str] = None) -> ProviderStatus:
    """
    Return a ProviderStatus snapshot — key presence, validity, live model count.
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
# Phase-2 stubs (not yet active — here for discoverability)
# ---------------------------------------------------------------------------
# async def get_usage(api_key, start_date, end_date) -> dict:
#     """GET /v1/usage — requires org-level key scope."""
#
# async def count_tokens(text: str, model: str) -> int:
#     """Use tiktoken locally — no API call needed."""
#
# async def check_model_access(model_id: str, api_key) -> bool:
#     """Verify the key can access a specific model (e.g. GPT-4 gating)."""
