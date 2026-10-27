# core/handler_utils.py

from typing import Optional
from core.factory import HandlerFactory
from core.tools.user_registry import UserToolRegistry
from core.api import ApiHandler


def create_provider_handler(
    *,
    provider: str,
    api_key: str,
    model: str,
    tools: Optional[UserToolRegistry] = None,
    dispatcher=None,
    enable_reasoning: Optional[str] = None,  # V6.7: level string, or None -- unused by any handler's __init__ today (reasoning is a create_message()-time param, not constructor config); kept for signature compatibility with existing callers
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> ApiHandler:
    """
    Canonical helper to create provider handlers.
    Ensures all required config is always present.

    max_tokens / temperature (V6.4): optional, provider-agnostic generation
    parameters. Each handler decides internally whether/how to forward them
    to its underlying API -- see core/providers/param_support.py. Omit both
    (the default) to preserve exact prior behaviour: no cap, provider default
    temperature. Intended for short-lived, deterministic helper calls
    (verification, classification, planning) rather than the main
    conversational flow, which should generally not set these.
    """

    if tools is None:
        tools = UserToolRegistry(default_tools=False, enabled_tools=[])

    config = {
        "api_key": api_key,
        "model": model,
        "tools": tools,
        "dispatcher": dispatcher,
        "enable_reasoning": enable_reasoning,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    return HandlerFactory.create_handler(provider, config)
