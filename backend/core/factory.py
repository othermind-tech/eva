# core/factory.py

from typing import Dict, Any
from core.api import ApiHandler

from providers.handlers.groq_handler import GroqHandler
from providers.handlers.openai_handler import OpenAIHandler
from providers.handlers.litellm_handler import LiteLLMHandler
from providers.handlers.ollama_handler import OllamaHandler
from providers.handlers.ollama_cloud_handler import OllamaCloudHandler
from providers.handlers.anthropic_handler import AnthropicHandler

from core.tools.builder import build_provider_tools
from core.tools.user_registry import UserToolRegistry


class HandlerFactory:

    @staticmethod
    def create_handler(provider: str, config: Dict[str, Any]) -> ApiHandler:
        from providers.registry import canonical_name
        provider = canonical_name(provider)

        # Every handler is handed an explicit key from the one credential
        # store (providers.credentials). Handlers must never depend on an SDK's
        # own os.environ fallback -- the process env no longer carries keys.
        from providers import credentials as _credentials
        config = {**config, "api_key": _credentials.resolve_api_key(provider, config.get("api_key"))}

        # config["tools"] must be a UserToolRegistry instance
        user_tools: UserToolRegistry = config.get("tools")
        if user_tools is None:
            raise ValueError("HandlerFactory requires user-specific tool registry (tools=...)")

        # 1. Get tool classes
        tool_classes = user_tools.all_tools()     # {name: ToolClass}

        # 2. Convert → provider JSON tool schema
        provider_tools = build_provider_tools(provider, tool_classes)

        # 3. Merge into handler config
        handler_config = {
            **config,
            "provider_tools": provider_tools,
            "tool_classes": tool_classes,
        }

        if provider == "groq":
            return GroqHandler(handler_config)

        if provider == "openai":
            return OpenAIHandler(handler_config)

        if provider == "litellm":
            return LiteLLMHandler(handler_config)

        if provider == "ollama":
            return OllamaHandler(handler_config)

        if provider == "ollama_cloud":
            return OllamaCloudHandler(handler_config)

        if provider == "anthropic":
            return AnthropicHandler(handler_config)

        # ── Registered gateway providers: route via LiteLLM ──────────────
        # (build_provider_tools() above already refused anything that is not
        # chat-ready, so reaching here means the registry vouches for it.)
        from core.providers.resolver import resolver as _resolver
        resolution = _resolver.resolve(provider, handler_config.get("model", ""))
        if resolution.supported and resolution.via_litellm and resolution.chat_ready:
            handler_config = {**handler_config, "model": resolution.prefixed_model}
            return LiteLLMHandler(handler_config)

        raise ValueError(f"Unknown provider: {provider}")
