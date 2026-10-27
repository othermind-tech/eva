# core/mcp/runtime.py

"""
Canonical MCP execution runtime for EVA.

This module is the ONLY place where:
- Tools are executed
- Results are normalized

Connection lifecycle (opening, reuse, reconnect-on-failure, closing) is
owned by MCPSessionPool (core/mcp/session_pool.py, P-06b) -- the pool
supersedes M-04's original fresh-connection-per-call retry helper
(call_with_reconnect), which was removed once the pool's call_tool()
started providing the same evict-and-retry-once behaviour on top of a
persistent connection instead of a throwaway one.
"""

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.db.models import MCPConfig, MCPToolMeta
from core.mcp.naming import build_mcp_tool_name_prefix, parse_mcp_tool_name
from core.mcp.session_pool import MCPToolRejected, mcp_session_pool
from core.providers.multimodal import is_vision_capable, within_inline_limit
from core.tools.base import ToolResult

# M-07: the key ToolResult.result carries the collected images under, when the current model is
# vision-capable and the tool result contained at least one. Read back by
# utils/message_builder.py::build_tool_message, which turns it into a real image content part
# instead of stringifying it; never written when there is no vision-capable model in play, so
# every other reader of a tool result (RAG, memory, the trace API) sees exactly the same shape
# it always has.
IMAGE_RESULT_KEY = "_eva_images"


def _content_block_to_text(block: Any) -> str:
    """
    Text of one MCP result content block. Text blocks pass through; an image
    (e.g. a browser screenshot) becomes a short placeholder, because
    str(ImageContent) would inline the whole base64 payload into the model's
    context -- tens of thousands of tokens of noise, and unreadable to a
    text-only model anyway. Also the fallback for an image this process
    decided NOT to inline (see _content_block_to_image) -- not vision-capable,
    or over IMAGE_INLINE_MAX_BYTES -- so no image is ever silently dropped
    with nothing left in its place.
    """
    text = getattr(block, "text", None)
    if text is not None:
        return text
    if getattr(block, "type", None) == "image":
        approx_kb = len(getattr(block, "data", "") or "") * 3 // 4 // 1024
        return f"[image omitted: {getattr(block, 'mime_type', 'unknown type')}, ~{approx_kb} KB]"
    return str(block)


def _content_block_to_image(block: Any) -> Optional[Tuple[str, str]]:
    """
    (mime_type, base64_data) for a block that is an image and small enough to inline (see
    IMAGE_INLINE_MAX_BYTES), else None -- including for a non-image block, an image block with
    no data, or one over the size cap. The caller falls back to _content_block_to_text for
    anything this returns None for, so an oversized image still reaches the model as a
    placeholder rather than vanishing.
    """
    if getattr(block, "type", None) != "image":
        return None
    data = getattr(block, "data", None)
    if not data:
        return None
    approx_bytes = len(data) * 3 // 4  # base64 -> raw byte estimate, no need to actually decode
    if not within_inline_limit(approx_bytes):
        return None
    mime_type = getattr(block, "mime_type", None) or "application/octet-stream"
    return mime_type, data


class MCPRuntime:
    """
    Executes MCP-backed tools in a safe, per-execution scope.
    """

    @staticmethod
    async def execute(
        *,
        db: AsyncSession,
        user_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        project_id: Optional[str] = None,
        user_registry: Optional[Any] = None,
    ) -> ToolResult:
        """
        Execute an MCP tool.

        tool_name format:
            <mcp_name>.<tool>

        Example:
            kite.place_order

        Parameters
        ----------
        project_id : str, optional
            The project context of the calling execution.  When provided, configs
            that are either global (project_id IS NULL) or scoped to this project
            are eligible.  When None (no project context), only global configs are
            used.  Project-cascade-paused configs (project_paused=True) are always
            excluded regardless of their enabled flag.
        user_registry : UserToolRegistry, optional
            When provided, a confirmed connection failure (the session
            pool's own retry already failed -- see step 4) prunes this
            config's tools from the registry so the model doesn't keep
            retrying a known-dead server for the rest of the conversation.
            None is safe -- just skips pruning (e.g. no execution context
            available to resolve one from).
        """

        # --------------------------------------------
        # 1. Parse tool namespace
        # --------------------------------------------
        try:
            mcp_name, mcp_tool = parse_mcp_tool_name(tool_name)
        except ValueError:
            return ToolResult(result={"error": f"Invalid MCP tool name '{tool_name}'"})

        # --------------------------------------------
        # 2. Resolve MCP config (project-scope aware)
        # --------------------------------------------
        # Build the project-scope filter:
        #   - With project context  → global MCPs OR MCPs for this project
        #   - Without project context → global MCPs only
        if project_id:
            scope_filter = or_(
                MCPConfig.project_id.is_(None),
                MCPConfig.project_id == project_id,
            )
        else:
            scope_filter = MCPConfig.project_id.is_(None)

        result = await db.execute(
            select(MCPConfig).where(
                MCPConfig.user_id == user_id,
                MCPConfig.name == mcp_name,
                MCPConfig.enabled == True,         # noqa: E712
                MCPConfig.project_paused == False,  # noqa: E712  cascade-paused = unavailable
                scope_filter,
            )
        )
        config = result.scalars().first()

        if not config:
            return ToolResult(result={
                "error": f"MCP '{mcp_name}' not enabled or not available in this project context",
            })

        # --------------------------------------------
        # 3. Resolve tool metadata (validation)
        # --------------------------------------------
        result = await db.execute(
            select(MCPToolMeta).where(
                MCPToolMeta.config_id == config.id,
                MCPToolMeta.name == mcp_tool,
            )
        )
        tool_meta = result.scalars().first()

        if not tool_meta:
            return ToolResult(result={"error": f"Tool '{tool_name}' not found in MCP"})

        # --------------------------------------------
        # 4. Execute via the persistent session pool (P-06b) -- reuses an
        #    already-open connection when healthy, verifies liveness with
        #    a pre-call ping, and reconnects-and-retries once on failure.
        # --------------------------------------------
        try:
            raw_result = await mcp_session_pool.call_tool(config, mcp_tool, arguments)

            # --------------------------------------------
            # 5. Normalize MCP result
            # --------------------------------------------
            content = raw_result.content

            if isinstance(content, list):
                # M-07: for a vision-capable model, keep an inlineable image's real bytes
                # instead of collapsing it to "[image omitted: ...]" -- see IMAGE_RESULT_KEY
                # above for how build_tool_message() turns this into a real image content
                # part. Every other model (today, every model on this account) takes the
                # unchanged path below: a plain list of strings, one per block.
                provider = model = None
                try:
                    from config.settings import get_user_session  # noqa: PLC0415
                    _session = get_user_session(user_id)
                    provider, model = _session.get("provider"), _session.get("model")
                except Exception:
                    pass  # no session to read -> provider/model stay None -> not vision-capable

                if is_vision_capable(provider, model):
                    text_parts: List[str] = []
                    images: List[Tuple[str, str]] = []
                    for c in content:
                        img = _content_block_to_image(c)
                        if img is not None:
                            images.append(img)
                        else:
                            text_parts.append(_content_block_to_text(c))
                    if images:
                        output = {
                            "text": "\n".join(p for p in text_parts if p),
                            IMAGE_RESULT_KEY: [
                                {"mime_type": m, "data_b64": d} for m, d in images
                            ],
                        }
                    else:
                        output = text_parts
                else:
                    # Common MCP pattern: list of content blocks
                    output = [_content_block_to_text(c) for c in content]
            else:
                output = getattr(content, "text", content)

            return ToolResult(result=output)

        except MCPToolRejected as e:
            # V6.59: the server is alive and refused THIS call (a protocol-level error
            # response). That is an ordinary tool failure, reported to the model like any
            # other -- not a dead server, so the server's tools must stay available.
            return ToolResult(result={"error": str(e)})

        except Exception as e:
            # The session pool already retried once internally (step 4) --
            # reaching here means the connection is confirmed dead, not a
            # transient blip. Prune this config's tools from the registry
            # so the model doesn't keep retrying a known-dead server for
            # the rest of the conversation (live-tested failure mode this
            # exists to prevent: repeated failed attempts tripping EVA's
            # duplicate-tool-call breaker).
            unavailable_note = ""
            if user_registry is not None:
                try:
                    pruned = user_registry.prune_dynamic_tools(build_mcp_tool_name_prefix(mcp_name))
                    if pruned:
                        unavailable_note = (
                            f" '{mcp_name}' is now unavailable for the rest of this "
                            f"conversation -- {len(pruned)} tool(s) removed from your "
                            "active toolkit. Do not retry this server."
                        )
                except Exception:
                    pass  # pruning is best-effort; the call's own error still returns
            # MCP failures are usually transient (network / process), but a
            # failure here has already survived the pool's own retry. The
            # dispatcher's retry loop only reacts to raised exceptions (not
            # a status field on the returned ToolResult), so this is
            # reported the same way every other tool reports a failure to
            # the model -- as a result dict, not a raise.
            return ToolResult(result={"error": str(e) + unavailable_note})
