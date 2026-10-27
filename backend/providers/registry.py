# providers/registry.py
#
# The single provider registry (V6.59 Phase 1).
#
# Before this module existed, "which providers does EVA know" was answered
# independently in six places (PROVIDER_KEY_MAP, the resolver's native set and
# alias table, the tool builder's allow-list, the factory's if-chain, the
# curated /providers/list, and the Secrets API's cloud list). They drifted: a
# provider could hold a stored key and be listed in the UI yet be refused by
# the tool builder the moment a chat started. Every one of those places now
# derives from the table below, so adding a provider is one edit here.
#
# This module is pure data + stdlib on purpose. config/env_loader.py imports
# it (through providers/capability/base.py) before logging, settings or
# LiteLLM are available, so it must never import any of them.
#
# Three kinds of provider:
#   native   -- EVA has a dedicated handler (anthropic, openai, groq, ollama,
#               ollama_cloud).
#   gateway  -- served by LiteLLMHandler with the model auto-prefixed
#               (deepseek, mistral, gemini, ...). Only providers listed here
#               are reachable; EVA deliberately does NOT expose every provider
#               LiteLLM knows, because each has its own credential shape and
#               reasoning/parameter quirks that have to be verified one by one.
#   legacy   -- keyed in the OS keyring so an existing stored key can still be
#               listed/deleted, but NOT chat-ready: the provider needs
#               structured credentials (Azure endpoint+version, AWS key pair
#               +region, Vertex project) that a single API-key field cannot
#               carry yet, or it has no tool-calling models (Perplexity).

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

# Tool-schema families (see core/tools/builder.py).
FAMILY_OPENAI_COMPAT = "openai_compat"
FAMILY_ANTHROPIC = "anthropic"

KIND_NATIVE = "native"
KIND_GATEWAY = "gateway"
KIND_LEGACY = "legacy"


@dataclass(frozen=True)
class ProviderSpec:
    name: str                          # canonical identifier used in every API call
    display_name: str
    kind: str                          # KIND_NATIVE | KIND_GATEWAY | KIND_LEGACY
    env_key: Optional[str]             # env var holding the key; None = local, no key
    litellm_prefix: Optional[str] = None   # "mistral/" -- gateway/legacy only
    family: str = FAMILY_OPENAI_COMPAT
    chat_ready: bool = True            # a handler path exists for this provider
    curated: bool = True               # shown in GET /providers/list
    is_local: bool = False
    aliases: Tuple[str, ...] = ()      # other spellings accepted on input
    not_ready_reason: Optional[str] = None

    @property
    def is_native(self) -> bool:
        return self.kind == KIND_NATIVE

    @property
    def via_litellm(self) -> bool:
        return self.kind in (KIND_GATEWAY, KIND_LEGACY)


def _native(name, display, env_key, *, family=FAMILY_OPENAI_COMPAT, is_local=False) -> ProviderSpec:
    return ProviderSpec(name, display, KIND_NATIVE, env_key, family=family, is_local=is_local)


def _gateway(name, display, env_key, *, prefix=None, aliases=()) -> ProviderSpec:
    return ProviderSpec(
        name, display, KIND_GATEWAY, env_key,
        litellm_prefix=prefix or f"{name}/", aliases=tuple(aliases),
    )


def _legacy(name, display, env_key, *, prefix=None, reason) -> ProviderSpec:
    return ProviderSpec(
        name, display, KIND_LEGACY, env_key,
        litellm_prefix=prefix or f"{name}/",
        chat_ready=False, curated=False, not_ready_reason=reason,
    )


# Order is the order shown in the UI: native first, then gateway providers.
_SPECS: Tuple[ProviderSpec, ...] = (
    _native("groq",         "Groq",           "GROQ_API_KEY"),
    _native("openai",       "OpenAI",         "OPENAI_API_KEY"),
    _native("anthropic",    "Anthropic",      "ANTHROPIC_API_KEY", family=FAMILY_ANTHROPIC),
    _native("ollama",       "Ollama (Local)", None, is_local=True),
    _native("ollama_cloud", "Ollama Cloud",   "OLLAMA_CLOUD_API_KEY"),

    _gateway("deepseek",     "DeepSeek",            "DEEPSEEK_API_KEY"),
    _gateway("gemini",       "Google Gemini",       "GEMINI_API_KEY"),
    _gateway("mistral",      "Mistral AI",          "MISTRAL_API_KEY"),
    _gateway("xai",          "xAI (Grok)",          "XAI_API_KEY"),
    _gateway("moonshot",     "Moonshot (Kimi)",     "MOONSHOT_API_KEY"),
    _gateway("zai",          "Z.AI (GLM)",          "ZAI_API_KEY"),
    _gateway("dashscope",    "Alibaba DashScope (Qwen)", "DASHSCOPE_API_KEY"),
    _gateway("minimax",      "MiniMax",             "MINIMAX_API_KEY"),
    _gateway("cerebras",     "Cerebras",            "CEREBRAS_API_KEY"),
    _gateway("together_ai",  "Together AI",         "TOGETHERAI_API_KEY", aliases=("together",)),
    _gateway("fireworks_ai", "Fireworks AI",        "FIREWORKS_AI_API_KEY"),
    _gateway("deepinfra",    "DeepInfra",           "DEEPINFRA_API_KEY"),
    _gateway("novita",       "Novita AI",           "NOVITA_API_KEY"),

    _legacy("azure",       "Azure OpenAI", "AZURE_API_KEY",
            reason="needs an endpoint URL and API version, not just a key"),
    _legacy("bedrock",     "AWS Bedrock",  "AWS_ACCESS_KEY_ID",
            reason="needs an AWS key pair and region, not a single key"),
    _legacy("vertex_ai",   "Google Vertex AI", "VERTEX_AI_KEY",
            reason="needs a GCP project, location and service-account credentials"),
    _legacy("cohere",      "Cohere",       "COHERE_API_KEY",
            reason="not yet verified against EVA's tool-calling loop"),
    _legacy("perplexity",  "Perplexity",   "PERPLEXITYAI_API_KEY",
            reason="no tool-calling models, so it cannot run EVA's agent loop"),
    _legacy("huggingface", "Hugging Face", "HUGGINGFACE_API_KEY",
            reason="not yet verified against EVA's tool-calling loop"),

    # Deprecated alias for "route through LiteLLM with a caller-supplied model
    # string". Kept chat-ready so existing sessions keep working, but never
    # offered to new users.
    ProviderSpec("litellm", "LiteLLM (legacy)", KIND_GATEWAY, "LITELLM_API_KEY",
                 litellm_prefix=None, curated=False),
)

PROVIDERS: Dict[str, ProviderSpec] = {s.name: s for s in _SPECS}
_ALIASES: Dict[str, str] = {a: s.name for s in _SPECS for a in s.aliases}


def canonical_name(provider: str) -> str:
    """Lower-case + resolve aliases ("together" -> "together_ai")."""
    p = (provider or "").lower().strip()
    return _ALIASES.get(p, p)


def get_spec(provider: str) -> Optional[ProviderSpec]:
    return PROVIDERS.get(canonical_name(provider))


def key_map() -> Dict[str, Optional[str]]:
    """provider -> env-var name (None for local). Backs PROVIDER_KEY_MAP."""
    return {s.name: s.env_key for s in _SPECS}


def curated_specs() -> List[ProviderSpec]:
    return [s for s in _SPECS if s.curated]


def native_names() -> FrozenSet[str]:
    return frozenset(s.name for s in _SPECS if s.is_native)


def local_names() -> FrozenSet[str]:
    return frozenset(s.name for s in _SPECS if s.is_local)


def alias_prefixes() -> Dict[str, str]:
    """name/alias -> LiteLLM model prefix, for every gateway/legacy provider."""
    out: Dict[str, str] = {}
    for s in _SPECS:
        if s.litellm_prefix:
            out[s.name] = s.litellm_prefix
            for a in s.aliases:
                out[a] = s.litellm_prefix
    return out


def tool_family(provider: str) -> Optional[str]:
    """Tool-schema family for a chat-ready provider, else None."""
    spec = get_spec(provider)
    if spec is None or not spec.chat_ready:
        return None
    return spec.family


def is_chat_supported(provider: str) -> bool:
    """True when EVA has a working handler path for this provider."""
    spec = get_spec(provider)
    return bool(spec and spec.chat_ready)
