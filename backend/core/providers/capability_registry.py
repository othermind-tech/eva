# core/providers/capability_registry.py
#
# Unified Provider Capability Registry for EVA.
#
# Storage layout
# --------------
# Each provider has a single JSON file:
#
#   providers/capability/<provider>/models_cache.json
#
# The file contains three kinds of keys:
#
#   "__meta__"     – bookkeeping (last_updated, source, model_count)
#   "__provider__" – provider-level feature flag defaults (inherited by every
#                    model in the file unless the model overrides them)
#   "<model-name>" – per-model capability record (all fields stored explicitly
#                    after the first successful LiteLLM refresh)
#
# Why JSON and not .py?
# ---------------------
# uvicorn --reload watches *.py files only.  Writing to .json never triggers
# a server restart, so refresh_from_litellm() can run at any time without
# causing a reload loop.
#
# Startup read order
# ------------------
#   _build_from_local() reads models_cache.json for each provider.
#   If a file is absent or unreadable it logs a warning and skips that
#   provider — the registry stays partially populated rather than crashing.
#
# Refresh flow
# ------------
#   refresh_from_litellm(force=True)          (startup lifespan + API endpoints)
#     1. Fetch LiteLLM model_prices_and_context_window.json
#     2. _merge_litellm() — overlay pricing + limits into _registry
#     3. _write_json_caches() — persist updated _registry back to
#        providers/capability/<provider>/models_cache.json
#     4. Next restart reads the enriched JSON — no network call needed
#
# Internal import
# ---------------
#   from core.providers.capability_registry import get_by_provider, get_model

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests as _requests  # already in requirements.txt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from config.settings import PROVIDER_CAPABILITY_REFRESH_TTL_SECONDS

LITELLM_PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

REFRESH_TTL: int = PROVIDER_CAPABILITY_REFRESH_TTL_SECONDS

# Absolute path to providers/capability/
_CAPABILITY_BASE_PATH: Path = (
    Path(__file__).parent.parent.parent  # project root
    / "providers"
    / "capability"
)

_CACHE_FILENAME = "models_cache.json"   # written per provider; never a .py file

# Maps our provider name → subfolder name under providers/capability/
_PROVIDER_FOLDER_MAP: Dict[str, str] = {
    "openai":    "openai",
    "groq":      "groq",
    "anthropic": "anthropic",
    "litellm":   "litellm",
    # "ollama" intentionally omitted (V4.3): Ollama has no fixed model set.
    # Models are listed live via resolver.list_models("ollama") on demand.
}

# Maps LiteLLM's "litellm_provider" field → our provider name
_LITELLM_PROVIDER_MAP: Dict[str, str] = {
    "openai":    "openai",
    "groq":      "groq",
    "anthropic": "anthropic",
    "litellm":   "litellm",
    # "ollama" is deliberately NOT mapped here -- see
    # _resolve_ollama_cloud_model_name() below. LiteLLM tags both local and
    # cloud Ollama under this same single tag, and only Ollama Cloud should
    # ever be merged (A-17, 2026-09-22).
}


def _resolve_ollama_cloud_model_name(model_name: str) -> Optional[str]:
    """
    A-17: fold Ollama Cloud's real max_context_tokens into the registry via
    the same LiteLLM merge every other provider already uses, WITHOUT also
    pulling in local Ollama.

    LiteLLM tags both under one litellm_provider == "ollama", distinguished
    only by a "-cloud" suffix on the model name -- e.g.
    "ollama/gpt-oss:120b-cloud" (cloud) vs "ollama/llama3.1" (local, no
    suffix). Confirmed live against ollama.com/v1/models: Ollama Cloud's own
    API returns the model as "gpt-oss:120b" (no suffix) -- so the suffix is
    LiteLLM's own disambiguation marker, not part of the real model name, and
    must be stripped for the registry key to ever match what
    get_model("ollama_cloud", ...) is actually called with.

    Local Ollama is deliberately left out: its real usable context depends
    on the user's own num_ctx configuration, which this static,
    provider-hosted data has no way to know -- surfacing a number here for
    local models would risk a confidently WRONG value, exactly the failure
    mode this fix exists to avoid (see agent_engine.py's
    _remaining_context_budget() docstring). Ollama Cloud has no such
    wrinkle: it's a fixed, provider-controlled catalog, the same category as
    openai/groq/anthropic.

    Returns the bare model name (e.g. "gpt-oss:120b") for a cloud entry, or
    None for a local entry or anything not shaped like an Ollama model name
    (caller skips the row in that case -- same as an unmapped provider).
    """
    prefix, suffix = "ollama/", "-cloud"
    if not model_name.startswith(prefix) or not model_name.endswith(suffix):
        return None
    bare = model_name[len(prefix):-len(suffix)]
    return bare or None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ModelCapability:
    """
    Full capability record for a single (provider, model) pair.

    Importable by other modules (e.g. the model fallback chain):
        from core.providers.capability_registry import ModelCapability, get_model
    """

    provider: str
    model: str

    # Token limits
    max_context_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None

    # Feature flags
    supports_streaming: bool = False
    supports_tool_calls: bool = False
    supports_reasoning: bool = False
    supports_vision: bool = False
    supports_json_schema: bool = False
    supports_file_upload: bool = False
    supports_usage_reporting: bool = False

    # Pricing (USD per token; None = unknown)
    cost_per_input_token: Optional[float] = None
    cost_per_output_token: Optional[float] = None

    # Provider-specific extras
    token_speed_tps: Optional[int] = None  # Groq: tokens-per-second throughput

    # Tool-call reliability: "reliable" | "unreliable" | None (unknown).
    # Models flagged "unreliable" intermittently generate malformed tool calls
    # that the provider's server-side validation rejects (e.g. wrong arg types,
    # hallucinated function names).  The frontend should warn users before
    # enabling tools with these models.
    tool_call_reliability: Optional[str] = None

    # Provenance: "local" | "litellm" | "merged" | "seed"
    source: str = "local"


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# Primary lookup: "{provider}:{model}" → ModelCapability
_registry: Dict[str, ModelCapability] = {}

# Provider-level defaults from the "__provider__" section of each JSON file.
# Used as the baseline when LiteLLM introduces a model that isn't in the cache.
_provider_caps: Dict[str, Dict[str, Any]] = {}

# Epoch timestamp of the last successful LiteLLM fetch (None = never)
_last_refresh: Optional[float] = None


def _key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


def _register(cap: ModelCapability) -> None:
    _registry[_key(cap.provider, cap.model)] = cap


# ---------------------------------------------------------------------------
# Helper — build a ModelCapability from a flat dict
# ---------------------------------------------------------------------------

def _make_capability(provider: str, model: str, d: Dict[str, Any]) -> ModelCapability:
    """
    Construct a ModelCapability from a merged flat dict (JSON fields).
    All field names in models_cache.json use the canonical names.
    """
    return ModelCapability(
        provider=provider,
        model=model,
        max_context_tokens=d.get("max_context_tokens"),
        max_output_tokens=d.get("max_output_tokens"),
        supports_streaming=d.get("supports_streaming", False),
        supports_tool_calls=d.get("supports_tool_calls", False),
        supports_reasoning=d.get("supports_reasoning", False),
        supports_vision=d.get("supports_vision", False),
        supports_json_schema=d.get("supports_json_schema", False),
        supports_file_upload=d.get("supports_file_upload", False),
        supports_usage_reporting=d.get("supports_usage_reporting", False),
        cost_per_input_token=d.get("cost_per_input_token"),
        cost_per_output_token=d.get("cost_per_output_token"),
        token_speed_tps=d.get("token_speed_tps"),
        tool_call_reliability=d.get("tool_call_reliability"),
        source=d.get("source", "local"),
    )


def _cache_path(provider: str) -> Path:
    folder = _PROVIDER_FOLDER_MAP.get(provider, provider)
    return _CAPABILITY_BASE_PATH / folder / _CACHE_FILENAME


# ---------------------------------------------------------------------------
# Bootstrap — read JSON caches at import time
# ---------------------------------------------------------------------------

def _build_from_local() -> None:
    """
    Populate _registry and _provider_caps from the JSON cache files.

    JSON structure per file:
        {
            "__meta__":     { "provider": "...", "last_updated": "...", ... },
            "__provider__": { "supports_streaming": true, ... },
            "<model-name>": { "max_context_tokens": 200000, ... },
            ...
        }

    Provider-level defaults (__provider__) are merged into each model entry
    so that models only need to store fields that differ.  Fields explicitly
    set in the model entry always win over provider defaults.
    """
    _registry.clear()
    _provider_caps.clear()

    for provider, folder in _PROVIDER_FOLDER_MAP.items():
        path = _cache_path(provider)
        if not path.exists():
            logger.warning(
                "No capability cache found for provider '%s' at %s — skipped",
                provider, path,
            )
            continue

        try:
            data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "Could not read capability cache for '%s': %s — skipped",
                provider, exc,
            )
            continue

        # Store provider-level defaults for use during LiteLLM refresh
        provider_defaults = data.get("__provider__", {})
        _provider_caps[provider] = provider_defaults

        count = 0
        for key, fields in data.items():
            if key.startswith("__") or not isinstance(fields, dict):
                continue  # skip __meta__, __provider__, and any non-dict values

            # Provider defaults are the base; model-specific fields override
            merged = {**provider_defaults, **fields}
            _register(_make_capability(provider, key, merged))
            count += 1

        logger.debug("Loaded %d %s models from JSON cache", count, provider)

    logger.debug(
        "Capability registry built from local JSON — %d models total",
        len(_registry),
    )


# ---------------------------------------------------------------------------
# Persist — write JSON caches after a refresh
# ---------------------------------------------------------------------------

def _write_json_caches() -> None:
    """
    Serialise the current _registry back to per-provider models_cache.json files.

    Each file written is a self-contained JSON document with:
      - "__meta__"     bookkeeping block
      - "__provider__" provider-level capability defaults (preserved from load)
      - one key per model with all capability fields stored explicitly

    Writing JSON (not .py) ensures uvicorn --reload never detects a change
    and never triggers an unwanted server restart.
    """
    by_provider: Dict[str, List[ModelCapability]] = {}
    for cap in _registry.values():
        by_provider.setdefault(cap.provider, []).append(cap)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for provider, caps in by_provider.items():
        if provider not in _PROVIDER_FOLDER_MAP:
            logger.debug("No cache folder defined for provider '%s', skipping", provider)
            continue

        path = _cache_path(provider)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing file to preserve manually-curated fields (e.g.
        # tool_call_reliability) that LiteLLM knows nothing about.  If the
        # in-memory value is None but the file already has a non-null value,
        # the file's value takes precedence so hand-edited flags survive
        # a live LiteLLM refresh without a server restart.
        existing_file_data: Dict[str, Any] = {}
        if path.exists():
            try:
                existing_file_data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

        payload: Dict[str, Any] = {
            "__meta__": {
                "provider":     provider,
                "last_updated": now_iso,
                "model_count":  len(caps),
                "source":       "litellm-refresh",
            },
            "__provider__": _provider_caps.get(provider, {}),
        }

        for cap in sorted(caps, key=lambda c: c.model):
            # Preserve manually-set tool_call_reliability from the existing
            # file when the in-memory registry has no value.
            file_tcr = existing_file_data.get(cap.model, {}).get("tool_call_reliability")
            tcr = cap.tool_call_reliability if cap.tool_call_reliability is not None else file_tcr

            payload[cap.model] = {
                "max_context_tokens":       cap.max_context_tokens,
                "max_output_tokens":        cap.max_output_tokens,
                "supports_streaming":       cap.supports_streaming,
                "supports_tool_calls":      cap.supports_tool_calls,
                "supports_reasoning":       cap.supports_reasoning,
                "supports_vision":          cap.supports_vision,
                "supports_json_schema":     cap.supports_json_schema,
                "supports_file_upload":     cap.supports_file_upload,
                "supports_usage_reporting": cap.supports_usage_reporting,
                "cost_per_input_token":     cap.cost_per_input_token,
                "cost_per_output_token":    cap.cost_per_output_token,
                "token_speed_tps":          cap.token_speed_tps,
                "tool_call_reliability":    tcr,
                "source":                   cap.source,
            }

        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "Cached %d %s models → %s",
            len(caps),
            provider,
            path.relative_to(_CAPABILITY_BASE_PATH.parent.parent),
        )


# ---------------------------------------------------------------------------
# LiteLLM HTTP fetch  (sync — always called via run_in_executor)
# ---------------------------------------------------------------------------

def _fetch_litellm_sync() -> Optional[bytes]:
    """
    Synchronous HTTP GET for the LiteLLM pricing JSON.
    Returns raw bytes on success, None on any error.
    """
    try:
        resp = _requests.get(LITELLM_PRICES_URL, timeout=10)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("LiteLLM price fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Merge LiteLLM data into _registry
# ---------------------------------------------------------------------------

def _merge_litellm(data: Dict[str, Any]) -> None:
    """
    Overlay pricing + token data from the LiteLLM JSON into _registry.

    Each model in the JSON that passes the provider + mode filters is written
    into TWO places:

    1. Native provider cache  (e.g. "openai", "groq", "anthropic")
       — for callers that route directly to a specific provider.

    2. LiteLLM cache  (provider = "litellm")
       — for callers that route through LiteLLM as a multi-provider gateway.
       The model name stored here is the LiteLLM routing key (the JSON key
       itself, e.g. "gpt-4o", "anthropic/claude-3-5-sonnet-20241022",
       "groq/llama-3.3-70b-versatile") — exactly what you pass to
       litellm.completion().

    This means the LiteLLM cache becomes a full superset of all accessible
    models, not just the handful with litellm_provider=="litellm".

    Merge rules (same for both caches)
    ------------------------------------
    - Entry already exists → overlay costs; refine limits only when None;
      promote vision/tool flags when LiteLLM explicitly sets them True.
      source → "merged".
    - New entry → build from provider-level defaults + LiteLLM data.
      source → "litellm".
    - Unknown provider or non-chat/completion mode → skip entirely.
    """
    # Counters for native-provider registrations
    added_native = updated_native = 0
    # Counters for LiteLLM-gateway registrations
    added_ll = updated_ll = 0
    skipped = 0

    litellm_defaults = _provider_caps.get("litellm", {})

    for model_name, info in data.items():
        if not isinstance(info, dict):
            continue

        # A-17: Ollama Cloud is resolved separately from the generic map --
        # see _resolve_ollama_cloud_model_name()'s docstring for why.
        native_model_name_override: Optional[str] = None
        litellm_provider_tag = info.get("litellm_provider", "")
        if litellm_provider_tag == "ollama":
            native_model_name_override = _resolve_ollama_cloud_model_name(model_name)
            if native_model_name_override is None:
                skipped += 1
                continue
            our_provider = "ollama_cloud"
        else:
            our_provider = _LITELLM_PROVIDER_MAP.get(litellm_provider_tag)
            if not our_provider:
                skipped += 1
                continue

        if info.get("mode", "chat") not in ("chat", "completion"):
            skipped += 1
            continue

        cost_in  = info.get("input_cost_per_token")
        cost_out = info.get("output_cost_per_token")
        ctx_in   = info.get("max_input_tokens")
        ctx_out  = info.get("max_output_tokens")
        has_vision    = bool(info.get("supports_vision", False))
        has_tools     = bool(info.get("supports_function_calling", False))
        # V6.7 bug fix: this was never read from litellm's data at all --
        # every model fell back to prov_defaults/litellm_defaults, which
        # are always {} (empty __provider__ blocks in every cache file), so
        # supports_reasoning silently resolved False for every registered
        # model, including o1/o3/gpt-5/claude-opus/gpt-oss. litellm's JSON
        # genuinely has this field correctly set per-model (verified
        # empirically); it was
        # just never being extracted, unlike supports_vision/has_tools
        # right above, which always were.
        has_reasoning = bool(info.get("supports_reasoning", False))
        # Ollama Cloud: LiteLLM usually says NOTHING about reasoning for the
        # "-cloud" entries (the key is absent, not False). Treating silence as
        # "no reasoning" made the registry report supports_reasoning=False for
        # models like gpt-oss, so POST /set-session silently switched reasoning
        # off -- a regression from the A-17 merge, contradicting this provider's
        # documented stance (api/providers.py "Ollama Cloud optimistic
        # default"): unknown means offer it and let the real API call decide.
        # An explicit value from LiteLLM, True or False, is still honoured.
        if litellm_provider_tag == "ollama" and info.get("supports_reasoning") is None:
            has_reasoning = True

        # ── 1. Native provider cache ──────────────────────────────────────────
        # Bug fix (found investigating why Groq models never resolved via
        # get_model()): litellm's JSON key is its own *routing* key, which for
        # some providers (confirmed: groq, 11/11 models; one stray on openai)
        # already includes that provider's name as a prefix (e.g.
        # "groq/openai/gpt-oss-20b"). The native cache must be keyed by the
        # bare model name a caller passes to that provider's own handler/SDK
        # (e.g. Groq's own client expects "openai/gpt-oss-20b", no "groq/"
        # prefix) -- storing the raw routing key here meant get_model(provider,
        # bare_model) could never match what was actually stored, silently
        # falling through to "capabilities unknown" for every affected model.
        # The LiteLLM gateway cache below is intentionally NOT affected -- that
        # one is supposed to keep the full routing key, that's its whole job.
        if native_model_name_override is not None:
            native_model_name = native_model_name_override
        else:
            native_prefix = f"{our_provider}/"
            native_model_name = (
                model_name[len(native_prefix):]
                if model_name.startswith(native_prefix)
                else model_name
            )

        existing_native = _registry.get(_key(our_provider, native_model_name))
        if existing_native:
            if cost_in  is not None: existing_native.cost_per_input_token  = cost_in
            if cost_out is not None: existing_native.cost_per_output_token = cost_out
            if ctx_in  and existing_native.max_context_tokens is None: existing_native.max_context_tokens = ctx_in
            if ctx_out and existing_native.max_output_tokens  is None: existing_native.max_output_tokens  = ctx_out
            if has_vision:    existing_native.supports_vision    = True
            if has_tools:     existing_native.supports_tool_calls = True
            if has_reasoning: existing_native.supports_reasoning = True
            existing_native.source = "merged"
            updated_native += 1
        else:
            prov_defaults = _provider_caps.get(our_provider, {})
            _register(ModelCapability(
                provider=our_provider,
                model=native_model_name,
                max_context_tokens=ctx_in,
                max_output_tokens=ctx_out,
                supports_streaming=prov_defaults.get("supports_streaming", True),
                supports_tool_calls=has_tools or prov_defaults.get("supports_tool_calls", False),
                supports_reasoning=has_reasoning or prov_defaults.get("supports_reasoning", False),
                supports_vision=has_vision,
                supports_json_schema=prov_defaults.get("supports_json_schema", False),
                supports_file_upload=prov_defaults.get("supports_file_upload", False),
                supports_usage_reporting=prov_defaults.get("supports_usage_reporting", True),
                cost_per_input_token=cost_in,
                cost_per_output_token=cost_out,
                source="litellm",
            ))
            added_native += 1

        # ── 2. LiteLLM gateway cache ──────────────────────────────────────────
        # The model_name here is the LiteLLM routing key — exactly what a caller
        # passes to litellm.completion(model=...) to reach this model.
        existing_ll = _registry.get(_key("litellm", model_name))
        if existing_ll:
            if cost_in  is not None: existing_ll.cost_per_input_token  = cost_in
            if cost_out is not None: existing_ll.cost_per_output_token = cost_out
            if ctx_in  and existing_ll.max_context_tokens is None: existing_ll.max_context_tokens = ctx_in
            if ctx_out and existing_ll.max_output_tokens  is None: existing_ll.max_output_tokens  = ctx_out
            if has_vision:    existing_ll.supports_vision    = True
            if has_tools:     existing_ll.supports_tool_calls = True
            if has_reasoning: existing_ll.supports_reasoning = True
            existing_ll.source = "merged"
            updated_ll += 1
        else:
            _register(ModelCapability(
                provider="litellm",
                model=model_name,
                max_context_tokens=ctx_in,
                max_output_tokens=ctx_out,
                supports_streaming=litellm_defaults.get("supports_streaming", True),
                supports_tool_calls=has_tools or litellm_defaults.get("supports_tool_calls", False),
                supports_reasoning=has_reasoning or litellm_defaults.get("supports_reasoning", False),
                supports_vision=has_vision,
                supports_json_schema=litellm_defaults.get("supports_json_schema", False),
                supports_file_upload=litellm_defaults.get("supports_file_upload", False),
                supports_usage_reporting=litellm_defaults.get("supports_usage_reporting", True),
                cost_per_input_token=cost_in,
                cost_per_output_token=cost_out,
                source="litellm",
            ))
            added_ll += 1

    logger.info(
        "LiteLLM merge — "
        "native(added=%d updated=%d)  gateway(added=%d updated=%d)  "
        "skipped=%d  total_registry=%d",
        added_native, updated_native,
        added_ll, updated_ll,
        skipped, len(_registry),
    )


# ---------------------------------------------------------------------------
# Public async refresh entry point
# ---------------------------------------------------------------------------

async def refresh_from_litellm(force: bool = False) -> None:
    """
    Fetch the LiteLLM pricing JSON, merge it into _registry, then persist
    the result to the per-provider models_cache.json files.

    Parameters
    ----------
    force : bool
        When True, skip the TTL check and always perform a live fetch.
        Should be True at startup (lifespan hook) and from API endpoints.

    Failure behaviour
    -----------------
    Network or JSON-parse failures are logged as warnings.  The in-memory
    registry and on-disk JSON files are left untouched, so the app continues
    to serve stale-but-valid data.
    """
    global _last_refresh

    if not force and _last_refresh is not None:
        if time.time() - _last_refresh < REFRESH_TTL:
            logger.debug("Capability registry is fresh — skipping refresh")
            return

    logger.info("Refreshing provider capabilities from LiteLLM...")

    raw = await asyncio.to_thread(_fetch_litellm_sync)

    if raw is None:
        logger.warning(
            "LiteLLM refresh skipped — fetch returned no data. "
            "JSON caches and in-memory registry unchanged."
        )
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("LiteLLM JSON parse error: %s", exc)
        return

    _merge_litellm(data)

    try:
        _write_json_caches()
    except Exception as exc:
        logger.warning("Failed to write JSON caches (non-fatal): %s", exc)

    _last_refresh = time.time()
    logger.info(
        "Provider capability registry refreshed and persisted — %d models total",
        len(_registry),
    )


# ---------------------------------------------------------------------------
# Ollama (V4.3 — REMOVED from static registry)
# ---------------------------------------------------------------------------
# Ollama has no fixed set of models — users install models dynamically via
# `ollama pull`.  In V4.3 Ollama models are listed live through the resolver:
#
#   from core.providers.resolver import resolver
#   models = await resolver.list_models("ollama")
#
# The capability registry only tracks providers with a static / semi-static
# model catalogue (anthropic, openai, groq, litellm).
#
# For backward compatibility, retain a no-op stub so any callers that still
# reference refresh_from_ollama() (e.g. old tests) do not crash.

async def refresh_from_ollama(host: Optional[str] = None) -> None:  # noqa: ARG001
    """
    No-op stub retained for backward compatibility.

    Ollama model listing was moved to core.providers.resolver.list_models()
    in V4.3.  The static capability registry no longer manages Ollama models.
    """
    logger.debug(
        "refresh_from_ollama() called — this is a no-op in V4.3. "
        "Use resolver.list_models('ollama') for live Ollama model listing."
    )


# ---------------------------------------------------------------------------
# Public query API
# ---------------------------------------------------------------------------

def get_all() -> List[ModelCapability]:
    """Return all registered model capability records."""
    return list(_registry.values())


def get_providers() -> List[str]:
    """Return sorted list of all provider names present in the registry."""
    return sorted({cap.provider for cap in _registry.values()})


def get_by_provider(provider: str) -> List[ModelCapability]:
    """Return all models for a given provider name (case-sensitive)."""
    return [cap for cap in _registry.values() if cap.provider == provider]


def get_model(provider: str, model: str) -> Optional[ModelCapability]:
    """Return capability record for a specific (provider, model) pair, or None."""
    return _registry.get(_key(provider, model))


def is_stale() -> bool:
    """True if a refresh is overdue (TTL exceeded or never performed)."""
    return _last_refresh is None or (time.time() - _last_refresh >= REFRESH_TTL)


def last_refreshed_iso() -> Optional[str]:
    """ISO-8601 timestamp of the last successful refresh, or None."""
    if _last_refresh is None:
        return None
    return datetime.datetime.utcfromtimestamp(_last_refresh).isoformat() + "Z"


def capability_as_dict(cap: ModelCapability) -> Dict[str, Any]:
    """Serialise a ModelCapability to a plain dict."""
    return asdict(cap)


# ---------------------------------------------------------------------------
# Bootstrap — populate registry from disk at module import time
# ---------------------------------------------------------------------------

_build_from_local()
