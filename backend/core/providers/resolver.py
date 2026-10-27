# core/providers/resolver.py
#
# ProviderResolver — 4-step provider resolution chain for EVA V4.3.
#
# Resolution order (resolve() method):
#   1. provider="litellm"  → legacy alias; route to step 3, add deprecation warning
#   2. Native handler set  (anthropic, openai, groq, ollama) → use it directly
#   3. Registered gateway provider (providers/registry.py) → route via
#      litellm_handler with the model auto-prefixed; chat_ready tells callers
#      whether a chat path exists yet (Azure/Bedrock/... are known, not ready)
#   4. Unknown             → ProviderResolution(supported=False)
#
# Also provides:
#   list_models(provider, api_key, live)  — live model listing with TTL cache
#   compute_call_cost(...)                — USD cost via litellm.cost_per_token()
#   get_model_cost(provider, model)       — per-token pricing dict
#   invalidate_cache(provider)            — flush TTL cache
#
# The module-level `resolver` singleton is the primary entry point for all code.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from providers import registry as _registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL model-listing cache  (keyed by "provider:api_key")
# ---------------------------------------------------------------------------

_model_cache: Dict[str, tuple] = {}   # key → (timestamp_float, List[LiveModel])


def _cache_ttl() -> int:
    try:
        from config.settings import settings
        return settings.PROVIDER_MODEL_CACHE_TTL_SECONDS
    except Exception:
        return 300  # fallback if settings unavailable at import time


# ---------------------------------------------------------------------------
# Provider alias table  (user-facing name → LiteLLM model prefix)
# ---------------------------------------------------------------------------

#
# All three tables below are derived from providers/registry.py -- the one
# place EVA decides which providers exist. Only registered providers resolve;
# EVA no longer treats every provider LiteLLM happens to know as supported
# (each needs its own verified credential shape).
_PROVIDER_ALIASES: Dict[str, str] = _registry.alias_prefixes()

_NATIVE_PROVIDERS = _registry.native_names()
_LOCAL_PROVIDERS  = _registry.local_names()   # ollama_cloud is NOT local — subscription billing


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

@dataclass
class ProviderResolution:
    """Outcome of a resolve() call — tells callers how to handle a provider/model pair."""

    provider: str           # Canonical provider name (lowercased)
    model: str              # Original model string (before prefixing)
    is_native: bool         # True  → EVA has a dedicated native handler
    is_local: bool          # True  → local inference (e.g. Ollama); cost = 0
    via_litellm: bool       # True  → route chat through litellm_handler
    prefixed_model: str     # Model string to pass to the handler (may be auto-prefixed)
    supported: bool = True  # False → unknown provider; caller should reject

    # False -> the provider is known (its key can be stored/listed) but no
    # chat path exists yet; not_ready_reason says why. See providers/registry.py.
    chat_ready: bool = True
    not_ready_reason: Optional[str] = None

    # Legacy "litellm" alias metadata
    legacy_litellm: bool = False
    deprecation_warning: Optional[str] = None


# ---------------------------------------------------------------------------
# ProviderResolver
# ---------------------------------------------------------------------------

class ProviderResolver:
    """
    Singleton provider resolution engine.

    All external code should import and use the module-level `resolver` instance:
        from core.providers.resolver import resolver
    """

    NATIVE_PROVIDERS = _NATIVE_PROVIDERS
    LOCAL_PROVIDERS  = _LOCAL_PROVIDERS
    PROVIDER_ALIASES = _PROVIDER_ALIASES

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, provider: str, model: str) -> ProviderResolution:
        """
        Determine how EVA should handle a (provider, model) pair.

        Steps
        -----
        0. provider="litellm" → legacy alias; silently route via litellm_handler
           and attach a deprecation warning (no behavioral change).
        1. provider in NATIVE_PROVIDERS → use native handler directly.
        2. litellm.model_cost has "{provider}/*" entries → route via
           litellm_handler with auto-prefixed model name.
        3. None of the above → unsupported.
        """
        p = _registry.canonical_name(provider)

        # ── 0. Legacy alias ────────────────────────────────────────────
        if p == "litellm":
            return ProviderResolution(
                provider="litellm",
                model=model,
                is_native=False,
                is_local=False,
                via_litellm=True,
                prefixed_model=model,   # caller already uses litellm model format
                supported=True,
                legacy_litellm=True,
                deprecation_warning=(
                    "Provider 'litellm' is a deprecated alias. "
                    "You can now specify the underlying provider directly "
                    "(e.g. provider='anthropic', provider='groq'). "
                    "The 'litellm' alias will continue working indefinitely."
                ),
            )

        # ── 1. Native handler ─────────────────────────────────────────
        if p in _NATIVE_PROVIDERS:
            return ProviderResolution(
                provider=p,
                model=model,
                is_native=True,
                is_local=(p in _LOCAL_PROVIDERS),
                via_litellm=False,
                prefixed_model=model,
                supported=True,
            )

        # ── 2. Registered LiteLLM-routed provider ─────────────────────
        spec = _registry.get_spec(p)
        if spec is not None and spec.via_litellm and spec.litellm_prefix:
            prefix = spec.litellm_prefix
            prefixed = model if model.startswith(prefix) else f"{prefix}{model}"
            return ProviderResolution(
                provider=spec.name,
                model=model,
                is_native=False,
                is_local=False,
                via_litellm=True,
                prefixed_model=prefixed,
                supported=True,
                chat_ready=spec.chat_ready,
                not_ready_reason=spec.not_ready_reason,
            )

        # ── 3. Unknown ────────────────────────────────────────────────
        return ProviderResolution(
            provider=p,
            model=model,
            is_native=False,
            is_local=False,
            via_litellm=False,
            prefixed_model=model,
            supported=False,
        )

    # ------------------------------------------------------------------
    # Live model listing
    # ------------------------------------------------------------------

    async def list_models(
        self,
        provider: str,
        api_key: Optional[str] = None,
        live: bool = False,
    ) -> List:
        """
        Return a list of LiveModel for the given provider.

        Uses a per-provider TTL cache (default 5 min).
        Pass live=True to bypass the cache and always fetch from the source.
        For Ollama (local), api_key is re-purposed as the custom host URL.

        Never raises — returns [] on any error.
        """
        from providers.capability.base import LiveModel  # noqa: F401

        p = _registry.canonical_name(provider)
        cache_key = f"{p}:{api_key or ''}"
        now = time.time()

        if not live and cache_key in _model_cache:
            ts, cached = _model_cache[cache_key]
            if now - ts < _cache_ttl():
                return cached

        models: List = []
        try:
            if p == "anthropic":
                from providers.capability.anthropic.provider_api import list_models as _lm
                models = await _lm(api_key=api_key)

            elif p == "openai":
                from providers.capability.openai.provider_api import list_models as _lm
                models = await _lm(api_key=api_key)

            elif p == "groq":
                from providers.capability.groq.provider_api import list_models as _lm
                models = await _lm(api_key=api_key)

            elif p == "ollama":
                from providers.capability.ollama.provider_api import list_models as _lm
                host = api_key if (api_key and api_key.startswith("http")) else None
                models = await _lm(host=host)

            elif p == "ollama_cloud":
                from providers.capability.ollama_cloud.provider_api import list_models as _lm
                models = await _lm(api_key=api_key)

            elif p == "litellm":
                models = []   # aggregator — no model list

            else:
                models = self._list_from_litellm_sdk(p)

        except Exception as exc:
            logger.warning(
                "list_models failed for provider '%s' (returning []): %s", p, exc
            )
            models = []

        _model_cache[cache_key] = (now, models)
        return models

    def _list_from_litellm_sdk(self, provider: str) -> List:
        """
        Filter litellm.model_cost by provider prefix, returning LiveModel list.

        model_id is the BARE name ("codestral-latest", not "mistral/codestral-
        latest"): the same form GET /providers/{p}/models/{id}/capabilities
        returns and POST /set-session expects. The routing prefix is re-added
        by resolve() when a chat actually starts, so exposing it here only
        gave the UI two spellings of one model.
        """
        from providers.capability.base import LiveModel
        try:
            import litellm
            prefix = _PROVIDER_ALIASES.get(provider)
            if not prefix:
                return []
            result = []
            for model_key, info in litellm.model_cost.items():
                if not model_key.startswith(prefix):
                    continue
                if not isinstance(info, dict):
                    continue
                # Chat only: "completion" models cannot hold a conversation.
                if info.get("mode", "chat") != "chat":
                    continue
                model_name = model_key[len(prefix):]
                result.append(LiveModel(
                    model_id=model_name,
                    display_name=model_name,
                    context_window=info.get("max_input_tokens"),
                    owned_by=provider,
                    active=True,
                ))
            return result
        except ImportError:
            return []

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate_cache(self, provider: Optional[str] = None) -> None:
        """
        Flush the model-listing cache.

        provider=None  → flush all providers.
        provider="groq" → flush only groq entries.
        """
        global _model_cache
        if provider:
            prefix = f"{provider.lower()}:"
            for k in list(_model_cache.keys()):
                if k.startswith(prefix):
                    del _model_cache[k]
        else:
            _model_cache.clear()

    # ------------------------------------------------------------------
    # Cost computation
    # ------------------------------------------------------------------

    def litellm_key(self, provider: str, model: str) -> Optional[str]:
        """
        The key under which LiteLLM's dataset holds (provider, model), or None.

        LiteLLM indexes some providers by their routing key ("groq/openai/gpt-
        oss-20b") and others by the bare name ("claude-haiku-4-5"), while a
        native provider's resolved model is always bare. Looking up only the
        resolved name meant a Groq model never matched, so cost was None for
        every Groq execution ever recorded. Try the routing-prefixed form, the
        "{provider}/{model}" form, then the bare name, in that order.
        """
        try:
            import litellm
        except ImportError:
            return None
        resolution = self.resolve(provider, model)
        for key in (resolution.prefixed_model, f"{resolution.provider}/{model}", model):
            if key and key in litellm.model_cost:
                return key
        return None

    def compute_call_cost(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> Optional[float]:
        """
        Compute the USD cost of a completed inference call.

        prompt_tokens is the TOTAL input (including any cached portion);
        cached_tokens is the part of it served from the provider's prompt cache,
        billed at the cache-read rate where the model has one (LiteLLM's
        cost_per_token takes the same convention).

        Returns
        -------
        float   — computed cost (always 0.0 for local providers)
        None    — cost data unavailable; do NOT fabricate a value
        """
        p = provider.lower()

        if p in _LOCAL_PROVIDERS:
            return 0.0

        if prompt_tokens == 0 and completion_tokens == 0:
            return None

        try:
            import litellm
            key = self.litellm_key(p, model)
            if key is None:
                return None            # no pricing data -- never fabricate a value
            kwargs = {}
            if cached_tokens:
                kwargs["cache_read_input_tokens"] = min(cached_tokens, prompt_tokens)
            result = litellm.cost_per_token(
                model=key,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                **kwargs,
            )
            # litellm.cost_per_token may return a tuple (prompt_cost, completion_cost)
            # or a single float depending on the version.
            if isinstance(result, (tuple, list)):
                return float(sum(result))
            return float(result)
        except Exception as exc:
            logger.debug(
                "compute_call_cost(%s/%s, %d+%d tokens): %s",
                provider, model, prompt_tokens, completion_tokens, exc,
            )
            return None

    def get_model_cost(self, provider: str, model: str) -> dict:
        """
        Return per-token pricing metadata for a model.

        Always safe to call — returns None values when cost data is unavailable.
        Local providers (Ollama) always return 0.0.
        """
        p = provider.lower()
        if p in _LOCAL_PROVIDERS:
            return {
                "input_cost_per_token": 0.0,
                "output_cost_per_token": 0.0,
                "cost_source_url": None,
                "note": "Local inference — no cost",
            }
        try:
            import litellm
            key = self.litellm_key(p, model)
            info = (litellm.model_cost.get(key) if key else None) or {}
            return {
                "input_cost_per_token": info.get("input_cost_per_token"),
                "output_cost_per_token": info.get("output_cost_per_token"),
                "cost_source_url": info.get("source"),
                "note": None,
            }
        except Exception:
            return {
                "input_cost_per_token": None,
                "output_cost_per_token": None,
                "cost_source_url": None,
                "note": None,
            }


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere:
#   from core.providers.resolver import resolver
# ---------------------------------------------------------------------------

resolver = ProviderResolver()
