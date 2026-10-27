# core/mcp/manager.py

from typing import List, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from core.db.repository import MCPRepo
from core.db.models import MCPConfig, MCPToolMeta
from core.mcp.connection import MCPConnection
from core.mcp.credentials import resolve_secrets
from core.mcp.naming import build_mcp_tool_name
from core.mcp.preview_registry import resolve_connection_args
from core.tools.dynamic_registry import DynamicToolRegistry, ToolManifest


class MCPManager:
    """
    Orchestrates MCP lifecycle for EVA:

    - Load enabled MCP configs
    - Sync tools / resources / prompts from MCP servers → DB cache
    - Register tools into dynamic registry
    - Unregister tools when disabled
    """

    def __init__(self, registry: DynamicToolRegistry | None = None):
        """
        registry: dynamic tool registry (optional).
        If registry is None, registration steps are skipped.
        """
        self.registry = registry

    # --------------------------------------------------
    # Shared helper — open connection from config
    # --------------------------------------------------
    def _connection(self, config: MCPConfig) -> MCPConnection:
        # S-11: config.env/.headers hold redacted placeholders at rest --
        # resolve_secrets() swaps in the real values from keyring only
        # here, at actual connection time.
        # CDP preview pane: resolve_connection_args() is a no-op for every
        # config except "playwright" -- see core/mcp/preview_registry.py.
        real_env, real_headers = resolve_secrets(config)
        return MCPConnection(
            name=config.name,
            transport=config.transport,
            command=config.command,
            args=resolve_connection_args(config),
            env=real_env,
            cwd=config.cwd,
            env_file=config.env_file,
            url=config.url,
            headers=real_headers,
        )

    # --------------------------------------------------
    # Load enabled MCP configs for a user
    # --------------------------------------------------
    async def load_user_configs(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> List[MCPConfig]:
        """Return all MCP configs for the given user (enabled and disabled)."""
        return await MCPRepo.list_configs(db, user_id)

    # --------------------------------------------------
    # Sync tools for a single config
    # --------------------------------------------------
    async def sync_tools_for_config(
        self,
        db: AsyncSession,
        config: MCPConfig,
    ) -> List[MCPToolMeta]:
        """
        Connects to the MCP server, lists tools, caches them in the DB,
        and returns the cached MCPToolMeta objects.
        """
        async with self._connection(config) as session:
            resp = await session.list_tools()

        await MCPRepo.delete_tools_for_config(db, config.id)

        synced_tools: List[MCPToolMeta] = []
        for tool in resp.tools:
            tool_data = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "output_schema": getattr(tool, "output_schema", None),
            }
            t = await MCPRepo.upsert_tool_meta(db, config.id, tool_data)
            synced_tools.append(t)

        return synced_tools

    # --------------------------------------------------
    # Sync resources for a single config
    # --------------------------------------------------
    async def sync_resources_for_config(
        self,
        db: AsyncSession,
        config: MCPConfig,
    ) -> list:
        """
        Connects to the MCP server, lists resources, caches their metadata.
        Returns an empty list if the server does not support resources.
        """
        try:
            async with self._connection(config) as session:
                resp = await session.list_resources()
        except Exception:
            return []

        await MCPRepo.delete_resources_for_config(db, config.id)

        synced: list = []
        for res in getattr(resp, "resources", []):
            resource_data = {
                "uri": str(res.uri),
                "name": getattr(res, "name", None),
                "description": getattr(res, "description", None),
                "mime_type": getattr(res, "mime_type", None),
            }
            r = await MCPRepo.upsert_resource_meta(db, config.id, resource_data)
            synced.append(r)

        return synced

    # --------------------------------------------------
    # Sync prompts for a single config
    # --------------------------------------------------
    async def sync_prompts_for_config(
        self,
        db: AsyncSession,
        config: MCPConfig,
    ) -> list:
        """
        Connects to the MCP server, lists prompt templates, caches their metadata.
        Returns an empty list if the server does not support prompts.
        """
        try:
            async with self._connection(config) as session:
                resp = await session.list_prompts()
        except Exception:
            return []

        await MCPRepo.delete_prompts_for_config(db, config.id)

        synced: list = []
        for prompt in getattr(resp, "prompts", []):
            args_list = []
            for arg in getattr(prompt, "arguments", []) or []:
                args_list.append({
                    "name": getattr(arg, "name", ""),
                    "description": getattr(arg, "description", None),
                    "required": getattr(arg, "required", False),
                })
            prompt_data = {
                "name": prompt.name,
                "description": getattr(prompt, "description", None),
                "arguments": args_list or None,
            }
            p = await MCPRepo.upsert_prompt_meta(db, config.id, prompt_data)
            synced.append(p)

        return synced

    # --------------------------------------------------
    # Register tools into dynamic registry (with per-config error capture)
    # --------------------------------------------------
    async def register_tools_for_user(
        self,
        db: AsyncSession,
        user_id: str,
        allowed_mcp_servers: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """
        Load all enabled MCP configs for the user and register their
        already-cached tools into the dynamic registry.

        P-06a: this is a pure DB read (MCPRepo.list_tools_for_config()), not
        a live connection to every enabled server. The cache is populated
        once when a config is created (api/mcp.py::create_mcp_config) and
        refreshed explicitly via POST /mcp/{id}/sync -- registration itself
        no longer pays a live-connection round trip (200-800ms per stdio
        server) on every single execution start, and a transiently
        unreachable server no longer drops its tools from the registry
        just because this particular execution couldn't reach it.

        Parameters
        ----------
        allowed_mcp_servers : B-053 — when provided (non-None, non-empty list),
            only MCP configs whose ``name`` appears in this list are registered.
            None or empty list means "all enabled configs" (default behaviour).

        Returns a dict {config_name: error_message} for any configs whose
        cache read failed (should be rare -- a DB error, not a connectivity
        one).  An empty dict means full success.
        """
        configs = await self.load_user_configs(db, user_id)
        errors: Dict[str, str] = {}

        # B-053: build allow-set for fast membership test (None → no restriction).
        allow_set = set(allowed_mcp_servers) if allowed_mcp_servers else None

        for cfg in configs:
            if not cfg.enabled:
                continue

            # B-053: skip configs not in the allow-set when one is provided.
            if allow_set is not None and cfg.name not in allow_set:
                continue

            try:
                tools = await MCPRepo.list_tools_for_config(db, cfg.id)
            except Exception as exc:
                errors[cfg.name] = str(exc)
                continue

            if self.registry:
                for tool in tools:
                    manifest = ToolManifest(
                        name=build_mcp_tool_name(cfg.name, tool.name),
                        description=tool.description or "",
                        input_schema=tool.input_schema or {},
                        output_schema=tool.output_schema,
                        origin=cfg.id,
                    )
                    self.registry.register_tool(manifest)

        return errors

    # --------------------------------------------------
    # Unregister tools when MCP config is removed or disabled
    # --------------------------------------------------
    async def unregister_tools_for_config(
        self,
        db: AsyncSession,
        config_id: str,
    ):
        """
        Remove tools for a given config from the dynamic registry (if present),
        and clear their cached DB entries.
        """
        if self.registry:
            tools = await MCPRepo.list_tools_for_config(db, config_id)
            for tool in tools:
                self.registry.remove_tool(tool.name)

        await MCPRepo.delete_tools_for_config(db, config_id)
