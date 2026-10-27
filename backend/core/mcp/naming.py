# core/mcp/naming.py
"""
Single source of truth for EVA's MCP tool-naming convention.

Every MCP tool is exposed to the rest of EVA (registry, provider tool
schemas, dispatch) under a compound name: "<config_name><delimiter><tool_name>".
Before this module existed, that convention was duplicated as raw f-strings
and .split(".", 1) calls in four separate places (core/mcp/manager.py,
api/tools.py, core/mcp/runtime.py x2) using "." as the delimiter.

"." breaks every major LLM provider's tool-name validation (Anthropic:
^[a-zA-Z0-9_-]{1,128}$, OpenAI-compatible providers use an equivalent
restricted pattern) -- confirmed live: a real chat turn calling a real MCP
tool failed with a 400 from Anthropic ("tools.1.custom.name: String should
match pattern") the moment the tool was promoted into the model's active
toolkit. Reproduced identically on a clean baseline with no EVA-side
provider-handler changes, confirming the bug was the delimiter itself, not
anything provider-specific -- so the fix belongs here (the one place the
convention is defined), not duplicated into every provider handler.

"__" was chosen as the replacement delimiter: valid under every known
provider's tool-name character class, and unlikely to collide with typical
single-underscore/hyphenated MCP server names in the wild. To make the
split unambiguous by construction (not just by luck), MCP config names are
validated at creation/update time (api/mcp.py) to reject the delimiter --
see is_valid_mcp_config_name().
"""

from __future__ import annotations

from typing import Tuple

MCP_TOOL_NAME_DELIMITER = "__"


def build_mcp_tool_name(config_name: str, tool_name: str) -> str:
    """Compound name for a specific MCP tool, e.g. 'brave_search__web_search'."""
    return f"{config_name}{MCP_TOOL_NAME_DELIMITER}{tool_name}"


def build_mcp_tool_name_prefix(config_name: str) -> str:
    """Prefix matching every tool belonging to one MCP config (for pruning)."""
    return f"{config_name}{MCP_TOOL_NAME_DELIMITER}"


def parse_mcp_tool_name(compound_name: str) -> Tuple[str, str]:
    """
    Split a compound tool name back into (config_name, tool_name).

    Splits on the FIRST occurrence only -- config names are guaranteed
    delimiter-free (validated at creation time), so the first occurrence is
    always the true boundary even if the tool's own name (set by the MCP
    server, outside EVA's control) happens to contain the delimiter too.

    Raises ValueError if the delimiter isn't present at all -- callers
    should treat this as "not a valid MCP tool name", matching the
    pre-existing "invalid name" error contract this replaces.
    """
    if MCP_TOOL_NAME_DELIMITER not in compound_name:
        raise ValueError(
            f"'{compound_name}' is not a valid MCP tool name "
            f"(missing '{MCP_TOOL_NAME_DELIMITER}' delimiter)"
        )
    config_name, tool_name = compound_name.split(MCP_TOOL_NAME_DELIMITER, 1)
    return config_name, tool_name


def is_valid_mcp_config_name(name: str) -> bool:
    """
    False if `name` contains the reserved delimiter -- such a name would
    make parse_mcp_tool_name() ambiguous (ok, for split(..., 1) it'd still
    parse "unambiguously" in the sense of always taking the first token,
    but the resulting config_name segment would then be wrong/truncated
    relative to the actual config name). Reject at the source instead.
    """
    return MCP_TOOL_NAME_DELIMITER not in name
