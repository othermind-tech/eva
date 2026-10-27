# providers/capability/base.py
#
# Shared data types returned by all provider_api.py modules.
#
# Every provider_api.py exposes the same set of functions using these types
# so callers can work with any provider without knowing which one.
#
# Key resolution (all functions that accept api_key) -- providers.credentials:
#   1. Explicit api_key param  — legacy per-call override
#   2. Key in the OS credential store (never os.environ)
#   3. Raises ProviderKeyError — neither is present

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from providers import credentials as _credentials
from providers import registry as _registry


# ---------------------------------------------------------------------------
# V4.4 — Centralised provider → env-var mapping (LiteLLM standard names)
# ---------------------------------------------------------------------------

#: Maps every known provider name to the env-var that holds its API key.
#: ``None`` means the provider is local (Ollama) and uses a host URL instead.
#: Derived from the single provider registry (providers/registry.py) -- edit
#: the registry, never this dict.
PROVIDER_KEY_MAP: Dict[str, Optional[str]] = _registry.key_map()

OLLAMA_HOST_ENV     = "OLLAMA_HOST"
OLLAMA_DEFAULT_HOST = "http://localhost:11434"


def get_provider_env_key(provider: str) -> Optional[str]:
    """Return the env-var name for a provider's API key, or ``None`` for local providers."""
    return PROVIDER_KEY_MAP.get(_registry.canonical_name(provider))


def is_provider_key_configured(
    provider: str,
    explicit_key: Optional[str] = None,
) -> bool:
    """
    Return True if `provider` can be used right now.

    Delegates to providers.credentials, the single key store: an explicit key
    (e.g. a fallback entry's own), else a key stored in the OS credential
    store. Local Ollama is always configured (a host URL, not a key); unknown
    providers never are. The process environment is NOT consulted.
    """
    return _credentials.is_configured(provider, explicit_key)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class LiveModel:
    """
    A model record fetched live from a provider's own API.
    Complements the static models_cache.json with real-time data.
    """
    model_id: str                           # exact ID to pass to the API
    display_name: Optional[str]  = None    # human-friendly name
    context_window: Optional[int] = None   # max input tokens (if returned)
    owned_by: Optional[str]      = None    # e.g. "openai", "meta", "google"
    active: bool                 = True    # False if deprecated / disabled
    created_at: Optional[str]    = None    # ISO-8601 or Unix epoch (as str)


@dataclass
class RateLimitInfo:
    """
    Current rate-limit snapshot parsed from API response headers.

    Most providers return per-request AND per-token limits.
    Fields are None when the provider doesn't expose a particular limit.
    """
    # Per-request limits
    requests_limit:     Optional[int] = None   # max requests per window
    requests_remaining: Optional[int] = None   # remaining in current window
    requests_reset:     Optional[str] = None   # when window resets (varies: ISO / "6s" / seconds)

    # Per-token limits
    tokens_limit:       Optional[int] = None
    tokens_remaining:   Optional[int] = None
    tokens_reset:       Optional[str] = None

    # Per-day limits (OpenAI-specific)
    requests_limit_day:     Optional[int] = None
    requests_remaining_day: Optional[int] = None

    source: str = "headers"   # "headers" | "api"


@dataclass
class ProviderStatus:
    """
    Quick health snapshot for one provider — returned by get_status().
    Used by GET /providers/status to let the front end show which
    providers are ready to use.
    """
    provider: str
    api_key_configured: bool            # key present (not necessarily valid)
    key_valid: Optional[bool] = None    # None = not checked
    live_model_count: Optional[int] = None
    error: Optional[str] = None         # last error message if key_valid=False


# ---------------------------------------------------------------------------
# V4.3 — Unified model / cost / usage types
# ---------------------------------------------------------------------------

@dataclass
class UnifiedModelInfo:
    """
    Combined view of a model returned by GET /providers/{provider}/models.
    Merges live listing data (from provider API) with static capability data.
    """
    provider: str
    model_id: str
    display_name: Optional[str]       = None
    context_window: Optional[int]     = None
    max_output_tokens: Optional[int]  = None
    owned_by: Optional[str]           = None
    active: bool                      = True
    # Feature flags (from capability registry; None = unknown)
    supports_streaming: Optional[bool]    = None
    supports_tool_calls: Optional[bool]   = None
    supports_reasoning: Optional[bool]    = None
    supports_vision: Optional[bool]       = None
    # Cost data
    cost_per_input_token: Optional[float]  = None
    cost_per_output_token: Optional[float] = None
    # Provenance
    source: str = "live"   # "live" | "registry" | "merged" | "litellm"


@dataclass
class ModelCostInfo:
    """Per-token pricing snapshot for a model."""
    provider: str
    model: str
    input_cost_per_token: Optional[float]   = None
    output_cost_per_token: Optional[float]  = None
    cost_source_url: Optional[str]          = None
    note: Optional[str]                     = None


@dataclass
class ProviderUsageSummary:
    """
    Aggregate token usage + estimated spend for one provider.
    Returned by GET /providers/costs.
    """
    provider: str
    model: str
    total_executions: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    estimated_cost_usd: Optional[float]   = None   # None if pricing data unavailable
    period_start: Optional[str]           = None   # ISO-8601
    period_end: Optional[str]             = None   # ISO-8601


@dataclass
class ProviderCreditStatus:
    """
    Live credit / billing status for a provider account.
    Populated on a best-effort basis; fields are None when unsupported.
    """
    provider: str
    balance_usd: Optional[float]       = None
    credits_remaining: Optional[float] = None
    hard_limit_usd: Optional[float]    = None
    is_free_tier: Optional[bool]       = None
    error: Optional[str]               = None   # last fetch error


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

ProviderKeyError = _credentials.ProviderKeyError   # defined beside the key store


class ProviderAPIError(RuntimeError):
    """Raised when a live provider API call fails unexpectedly."""


# ---------------------------------------------------------------------------
# Shared utility
# ---------------------------------------------------------------------------

def _safe_int(value: object) -> Optional[int]:
    """Parse an integer from a header string; return None on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
