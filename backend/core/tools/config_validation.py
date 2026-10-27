# core/tools/config_validation.py
#
# Shared validation helpers for the subagent/daemon config builder tools
# (tools/default/subagent_config_tools.py, tools/default/daemon_config_tools.py).
#
# Lives here, not in tools/default/ or utils/: this isn't a tool itself (so
# tools/default/ -- which core/tools/loader.py scans expecting every file to
# register one -- is the wrong place), and it isn't generic, dependency-free
# code either (compare utils/tool_normalizer.py, which has zero knowledge of
# EVA's tool system) -- it directly reasons about DefaultToolRegistry,
# execution.dynamic_registry, and capability_registry. That makes it
# tool-*system* logic, which is exactly what core/tools/ already holds
# (registry.py, dispatcher.py, executor.py, dynamic_registry.py, ...).

from __future__ import annotations

from typing import Any, List, Optional


def validate_tool_names(tool_names: List[str], execution: Any) -> List[str]:
    """
    Return the subset of tool_names that are NOT recognised -- neither a
    registered default tool nor present in the execution's dynamic (MCP)
    registry. Empty list means every name is valid.
    """
    if not tool_names:
        return []

    from core.tools.registry import DefaultToolRegistry

    known = set(DefaultToolRegistry.all().keys())

    dynamic_registry = getattr(execution, "dynamic_registry", None)
    if dynamic_registry is not None:
        try:
            known |= {m.name for m in dynamic_registry.list_tools()}
        except Exception:
            pass

    return [name for name in tool_names if name not in known]


def validate_model_provider(provider: Optional[str], model: Optional[str]) -> Optional[str]:
    """
    Return a warning string if (provider, model) isn't recognised by
    capability_registry, or None if it's recognised or not checkable.

    Deliberately a soft warning, not a hard error: capability_registry can
    lag behind a provider's actual current model offerings, so refusing to
    save on an unrecognised pair would be too strict.
    """
    if not provider or not model:
        return None

    try:
        from core.providers.capability_registry import get_model
        if get_model(provider, model) is None:
            return (
                f"Provider/model '{provider}/{model}' is not recognised by EVA's "
                "capability registry. It may still work if the provider actually "
                "supports it -- double-check the spelling."
            )
    except Exception:
        pass

    return None
