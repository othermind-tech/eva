# providers/capability/anthropic/provider_api.py
#
# Live provider functions for Anthropic.
#
# All functions require an API key.  Resolution order:
#   1. Explicit api_key parameter     (from user session: config["api_key"])
#   2. Key in the OS credential store (providers.credentials; never os.environ)
#   3. Raises ProviderKeyError
#
# Uses httpx directly — the anthropic SDK is not a hard dependency.
# Anthropic's models endpoint: GET https://api.anthropic.com/v1/models
# Required headers: x-api-key, anthropic-version: 2023-06-01
#
# Anthropic rate-limit headers (returned on every response):
#   anthropic-ratelimit-requests-limit
#   anthropic-ratelimit-requests-remaining
#   anthropic-ratelimit-requests-reset
#   anthropic-ratelimit-tokens-limit
#   anthropic-ratelimit-tokens-remaining
#   anthropic-ratelimit-tokens-reset

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

PROVIDER    = "anthropic"
ENV_KEY     = "ANTHROPIC_API_KEY"
BASE_URL    = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def is_available(api_key: Optional[str] = None) -> bool:
    """Return True if an Anthropic API key is present (session or env)."""
    return _credentials.is_configured(PROVIDER, api_key)


def _resolve_key(api_key: Optional[str] = None) -> str:
    key = _credentials.resolve_api_key(PROVIDER, api_key)
    if not key:
        raise ProviderKeyError(
            f"Anthropic API key not configured. "
            f"Add it under Profile → API Keys (POST /secrets/{PROVIDER})."
        )
    return key


def _auth_headers(key: str) -> dict:
    return {
        "x-api-key": key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }


# ---------------------------------------------------------------------------
# Live functions
# ---------------------------------------------------------------------------

async def list_models(api_key: Optional[str] = None) -> List[LiveModel]:
    """
    Fetch the live list of models accessible for this API key via Anthropic.

    Anthropic's GET /v1/models endpoint returns a paginated list.
    Each model object has: id, display_name, created_at, type.
    """
    import httpx
    key = _resolve_key(api_key)
    url = f"{BASE_URL}/models"
    models: List[LiveModel] = []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Handle pagination — Anthropic uses cursor-based pagination
            params: dict = {"limit": 100}
            while True:
                resp = await client.get(url, headers=_auth_headers(key), params=params)
                resp.raise_for_status()
                data = resp.json()

                for m in data.get("data", []):
                    models.append(
                        LiveModel(
                            model_id=m["id"],
                            display_name=m.get("display_name"),
                            owned_by="anthropic",
                            active=True,
                            created_at=m.get("created_at"),
                        )
                    )

                # Stop if no more pages
                if not data.get("has_more"):
                    break
                last_id = data.get("last_id")
                if not last_id:
                    break
                params["after_id"] = last_id

        return sorted(models, key=lambda m: m.model_id)
    except Exception as exc:
        raise ProviderAPIError(f"Anthropic list_models failed: {exc}") from exc


async def get_model_info(
    model_id: str,
    api_key: Optional[str] = None,
) -> Optional[LiveModel]:
    """
    Retrieve a single model record from Anthropic.
    Returns None if the model is not found or not accessible.
    """
    import httpx
    key = _resolve_key(api_key)
    url = f"{BASE_URL}/models/{model_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_auth_headers(key))
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            m = resp.json()
            return LiveModel(
                model_id=m["id"],
                display_name=m.get("display_name"),
                owned_by="anthropic",
                active=True,
                created_at=m.get("created_at"),
            )
    except Exception as exc:
        if "404" in str(exc):
            return None
        raise ProviderAPIError(
            f"Anthropic get_model_info({model_id}) failed: {exc}"
        ) from exc


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
    Probe the Anthropic API and parse rate-limit headers from the response.

    Anthropic rate-limit headers:
      anthropic-ratelimit-requests-limit
      anthropic-ratelimit-requests-remaining
      anthropic-ratelimit-requests-reset     (ISO-8601 timestamp)
      anthropic-ratelimit-tokens-limit
      anthropic-ratelimit-tokens-remaining
      anthropic-ratelimit-tokens-reset       (ISO-8601 timestamp)
      anthropic-ratelimit-input-tokens-limit
      anthropic-ratelimit-input-tokens-remaining
      anthropic-ratelimit-input-tokens-reset
      anthropic-ratelimit-output-tokens-limit
      anthropic-ratelimit-output-tokens-remaining
      anthropic-ratelimit-output-tokens-reset

    We capture them from a cheap GET /v1/models (or /v1/models/{model}) call.
    """
    import httpx
    key = _resolve_key(api_key)
    url = f"{BASE_URL}/models/{model}" if model else f"{BASE_URL}/models"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_auth_headers(key))
            resp.raise_for_status()
        h = resp.headers
        return RateLimitInfo(
            requests_limit=_safe_int(h.get("anthropic-ratelimit-requests-limit")),
            requests_remaining=_safe_int(h.get("anthropic-ratelimit-requests-remaining")),
            requests_reset=h.get("anthropic-ratelimit-requests-reset"),
            tokens_limit=_safe_int(h.get("anthropic-ratelimit-tokens-limit")),
            tokens_remaining=_safe_int(h.get("anthropic-ratelimit-tokens-remaining")),
            tokens_reset=h.get("anthropic-ratelimit-tokens-reset"),
            source="headers",
        )
    except Exception as exc:
        raise ProviderAPIError(f"Anthropic get_rate_limits failed: {exc}") from exc


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
#     """POST /v1/messages/count_tokens — Anthropic's native token counter."""
#
# async def get_usage(api_key, start_date, end_date) -> dict:
#     """GET /v1/usage — workspace-level token usage (requires admin key)."""
