# core/tools/user_registry.py

from typing import Any, Dict, Optional, Set, Type
from .registry import DefaultToolRegistry, CustomToolRegistry
from .base import ToolBase

# Tools that are ALWAYS included in the initial provider_tools list when
# progressive disclosure is active, regardless of promotion state.
# describe_tool is the gateway for promoting other tools.
_ALWAYS_ACTIVE_TOOLS = {"describe_tool"}


class UserToolRegistry:
    """
    Per-user tool registry:
    - loads default tools
    - includes only user-enabled custom tools
    - filters out chat_excluded (agent_only) tools when execution_mode is 'conversational'

    V5.0 Sprint 2 — Progressive Tool Disclosure:
    - _is_progressive: when True, only _ALWAYS_ACTIVE_TOOLS + promoted tools
      are included in the provider tool list per turn (get_active_tool_classes).
      The full tool set remains available for describe_tool lookups.
    - promote(name): marks a tool as promoted, making it available for the
      next provider API call.
    """

    def __init__(
        self,
        default_tools: bool = True,
        enabled_tools: Optional[list] = None,
        execution_mode: str = "",
        progressive: bool = False,
        allowed_categories: Optional[Set[str]] = None,
        dynamic_registry: Optional[Any] = None,
        allowed_default_tools: Optional[Set[str]] = None,
    ):
        enabled_tools = enabled_tools or []

        self.tools: Dict[str, Type[ToolBase]] = {}
        self._is_progressive: bool = progressive
        self._promoted_tools: Set[str] = set()
        self._execution_mode: str = execution_mode
        # MCP tool source. Progressive registries defer these to on-demand
        # promote() (see below) so manifest + describe_tool stays the single
        # discovery path. Non-progressive registries (subagents, daemons,
        # parallel-delegate children) never call describe_tool -- they get
        # their full tool set immediately -- so MCP tools are merged in
        # below instead, at construction time.
        self.dynamic_registry: Optional[Any] = dynamic_registry

        # Load system tools
        if default_tools:
            self.tools.update(DefaultToolRegistry.all())

        # allowed_default_tools: unlike enabled_tools (which only ever
        # gated custom/MCP tools -- default_tools=True always loaded every
        # default tool unconditionally), this actually restricts the
        # default toolset. Callers that hand a configured tool list to a
        # background agent (daemons, subagents) and expect it to mean
        # "only these tools" need this -- enabled_tools alone never
        # enforced that for default tools. None preserves prior behavior.
        if allowed_default_tools is not None:
            self.tools = {
                name: cls
                for name, cls in self.tools.items()
                if name in allowed_default_tools or name in _ALWAYS_ACTIVE_TOOLS
            }

        # Load selected custom tools
        for name, tool_cls in CustomToolRegistry.all().items():
            if name in enabled_tools:
                self.tools[name] = tool_cls

        # Load selected MCP tools for non-progressive registries. Gated by
        # enabled_tools -- the same opt-in convention as custom tools above
        # -- so a subagent/daemon only gets the MCP tools its config
        # explicitly lists, not automatic access to every server the user
        # has configured.
        if dynamic_registry is not None and not progressive:
            from core.tools.tool_adapter import build_toolbase_subclass_from_manifest
            for manifest in dynamic_registry.list_tools():
                if manifest.name in enabled_tools:
                    self.tools[manifest.name] = build_toolbase_subclass_from_manifest(manifest)

        # In conversational (Chat) mode, exclude tools that require agent/PEV context.
        # Uses chat_excluded (canonical V5.0) with fallback to legacy agent_only flag.
        if execution_mode == "conversational":
            self.tools = {
                name: cls
                for name, cls in self.tools.items()
                if not (
                    getattr(cls, "chat_excluded", False)
                    or getattr(cls, "agent_only", False)
                )
            }

        # Category-based filtering (B1): restrict tool set to allowed categories.
        # None means no category restriction (all categories allowed).
        if allowed_categories is not None:
            self.tools = {
                name: cls
                for name, cls in self.tools.items()
                if getattr(cls, "category", "general") in allowed_categories
                # describe_tool is always kept regardless of category filter
                or name in _ALWAYS_ACTIVE_TOOLS
            }

    # ── Progressive Disclosure API ───────────────────────────────────────────

    def promote(self, name: str) -> bool:
        """
        Mark a tool as promoted, making it available in the provider tool
        list from the next ReAct turn onwards.

        Returns True if the tool exists and was added; False if unknown.

        If name isn't a locally-known tool, falls back to the dynamic (MCP)
        registry: builds a real ToolBase subclass from its manifest and
        inserts it into self.tools, so it becomes an ordinary member of the
        active set from here on -- get_active_tool_classes() only ever
        looks at self.tools + _promoted_tools, so this is what makes an
        MCP tool actually reachable by the model after describe_tool()
        looks it up.
        """
        if name in self.tools:
            self._promoted_tools.add(name)
            return True

        if self.dynamic_registry is not None:
            manifest = self.dynamic_registry.get(name)
            if manifest is not None:
                from core.tools.tool_adapter import build_toolbase_subclass_from_manifest
                self.tools[name] = build_toolbase_subclass_from_manifest(manifest)
                self._promoted_tools.add(name)
                return True

        return False

    def get_active_tool_classes(self) -> Dict[str, Type[ToolBase]]:
        """
        Return the tool classes that should be included in the provider
        tool list for the CURRENT turn.

        - Non-progressive mode: returns the full tool set (same as all_tools).
        - Progressive mode: returns only _ALWAYS_ACTIVE_TOOLS + promoted tools.
          This is the token-efficient subset sent to the LLM provider API.
          The describe_tool meta-tool is always present so the model can
          promote additional tools on demand.
        """
        if not self._is_progressive:
            return self.tools

        active: Dict[str, Type[ToolBase]] = {}
        for name, cls in self.tools.items():
            if name in _ALWAYS_ACTIVE_TOOLS or name in self._promoted_tools:
                active[name] = cls
        return active

    def promoted_count(self) -> int:
        """Return the number of currently promoted tools."""
        return len(self._promoted_tools)

    def prune_dynamic_tools(self, name_prefix: str) -> list:
        """
        Remove every tool whose name starts with name_prefix (the MCP
        naming convention -- see core/mcp/naming.py -- is
        "{mcp_name}{delimiter}{tool_name}", so name_prefix is typically
        build_mcp_tool_name_prefix(mcp_name)) from the active registry AND from
        dynamic_registry itself, so a confirmed-dead MCP server's tools
        stop appearing in future manifests too, not just this turn's
        active set.

        Called when core/mcp/runtime.py::MCPRuntime.execute() sees a
        connection failure survive the session pool's own retry -- i.e.
        the server is confirmed unreachable, not just a transient blip.
        Without this, the model can plausibly keep retrying the same dead
        tool for the rest of the conversation (live-tested behaviour: this
        is exactly the pattern that trips EVA's duplicate-tool-call
        breaker). Scoped to this registry/conversation -- the config
        itself is untouched and becomes available again on its own via
        the next sync.

        Returns the list of pruned tool names (for logging).
        """
        pruned = [name for name in self.tools if name.startswith(name_prefix)]
        for name in pruned:
            del self.tools[name]
            self._promoted_tools.discard(name)
            if self.dynamic_registry is not None:
                self.dynamic_registry.remove_tool(name)
        return pruned

    # ── Compatibility ────────────────────────────────────────────────────────

    def all_tools(self) -> Dict[str, Type[ToolBase]]:
        """Return the complete available tool set (ignores progressive state)."""
        return self.tools
