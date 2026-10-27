# core/tools/builder.py

from typing import Dict, Any, List
from logging import getLogger

from providers import registry as _registry

from .base import ToolBase

logger = getLogger(__name__)


def build_provider_tools(provider: str, tools: Dict[str, ToolBase]) -> List[Dict[str, Any]]:
    """
    Convert our internal tool registry → provider-specific tool schema.

    provider: any chat-ready provider in providers/registry.py (native:
              openai, groq, ollama, ollama_cloud, anthropic; gateway:
              deepseek, mistral, gemini, ... routed through LiteLLM)
    tools: dict of { tool_name: ToolClass }

    IMPORTANT — tool schema formats differ by provider family:

      OpenAI-compatible (openai, groq, ollama, ollama_cloud, litellm):
        {
          "type": "function",
          "function": {
            "name": "...",
            "description": "...",
            "parameters": { <JSON Schema> }
          }
        }

      Anthropic-native (anthropic):
        {
          "name": "...",
          "description": "...",
          "input_schema": { <JSON Schema, no top-level "title"> }
        }

    To add a provider, add it to providers/registry.py (and, for a new native
    handler, to core/factory.py); give it a new family here only if its schema
    format differs from the OpenAI format.
    LiteLLM handles wire-format translation for its own handler internally.
    """
    logger.debug(
        "Building provider tools",
        extra={
            "provider": provider,
            "tool_count": len(tools),
        },
    )

    provider = provider.lower()

    # ── Provider family ──────────────────────────────────────────────────────
    # Comes from the single provider registry (providers/registry.py), so a
    # provider that can be configured and listed can never be refused here
    # (the bug that made "deepseek" fail only once a chat started).
    family = _registry.tool_family(provider)
    if family is None:
        spec = _registry.get_spec(provider)
        if spec is not None and not spec.chat_ready:
            reason = f"Provider '{spec.name}' is not chat-ready: {spec.not_ready_reason}."
        else:
            reason = f"Unknown provider '{provider}'."
        logger.error(
            "No handler path for provider",
            extra={"provider": provider},
        )
        raise ValueError(
            f"{reason} Chat-ready providers: "
            f"{sorted(n for n, s in _registry.PROVIDERS.items() if s.chat_ready)}."
        )
    provider = _registry.canonical_name(provider)
    _OPENAI_COMPAT = family == _registry.FAMILY_OPENAI_COMPAT
    _ANTHROPIC_NATIVE = family == _registry.FAMILY_ANTHROPIC

    built = []

    for tool_name, tool_cls in tools.items():
        schema = tool_cls.args_schema.model_json_schema()

        if _OPENAI_COMPAT:
            # OpenAI function-calling format — used by openai, groq, ollama,
            # ollama_cloud, and LiteLLM (which translates internally).
            built.append({
                "type": "function",
                "function": {
                    "name":        tool_name,
                    "description": tool_cls.description,
                    "parameters":  schema,
                }
            })

        elif _ANTHROPIC_NATIVE:
            # Anthropic tool format — no "type":"function" wrapper, uses
            # "input_schema" instead of "parameters", and must not have a
            # top-level "title" key (Anthropic rejects it as invalid schema).
            anthropic_schema = {k: v for k, v in schema.items() if k != "title"}
            built.append({
                "name":         tool_name,
                "description":  tool_cls.description,
                "input_schema": anthropic_schema,
            })

    logger.debug(
        "Provider tools built",
        extra={
            "provider":    provider,
            "built_tools": len(built),
        },
    )
    return built
