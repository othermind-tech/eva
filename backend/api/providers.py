# api/providers.py
#
# Provider Capability & Model Listing API  (V4.5 — Unified Provider Flow)
#
# Endpoints:
#
#   GET  /providers/list                            – curated 10-provider list (frontend dropdown)
#   GET  /providers/capabilities                    – full static capability registry (renamed)
#   GET  /providers/costs                           – aggregate token usage + cost
#   POST /providers/models/refresh                  – invalidate resolver TTL cache
#   GET  /providers/{provider}/models               – live model listing via resolver (+ TTL cache)
#   GET  /providers/{provider}/models/{model_id}/capabilities  – per-model capability detail
#
# IMPORTANT — FastAPI route declaration order:
#   Fixed paths (/list, /capabilities, /costs, /models/refresh) MUST be declared before
#   any parameterised routes (/{provider}/models, /{provider}/models/.../capabilities).
#   FastAPI matches in declaration order; reversing this would cause "list" to be treated
#   as a provider name.
#
# Auth is required on every request.

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder

from api._etag import etag_response
from pydantic import BaseModel

from core.auth.dependencies import get_current_auth
from core.providers import capability_registry
from core.providers.capability_registry import ModelCapability
from core.providers.resolver import resolver
from core.db.session import AsyncSessionLocal
from core.db.repository import ExecutionMetricsRepo
from providers import registry as _registry
from providers.capability.base import (
    get_provider_env_key,
    is_provider_key_configured,
    PROVIDER_KEY_MAP,
)

router = APIRouter(prefix="/providers", tags=["Providers"])


# ---------------------------------------------------------------------------
# Curated provider list  (V4.5)
# ---------------------------------------------------------------------------

#: Providers offered by the unified frontend flow -- derived from the single
#: provider registry (providers/registry.py); edit the registry, not this list.
#: Order: native providers first, then LiteLLM-gateway providers.
_CURATED_PROVIDERS = [
    {
        "provider": s.name,
        "display_name": s.display_name,
        "is_native": s.is_native,
        "is_local": s.is_local,
    }
    for s in _registry.curated_specs()
]


# ---------------------------------------------------------------------------
# Response schemas — static capability registry
# ---------------------------------------------------------------------------

class ModelCapabilityOut(BaseModel):
    provider: str
    model: str

    # Token limits
    max_context_tokens: Optional[int]
    max_output_tokens: Optional[int]

    # Feature flags
    supports_streaming: bool
    supports_tool_calls: bool
    supports_reasoning: bool
    supports_vision: bool
    supports_json_schema: bool
    supports_file_upload: bool
    supports_usage_reporting: bool

    # Pricing
    cost_per_input_token: Optional[float]
    cost_per_output_token: Optional[float]

    # Provider-specific
    token_speed_tps: Optional[int]

    # Provenance
    source: str


class ProviderOut(BaseModel):
    provider: str
    model_count: int
    is_native: bool          # True if EVA has a dedicated native handler
    is_local: bool           # True for local providers (Ollama) — no cost
    models: List[ModelCapabilityOut]


# ---------------------------------------------------------------------------
# Response schemas — curated provider list  (V4.5)
# ---------------------------------------------------------------------------

class ProviderListItemOut(BaseModel):
    provider: str           # canonical provider identifier used in all API calls
    display_name: str       # human-friendly label for UI dropdowns
    is_native: bool         # True → EVA has a dedicated native handler
    is_local: bool          # True → local inference (Ollama), no cost
    is_configured: bool     # True → API key / host is present on this server
    # V6.59: configured AND EVA has a working chat path for it. This -- not
    # is_configured -- is what selectors (session, agent, daemon, fallback)
    # should filter on: a configured provider is always able to chat.
    chat_ready: bool


# ---------------------------------------------------------------------------
# Response schemas — per-model capabilities  (V4.5)
# ---------------------------------------------------------------------------

class ModelCapabilitiesOut(BaseModel):
    provider: str
    model_id: str           # bare model name as used in set-session calls

    # Token limits (None = unknown)
    max_context_tokens: Optional[int]
    max_output_tokens: Optional[int]

    # Feature flags (None = unknown for this model/source)
    supports_streaming: Optional[bool]
    supports_tool_calls: Optional[bool]
    supports_reasoning: Optional[bool]
    supports_vision: Optional[bool]
    supports_json_schema: Optional[bool]
    supports_file_upload: Optional[bool]
    supports_usage_reporting: Optional[bool]

    # V6.7: which reasoning levels this model accepts, only meaningful
    # when supports_reasoning is true. Ordered weakest -> strongest, a
    # subset of core.providers.reasoning_support.REASONING_LEVELS
    # ("minimal"/"low"/"medium"/"high"/"xhigh"). Empty/None when
    # supports_reasoning is false or unknown. Frontend: render a level
    # picker with exactly these options; if this model can't disable
    # reasoning at all, reasoning_can_disable is false and the "Off"
    # option should not be offered.
    reasoning_levels: Optional[List[str]] = None
    reasoning_can_disable: Optional[bool] = None

    # Pricing (None = unknown or free for local)
    cost_per_input_token: Optional[float]
    cost_per_output_token: Optional[float]

    # "reliable" | "unreliable" | None (unknown).
    # Models flagged "unreliable" intermittently generate malformed tool calls.
    tool_call_reliability: Optional[str] = None

    # Provenance: "registry" | "litellm" | "merged" | "unknown"
    source: str


# ---------------------------------------------------------------------------
# Response schemas — live model listing
# ---------------------------------------------------------------------------

class LiveModelOut(BaseModel):
    model_id: str
    display_name: Optional[str]
    context_window: Optional[int]
    owned_by: Optional[str]
    active: bool
    cost_per_input_token: Optional[float]   = None
    cost_per_output_token: Optional[float]  = None
    # V6.59: None = unknown (no capability data for this model).
    supports_tool_calls: Optional[bool]     = None
    # False for models EVA's agent loop cannot drive (embedding/image/audio
    # models, or models known to lack tool calling). Unknown counts as usable.
    agent_usable: bool                      = True


class LiveProviderModelsOut(BaseModel):
    provider: str
    is_native: bool
    is_local: bool
    chat_ready: bool = True
    model_count: int
    models: List[LiveModelOut]
    cached: bool        # True if result came from TTL cache (not a fresh fetch)


# ---------------------------------------------------------------------------
# Response schemas — costs
# ---------------------------------------------------------------------------

class ProviderCostRow(BaseModel):
    provider: str
    model: str
    total_executions: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost_usd: Optional[float]   # None if no pricing data available


class ProviderCostsOut(BaseModel):
    user_id: str
    period_start: Optional[str]
    period_end: Optional[str]
    rows: List[ProviderCostRow]
    grand_total_tokens: int
    grand_total_cost_usd: Optional[float]


# ---------------------------------------------------------------------------
# Response schemas — cache refresh
# ---------------------------------------------------------------------------

class CacheRefreshOut(BaseModel):
    provider: Optional[str]
    message: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_out(cap: ModelCapability) -> ModelCapabilityOut:
    """Convert a ModelCapability dataclass to the API response schema."""
    return ModelCapabilityOut(
        provider=cap.provider,
        model=cap.model,
        max_context_tokens=cap.max_context_tokens,
        max_output_tokens=cap.max_output_tokens,
        supports_streaming=cap.supports_streaming,
        supports_tool_calls=cap.supports_tool_calls,
        supports_reasoning=cap.supports_reasoning,
        supports_vision=cap.supports_vision,
        supports_json_schema=cap.supports_json_schema,
        supports_file_upload=cap.supports_file_upload,
        supports_usage_reporting=cap.supports_usage_reporting,
        cost_per_input_token=cap.cost_per_input_token,
        cost_per_output_token=cap.cost_per_output_token,
        token_speed_tps=getattr(cap, "token_speed_tps", None),
        source=cap.source,
    )


def _group_by_provider(caps: List[ModelCapability]) -> List[ProviderOut]:
    """Group a flat list of ModelCapability records into per-provider blocks."""
    groups: dict[str, List[ModelCapabilityOut]] = {}
    for cap in caps:
        groups.setdefault(cap.provider, []).append(_to_out(cap))

    result = []
    for provider, models in sorted(groups.items()):
        resolution = resolver.resolve(provider, "")
        result.append(ProviderOut(
            provider=provider,
            model_count=len(models),
            is_native=resolution.is_native,
            is_local=resolution.is_local,
            models=sorted(models, key=lambda m: m.model),
        ))
    return result


def _reasoning_level_fields(provider: str, model_id: str, supports_reasoning: Optional[bool]) -> tuple:
    """
    V6.7: (reasoning_levels, reasoning_can_disable) for ModelCapabilitiesOut.
    Only meaningful when supports_reasoning is true -- returns (None, None)
    otherwise so the frontend can tell "no reasoning" apart from "reasoning,
    but we don't know the level set" (empty list).
    """
    if not supports_reasoning:
        return None, None
    from core.providers.reasoning_support import get_reasoning_support  # noqa: PLC0415
    support = get_reasoning_support(provider, model_id)
    return list(support.levels), support.can_disable


def _model_usability(provider: str, model_id: str) -> tuple:
    """
    V6.59: (supports_tool_calls, agent_usable) for one listed model -- the same
    judgement the run-time gate makes (core/providers/usability.py), so a model
    the picker hides is exactly one a run would refuse.
    """
    from core.providers.usability import model_usability  # noqa: PLC0415
    tools, usable, _reason = model_usability(provider, model_id)
    return tools, usable


def _capabilities_from_litellm(provider: str, model_id: str) -> Optional[ModelCapabilitiesOut]:
    """
    Build a ModelCapabilitiesOut from LiteLLM's model_cost dictionary.

    Tries, in order: the gateway-routing prefixed form (e.g. "azure/gpt-4o",
    correct when `provider` routes through LiteLLM); the LiteLLM *routing*
    key built directly as "{provider}/{model_id}" (needed for native
    providers -- resolver.resolve()'s prefixed_model deliberately returns the
    bare form for these, since that's what their own SDK wants, but LiteLLM's
    OWN dataset still indexes them by the full routing key regardless, e.g.
    "groq/openai/gpt-oss-20b"); then the bare model_id (covers native
    providers like anthropic/openai whose LiteLLM key has no prefix at all).
    Returns None if the model is not present in the LiteLLM dataset at all.
    """
    try:
        import litellm  # type: ignore[import]
    except ImportError:
        return None

    resolution = resolver.resolve(provider, model_id)
    info = (
        litellm.model_cost.get(resolution.prefixed_model)
        or litellm.model_cost.get(f"{provider}/{model_id}")
        or litellm.model_cost.get(model_id)
        or {}
    )
    if not info:
        return None

    # LiteLLM field → EVA field mapping
    # supports_reasoning_content is present in newer LiteLLM versions
    supports_reasoning = info.get("supports_reasoning_content") or info.get("supports_reasoning")

    supports_reasoning_bool = bool(supports_reasoning) if supports_reasoning is not None else None
    reasoning_levels, reasoning_can_disable = _reasoning_level_fields(provider, model_id, supports_reasoning_bool)

    return ModelCapabilitiesOut(
        provider=provider,
        model_id=model_id,
        max_context_tokens=info.get("max_input_tokens"),
        max_output_tokens=info.get("max_output_tokens"),
        supports_streaming=info.get("supports_streaming", True),
        supports_tool_calls=info.get("supports_function_calling"),
        supports_reasoning=supports_reasoning_bool,
        supports_vision=info.get("supports_vision"),
        supports_json_schema=info.get("supports_response_schema"),
        supports_file_upload=None,          # LiteLLM has no direct field for this
        supports_usage_reporting=None,      # LiteLLM has no direct field for this
        cost_per_input_token=info.get("input_cost_per_token"),
        cost_per_output_token=info.get("output_cost_per_token"),
        reasoning_levels=reasoning_levels,
        reasoning_can_disable=reasoning_can_disable,
        source="litellm",
    )


# ---------------------------------------------------------------------------
# Endpoints — FIXED PATHS FIRST (must come before parameterised routes)
# ---------------------------------------------------------------------------

@router.get(
    "/list",
    response_model=List[ProviderListItemOut],
    summary="Curated list of supported providers for the frontend provider selector",
)
async def list_supported_providers(request: Request, auth=Depends(get_current_auth)):
    """
    Returns the curated list of providers that EVA supports through its
    unified frontend flow (derived from providers/registry.py). Intended for
    populating provider dropdown menus.

    Each entry includes:
    - **provider** — canonical name; pass directly to `POST /set-session` and
      `GET /providers/{provider}/models`
    - **display_name** — human-friendly label for the UI
    - **is_native** — True if EVA has a dedicated native handler (groq/openai/anthropic/ollama)
    - **is_local** — True for local inference providers (Ollama); no API cost
    - **is_configured** — True if the provider's API key (or Ollama host) is present on
      this server. Unconfigured providers can still be shown in the UI; selecting them
      will return a `424` from `GET /providers/{provider}/models` prompting key setup.
    - **chat_ready** — configured AND able to chat. Selectors that pick a provider
      for a session, agent, daemon or fallback entry should show only these.

    Provider order: native providers first, then LiteLLM-gateway providers alphabetically.
    Supports ETag / ``If-None-Match`` conditional requests (B-020).
    """
    result = []
    for entry in _CURATED_PROVIDERS:
        p = entry["provider"]
        configured = is_provider_key_configured(p)
        result.append(ProviderListItemOut(
            provider=p,
            display_name=entry["display_name"],
            is_native=entry["is_native"],
            is_local=entry["is_local"],
            is_configured=configured,
            chat_ready=configured and _registry.is_chat_supported(p),
        ))
    return etag_response(request, jsonable_encoder(result))


@router.get(
    "/capabilities",
    response_model=List[ProviderOut],
    summary="Full static capability registry grouped by provider",
)
async def list_provider_capabilities(auth=Depends(get_current_auth)):
    """
    Returns every provider and model in the **static capability registry**,
    grouped by provider. This is the renamed form of the original `GET /providers`
    endpoint (still available at that path for backward compatibility).

    Each provider entry includes:
    - **is_native** – EVA has a dedicated native handler (anthropic/openai/groq/ollama)
    - **is_local** – local inference, no cost (ollama)
    - **Feature flags** – streaming, tool calls, reasoning, vision, etc.
    - **Token limits** – context window and max output tokens
    - **Pricing** – USD per input/output token (`null` if unknown)

    Note: Ollama is not included here because it has no fixed model catalogue.
    Use `GET /providers/ollama/models` for live Ollama model listing.

    A refresh from LiteLLM is performed on every request so pricing and capability
    data always reflects the latest published values.
    """
    await capability_registry.refresh_from_litellm(force=True)
    all_caps = capability_registry.get_all()
    return _group_by_provider(all_caps)


@router.get(
    "/costs",
    response_model=ProviderCostsOut,
    summary="Aggregate token usage and estimated spend per provider/model",
)
async def get_provider_costs(
    provider: Optional[str] = Query(None, description="Filter by provider name"),
    from_date: Optional[str] = Query(None, description="ISO-8601 start date (inclusive)"),
    to_date: Optional[str] = Query(None, description="ISO-8601 end date (inclusive)"),
    auth=Depends(get_current_auth),
):
    """
    Returns aggregated token usage and estimated USD cost per (provider, model)
    pair, derived from execution metrics stored in the DB.

    - **total_cost_usd** — sum of `computed_cost_usd` from execution_metrics.
      `null` if no pricing data was available at the time of execution.
    - **grand_total_cost_usd** — sum across all rows.  `null` if any row has
      no cost data (to avoid misleading partial sums).

    Filtering:
    - `provider` — restrict to a single provider
    - `from_date` / `to_date` — ISO-8601 strings (e.g. `2025-01-01`)
    """
    user_id = auth["user_id"]

    # Parse optional date filters
    _from: Optional[datetime] = None
    _to: Optional[datetime] = None
    try:
        if from_date:
            _from = datetime.fromisoformat(from_date)
        if to_date:
            _to = datetime.fromisoformat(to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}")

    async with AsyncSessionLocal() as db:
        rows = await ExecutionMetricsRepo.get_token_usage_summary(
            db,
            user_id=user_id,
            provider=provider,
            from_date=_from,
            to_date=_to,
        )

    cost_rows = [
        ProviderCostRow(
            provider=r["provider_used"] or "unknown",
            model=r["model_used"] or "unknown",
            total_executions=r["total_executions"],
            total_prompt_tokens=r["total_prompt_tokens"],
            total_completion_tokens=r["total_completion_tokens"],
            total_tokens=r["total_tokens"],
            total_cost_usd=r["total_cost_usd"],
        )
        for r in rows
    ]

    grand_tokens = sum(r.total_tokens for r in cost_rows)
    # grand_total_cost_usd is None if ANY row has no cost data (partial sums mislead)
    all_costs = [r.total_cost_usd for r in cost_rows]
    grand_cost: Optional[float] = (
        sum(c for c in all_costs if c is not None)
        if all_costs and all(c is not None for c in all_costs)
        else (sum(c for c in all_costs if c is not None) if any(c is not None for c in all_costs) else None)
    )

    return ProviderCostsOut(
        user_id=user_id,
        period_start=from_date,
        period_end=to_date,
        rows=cost_rows,
        grand_total_tokens=grand_tokens,
        grand_total_cost_usd=grand_cost,
    )


@router.post(
    "/models/refresh",
    response_model=CacheRefreshOut,
    summary="Invalidate the resolver model-listing cache for a provider",
)
async def refresh_model_cache(
    provider: Optional[str] = Query(None, description="Provider name to flush (omit to flush all)"),
    auth=Depends(get_current_auth),
):
    """
    Invalidate the TTL model-listing cache in the ProviderResolver.

    - **provider** (optional) — flush only that provider's cache.
      Omit to flush all providers at once.

    After calling this endpoint, the next call to `GET /providers/{provider}/models`
    will perform a fresh live fetch from the provider's API.
    """
    resolver.invalidate_cache(provider)
    if provider:
        msg = f"Model listing cache for provider '{provider}' has been invalidated."
    else:
        msg = "Model listing cache for all providers has been invalidated."
    return CacheRefreshOut(provider=provider, message=msg)


# ---------------------------------------------------------------------------
# Endpoints — PARAMETERISED PATHS (declared after all fixed paths above)
# ---------------------------------------------------------------------------

@router.get(
    "/{provider}/models",
    response_model=LiveProviderModelsOut,
    summary="Live model listing for a single provider",
)
async def list_provider_models(
    provider: str,
    live: bool = Query(False, description="Bypass cache and always fetch from provider API"),
    agent_usable_only: bool = Query(
        False,
        description="Hide models EVA's agent loop cannot drive (non-chat models, "
                    "or models known to lack tool calling). Selectors should pass true.",
    ),
    auth=Depends(get_current_auth),
):
    """
    Returns the live list of available models for the given provider.

    - Uses a **TTL cache** (default 5 minutes) so rapid calls don't hammer the provider API.
    - Pass `live=true` to bypass the cache and always fetch fresh.
    - For **Ollama**, no API key is needed — the host is read from `OLLAMA_HOST`.
    - For providers covered by the **LiteLLM SDK** (azure, bedrock, mistral, etc.),
      models are read from the local `litellm.model_cost` dictionary — no network call.
    - Returns **404** if the provider is unknown / unsupported.
    - Returns **424** if the provider's API key is not configured.

    Cost data (input/output token prices) is merged from `litellm.model_cost`
    where available.
    """
    resolution = resolver.resolve(provider, "")
    if not resolution.supported:
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{provider}' is not supported by EVA.",
        )

    # ── V4.4 Pre-flight API key check ────────────────────────────────────────
    # For native cloud providers (anthropic, openai, groq) we need the API key
    # before hitting their live endpoints.  Ollama is local — no key needed.
    # LiteLLM SDK providers (bedrock, gemini, cohere, …) use local model_cost
    # data for listing, so no key is required at this layer.
    session_api_key = auth.get("api_key")
    if resolution.is_native and not resolution.is_local:
        if not is_provider_key_configured(resolution.provider, explicit_key=session_api_key):
            raise HTTPException(
                status_code=424,
                detail={
                    "code": "EVA_KEY_NOT_CONFIGURED",
                    "provider": resolution.provider,
                    "message": (
                        f"API key for '{resolution.provider}' is not configured. "
                        f"Use POST /secrets/{resolution.provider} to set your API key."
                    ),
                },
            )

    live_models = await resolver.list_models(provider, api_key=session_api_key, live=live)

    # Merge in cost data from litellm where available (best-effort)
    model_out_list: List[LiveModelOut] = []
    for m in live_models:
        cost_info = resolver.get_model_cost(provider, m.model_id)
        tools_flag, usable = _model_usability(provider, m.model_id)
        if agent_usable_only and not usable:
            continue
        model_out_list.append(LiveModelOut(
            model_id=m.model_id,
            display_name=m.display_name,
            context_window=m.context_window,
            owned_by=m.owned_by,
            active=m.active,
            cost_per_input_token=cost_info.get("input_cost_per_token"),
            cost_per_output_token=cost_info.get("output_cost_per_token"),
            supports_tool_calls=tools_flag,
            agent_usable=usable,
        ))

    model_out_list.sort(key=lambda m: m.model_id)

    return LiveProviderModelsOut(
        provider=resolution.provider,
        is_native=resolution.is_native,
        is_local=resolution.is_local,
        chat_ready=resolution.chat_ready,
        model_count=len(model_out_list),
        models=model_out_list,
        cached=not live,
    )


@router.get(
    "/{provider}/models/{model_id:path}/capabilities",
    response_model=ModelCapabilitiesOut,
    summary="Capability detail for a specific provider/model",
)
async def get_model_capabilities(
    provider: str,
    model_id: str,
    auth=Depends(get_current_auth),
):
    """
    Returns the full capability profile for a specific **provider + model** pair.

    `model_id` is the bare model name exactly as returned by
    `GET /providers/{provider}/models` (e.g. `llama-3.3-70b-versatile`,
    `gpt-4o`, `claude-3-5-sonnet-20241022`).

    Resolution order:
    0. **Local Ollama optimistic default** (V6.7 Fix 3) — no fixed model
       catalogue, so neither the static registry nor LiteLLM's dataset can
       ever have per-model data for it (deliberate -- see A-17, 2026-09-22:
       a locally-run model's real usable context depends on the user's own
       num_ctx configuration, which no static, provider-hosted data source
       can know, so EVA never attempts to populate real numbers for it, on
       purpose). Rather than reporting reasoning support as unknown -- which
       would suppress the reasoning control client-side purely for lack of
       data, not because the model doesn't have one -- this provider reports
       `supports_reasoning: true` and the full level vocabulary
       unconditionally, matching the existing "offer it, let the real API
       call be the source of truth" design already used for OpenAI/Groq's
       *level* vocabulary (deliberately extended here to the *binary* flag
       too, since even that isn't knowable per-model for a self-hosted,
       user-pulled-any-model provider). Every other capability field stays
       `null`/`source: "unknown"`, same as resolution 4 below -- this only
       changes the reasoning-specific fields.
    1. **Static capability registry** — highest quality; covers native providers
       (groq, openai, anthropic) with manually-curated feature flags. Also
       covers Ollama Cloud as of A-17 (2026-09-22): unlike local Ollama, it's
       a fixed, provider-controlled catalogue, so LiteLLM's real per-model
       data (context window, etc.) is merged in here the same way it is for
       every other provider -- see capability_registry.py's
       `_resolve_ollama_cloud_model_name()`.
    2. **Ollama Cloud optimistic default** — same reasoning as resolution 0,
       for an Ollama Cloud model that resolution 1's registry doesn't (yet,
       or ever) have real data for. Only reached when the registry lookup in
       step 1 found nothing for this specific model.
    3. **LiteLLM model_cost** — covers 2,000+ models including all LiteLLM-gateway
       providers (azure, bedrock, mistral, perplexity, etc.) with pricing and
       basic capability flags.
    4. **Partial response** — provider is known but model has no data; returns the
       provider/model pair with all capability fields as `null` and `source: "unknown"`.

    Note: `{model_id:path}` allows the path segment to contain `/` characters,
    which can occur for some LiteLLM-prefixed model identifiers.
    The bare model name (without the provider prefix) is always returned in the
    response `model_id` field.
    """
    p = provider.lower().strip()

    # Strip provider prefix if the caller passed the full LiteLLM-prefixed form
    # e.g. model_id="azure/gpt-4o" with provider="azure" → bare="gpt-4o"
    prefix = f"{p}/"
    bare_model = model_id[len(prefix):] if model_id.startswith(prefix) else model_id

    def _ollama_optimistic_default() -> "ModelCapabilitiesOut":
        reasoning_levels, reasoning_can_disable = _reasoning_level_fields(p, bare_model, True)
        return ModelCapabilitiesOut(
            provider=p,
            model_id=bare_model,
            max_context_tokens=None,
            max_output_tokens=None,
            supports_streaming=None,
            supports_tool_calls=None,
            supports_reasoning=True,
            supports_vision=None,
            supports_json_schema=None,
            supports_file_upload=None,
            supports_usage_reporting=None,
            cost_per_input_token=None,
            cost_per_output_token=None,
            reasoning_levels=reasoning_levels,
            reasoning_can_disable=reasoning_can_disable,
            source="unknown",
        )

    # ── 0. Local Ollama — optimistic reasoning default, unconditional ─────
    # (the registry never has data for it -- see the docstring above -- so
    # there's nothing to check first, unlike Ollama Cloud below.)
    if p == "ollama":
        return _ollama_optimistic_default()

    # ── 1. Static capability registry (best quality) ──────────────────────
    cap = capability_registry.get_model(p, bare_model)
    if cap:
        reasoning_levels, reasoning_can_disable = _reasoning_level_fields(p, bare_model, cap.supports_reasoning)
        return ModelCapabilitiesOut(
            provider=p,
            model_id=bare_model,
            max_context_tokens=cap.max_context_tokens,
            max_output_tokens=cap.max_output_tokens,
            supports_streaming=cap.supports_streaming,
            supports_tool_calls=cap.supports_tool_calls,
            supports_reasoning=cap.supports_reasoning,
            supports_vision=cap.supports_vision,
            supports_json_schema=cap.supports_json_schema,
            supports_file_upload=cap.supports_file_upload,
            supports_usage_reporting=cap.supports_usage_reporting,
            cost_per_input_token=cap.cost_per_input_token,
            cost_per_output_token=cap.cost_per_output_token,
            tool_call_reliability=cap.tool_call_reliability,
            reasoning_levels=reasoning_levels,
            reasoning_can_disable=reasoning_can_disable,
            source="registry",
        )

    # ── 2. Ollama Cloud — optimistic reasoning default ─────────────────────
    # Only reached when step 1's registry lookup found nothing for this
    # specific model (e.g. one LiteLLM doesn't track yet). Skips step 3's
    # generic LiteLLM lookup deliberately: that path keys off "{provider}/
    # {model}" ("ollama_cloud/gpt-oss:120b"), which never matches LiteLLM's
    # own "-cloud"-suffixed key shape, so it would never find anything here
    # anyway -- going straight to the same optimistic default local Ollama
    # uses keeps this provider's data-quality guarantee (reasoning support
    # is never falsely reported as unknown) instead of falling through to
    # resolution 4's plain "supports_reasoning: null".
    if p == "ollama_cloud":
        return _ollama_optimistic_default()

    # ── 3. LiteLLM model_cost fallback ───────────────────────────────────
    litellm_caps = _capabilities_from_litellm(p, bare_model)
    if litellm_caps:
        return litellm_caps

    # ── 4. Provider known but model not in any dataset ────────────────────
    resolution = resolver.resolve(p, bare_model)
    if not resolution.supported:
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{provider}' is not supported by EVA.",
        )

    # Return a partial response so the frontend doesn't hard-fail on unknown models
    return ModelCapabilitiesOut(
        provider=p,
        model_id=bare_model,
        max_context_tokens=None,
        max_output_tokens=None,
        supports_streaming=None,
        supports_tool_calls=None,
        supports_reasoning=None,
        supports_vision=None,
        supports_json_schema=None,
        supports_file_upload=None,
        supports_usage_reporting=None,
        cost_per_input_token=None,
        cost_per_output_token=None,
        source="unknown",
    )
