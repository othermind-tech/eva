# providers/capability/ollama_cloud/provider_api.py
#
# Live provider functions for Ollama Cloud (https://ollama.com).
#
# Ollama Cloud exposes an OpenAI-compatible REST API at:
#   https://ollama.com/v1/
#
# Authentication:
#   Authorization: Bearer <OLLAMA_CLOUD_API_KEY>
#   Keys are provisioned at https://ollama.com/settings/keys
#
# Key differences from local Ollama (provider "ollama"):
#   - Requires a real API key (not a host URL)
#   - Models are fetched from the cloud, not a local instance
#   - Billing is subscription-based — no per-token cost data in LiteLLM
#   - Uses the OpenAI SDK / OpenAI-compatible REST, NOT the ollama Python SDK
#
# Key resolution order (all functions that accept api_key):
#   1. Explicit api_key param  — passed from user session
#   2. Key in the OS credential store (providers.credentials; never os.environ)
#   3. Raises ProviderKeyError

from __future__ import annotations

from typing import List, Optional

from providers import credentials as _credentials

from providers.capability.base import (
    LiveModel,
    ProviderAPIError,
    ProviderKeyError,
    ProviderStatus,
    RateLimitInfo,
)

PROVIDER = "ollama_cloud"
ENV_KEY  = "OLLAMA_CLOUD_API_KEY"
BASE_URL = "https://ollama.com/v1"


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def is_available(api_key: Optional[str] = None) -> bool:
    """Return True if an Ollama Cloud API key is present (session or env)."""
    return _credentials.is_configured(PROVIDER, api_key)


def _resolve_key(api_key: Optional[str] = None) -> str:
    key = _credentials.resolve_api_key(PROVIDER, api_key)
    if not key:
        raise ProviderKeyError(
            f"Ollama Cloud API key not configured. "
            f"Add it under Profile → API Keys (POST /secrets/{PROVIDER}). "
            f"Keys can be created at https://ollama.com/settings/keys"
        )
    return key


# ---------------------------------------------------------------------------
# Live functions
# ---------------------------------------------------------------------------

async def list_models(api_key: Optional[str] = None) -> List[LiveModel]:
    """
    Fetch the live list of models available on Ollama Cloud.

    Uses GET /v1/models (OpenAI-compatible endpoint).
    Returns all models accessible to the given API key.
    """
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=_resolve_key(api_key), base_url=BASE_URL)
    try:
        response = await client.models.list()
        models: List[LiveModel] = []
        for m in sorted(response.data, key=lambda m: m.id):
            models.append(
                LiveModel(
                    model_id=m.id,
                    display_name=m.id,
                    context_window=None,          # not surfaced in the list endpoint
                    owned_by=getattr(m, "owned_by", "ollama"),
                    active=True,
                    created_at=str(m.created) if getattr(m, "created", None) else None,
                )
            )
        return models
    except ProviderKeyError:
        raise
    except Exception as exc:
        raise ProviderAPIError(f"Ollama Cloud list_models failed: {exc}") from exc


async def get_model_info(
    model_id: str,
    api_key: Optional[str] = None,
) -> Optional[LiveModel]:
    """
    Retrieve a single model record from Ollama Cloud.
    Returns None if the model is not found or not accessible.
    """
    from openai import AsyncOpenAI, NotFoundError
    client = AsyncOpenAI(api_key=_resolve_key(api_key), base_url=BASE_URL)
    try:
        m = await client.models.retrieve(model_id)
        return LiveModel(
            model_id=m.id,
            display_name=m.id,
            context_window=None,
            owned_by=getattr(m, "owned_by", "ollama"),
            active=True,
            created_at=str(m.created) if getattr(m, "created", None) else None,
        )
    except NotFoundError:
        return None
    except ProviderKeyError:
        raise
    except Exception as exc:
        raise ProviderAPIError(
            f"Ollama Cloud get_model_info({model_id}) failed: {exc}"
        ) from exc


async def validate_api_key(api_key: Optional[str] = None) -> bool:
    """
    Return True if the API key is valid (can list models successfully).
    Uses a lightweight GET /v1/models call.
    """
    try:
        models = await list_models(api_key)
        return True  # reachable even if zero models returned
    except ProviderKeyError:
        return False
    except Exception:
        return False


async def get_rate_limits(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> RateLimitInfo:
    """
    Ollama Cloud is subscription-based — no standard rate-limit headers
    are documented.  Returns an empty RateLimitInfo (all fields None).
    """
    return RateLimitInfo()


async def get_status(api_key: Optional[str] = None) -> ProviderStatus:
    """
    Return a ProviderStatus snapshot — key presence, validity, live model count.
    Never raises; errors are captured in ProviderStatus.error.
    """
    if not is_available(api_key):
        return ProviderStatus(
            provider=PROVIDER,
            api_key_configured=False,
        )
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
