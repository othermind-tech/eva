from typing import List, Dict, Any, Tuple, Optional
import json
from logging import getLogger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from core.types import MemoryMessage
from core.memory.token_counter import TokenCounter
from core.memory.policies import MemoryPolicy, DEFAULT_MEMORY_POLICY
from core.memory.summarizer import ConversationSummarizer
from core.db.models import Message, ConversationSummary, ToolCall
from collections import defaultdict

logger = getLogger(__name__)


def _build_tool_stub(calls: List[ToolCall]) -> str:
    """
    Convert a list of ToolCall records into a compact plain-text stub.

    Matches the B-067 format used by worker.py so history looks consistent
    whether it came from the engine or the worker's active-turn path:

        ⚙ web_search(query=Mumbai weather today)
        ⚙ read_file(path=/data/report.csv)
    """
    parts = []
    for tc in calls:
        args = tc.arguments or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        arg_str = ", ".join(
            f"{k}={str(v)[:60]}"
            for k, v in list(args.items())[:2]
        )
        parts.append(f"⚙ {tc.tool_name}({arg_str})")
    return "\n".join(parts)


class MemoryEngine:
    def __init__(
        self,
        db: AsyncSession,
        policy: MemoryPolicy = DEFAULT_MEMORY_POLICY,
    ):
        self.db = db
        self.policy = policy
    # ------------------------------------------------------------------
    # INTERNAL: Tool-aware reconstruction helper
    # ------------------------------------------------------------------
    def _append_message_with_tools(
        self,
        context: List[dict],
        msg: Message,
        tool_calls_by_message: Dict[str, List[ToolCall]],
        image_by_message_id: Optional[Dict[str, Tuple[str, str]]] = None,
    ):
        # USER MESSAGE
        if msg.role == "user":
            # M-07: this message is the placeholder api/conversation_management.py injects for
            # an attached image ("[IMAGE ATTACHMENT] ... refer to it") -- image_by_message_id is
            # only ever non-empty when the CURRENT model is vision-capable (computed once in
            # build_context, see there), so this reconstructs the real image content on top of
            # that same placeholder text. Every other user message, and this one for any
            # non-vision-capable model, is completely unaffected.
            image = (image_by_message_id or {}).get(msg.id)
            if image:
                from core.providers.multimodal import build_multimodal_content  # noqa: PLC0415
                content = build_multimodal_content(msg.content or "", [image])
            else:
                content = msg.content or ""
            context.append({
                "role": "user",
                "content": content,
            })
            return

        # TOOL RESULT MESSAGE — drop entirely (synthesised into assistant response)
        if msg.role == "tool":
            return

        # ASSISTANT MESSAGE
        if msg.role == "assistant":
            calls = tool_calls_by_message.get(msg.id)

            if calls:
                # Tool-call turn: emit compact stub (B-067 format) instead of
                # full tool_calls block + raw result payloads.  This keeps the
                # model aware of what was called without blowing up the context.
                stub = _build_tool_stub(calls)
                context.append({
                    "role": "assistant",
                    "content": stub,
                })
            else:
                # Normal assistant text response — keep verbatim.
                context.append({
                    "role": "assistant",
                    "content": msg.content or "",
                })
    async def _image_attachments_for_vision(
        self,
        *,
        conversation_id: str,
        user_id: str,
        provider: str,
        model_name: str,
    ) -> Dict[str, Tuple[str, str]]:
        """
        {message_id: (mime_type, base64_data)} for every image attached to this conversation,
        or {} immediately if the current model isn't confirmed vision-capable -- the common
        case, and the only one that matters for cost: no extra query, no file read, no base64
        encoding happens otherwise.
        """
        from core.providers.multimodal import is_vision_capable, within_inline_limit  # noqa: PLC0415
        if not is_vision_capable(provider, model_name):
            return {}

        import asyncio  # noqa: PLC0415
        import base64  # noqa: PLC0415
        from core.db.repository import AttachmentRepo, ConversationAttachmentRepo  # noqa: PLC0415
        from utils.blob_store import read_file_blob  # noqa: PLC0415

        links = await ConversationAttachmentRepo.list_for_conversation(self.db, conversation_id)
        out: Dict[str, Tuple[str, str]] = {}
        for link in links:
            if link.attachment_type != "image" or not link.message_id or not link.attachment_id:
                continue
            att = await AttachmentRepo.get(self.db, link.attachment_id)
            if not att or not att.blob_path:
                continue
            try:
                raw_bytes = await asyncio.to_thread(
                    read_file_blob, user_id, att.blob_path, att.project_id,
                )
            except Exception:
                logger.warning(
                    "M-07: could not read attachment blob for a vision-capable model — "
                    "falling back to the placeholder text for this message",
                    exc_info=True,
                    extra={"conversation_id": conversation_id, "attachment_id": link.attachment_id},
                )
                continue
            if not raw_bytes or not within_inline_limit(len(raw_bytes)):
                continue
            out[link.message_id] = (att.mime_type or "application/octet-stream", base64.b64encode(raw_bytes).decode())
        return out

    async def build_context(
        self,
        conversation_id: str,
        system_prompt: str,
        model_name: str,
        provider: str,
        api_key: str,
        user_id: str,
        fallback_chain: Optional[List[dict]] = None,
    ) -> Tuple[List[dict], bool]:
        logger.debug(
            "Building conversation context",
            extra={
                "conversation_id": conversation_id,
                "user_id": user_id,
                "model": model_name,
            },
        )
        # Order priority (mirrors DBRepo.list_messages_for_conversation):
        #   1. Rows with sequence set come first (NULL-sequence rows last).
        #   2. sequence ASC — deterministic per-conversation position.
        #   3. created_at ASC — timestamp tiebreaker for NULL-sequence rows.
        #   4. rowid ASC — SQLite insertion-order final tiebreaker; handles
        #      same-second timestamps and batch-inserted messages correctly.
        #
        # DO NOT use order_by(asc(Message.id)) — UUID v4 IDs are random and
        # do not sort chronologically, causing the LLM to see messages out of
        # order and respond to the wrong user input.
        msg_stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(
                Message.sequence.is_(None),   # False (non-NULL) sorts before True (NULL)
                Message.sequence,
                Message.created_at,
                text("messages.rowid"),
            )
        )
        msg_result = await self.db.execute(msg_stmt)
        messages = msg_result.scalars().all()

        # M-07: for a confirmed vision-capable model only, reconstruct the real bytes of any
        # attached image so _append_message_with_tools can send it as real content instead of
        # just the placeholder note. Empty (and zero extra queries/file reads) for every other
        # model -- today, every model on this account.
        image_by_message_id = await self._image_attachments_for_vision(
            conversation_id=conversation_id, user_id=user_id, provider=provider, model_name=model_name,
        )
        logger.debug(
            "Loaded conversation messages",
            extra={
                "conversation_id": conversation_id,
                "message_count": len(messages),
            },
        )

        # --------------------------------------------------------------
        # 2. Load tool calls (grouped by message)
        # --------------------------------------------------------------
        tool_stmt = (
            select(ToolCall)
            .join(Message, ToolCall.message_id == Message.id)
            .where(Message.conversation_id == conversation_id)
        )
        tool_result = await self.db.execute(tool_stmt)
        tool_calls = tool_result.scalars().all()

        tool_calls_by_message: Dict[str, List[ToolCall]] = defaultdict(list)
        for tc in tool_calls:
            tool_calls_by_message[tc.message_id].append(tc)      

        memory_messages: List[MemoryMessage] = []
        total_tokens = TokenCounter.estimate(system_prompt, model_name)

        for m in messages:
            content = m.content or ""
            # Skip raw tool result messages for token counting — they are dropped
            # in the output (replaced by compact stubs), so their raw JSON content
            # size is irrelevant and can be enormous (e.g. web search results).
            # Including them inflates total_tokens to millions and triggers
            # summarisation even on short conversations.
            if m.role == "tool":
                # Still add to memory_messages so the summary boundary logic
                # (last_message_id check) can find all message IDs correctly,
                # but with zero token weight.
                memory_messages.append(
                    MemoryMessage(
                        message_id=m.id,
                        role=m.role,
                        content=content,
                        token_estimate=0,
                    )
                )
                continue
            tokens = TokenCounter.estimate(content, model_name)
            memory_messages.append(
                MemoryMessage(
                    message_id=m.id,
                    role=m.role,
                    content=content,
                    token_estimate=tokens,
                )
            )
            total_tokens += tokens

        overflow = (
            total_tokens >= self.policy.summary_trigger_tokens
            or total_tokens >= self.policy.max_context_tokens
            or len(memory_messages) >= self.policy.summary_trigger_message_count
        )

        # ------------------------------------------------------------------
        # 4. Start context with system prompt
        # ------------------------------------------------------------------
        context: List[dict] = []
        if system_prompt:
            context.append({"role": "system", "content": system_prompt})

        if not overflow:
            logger.debug(
                "Using full context (no summary needed)",
                extra={
                    "conversation_id": conversation_id,
                    "total_tokens": total_tokens,
                    "message_count": len(memory_messages),
                },
            )
            for msg in messages:
                self._append_message_with_tools(
                    context,
                    msg,
                    tool_calls_by_message,
                    image_by_message_id,
                )
            return context, False

        # ---- SUMMARY PATH ----
        logger.info(
            "Context overflow - creating summary",
            extra={
                "conversation_id": conversation_id,
                "total_tokens": total_tokens,
                "policy_max_tokens": self.policy.max_context_tokens,
                "message_count": len(memory_messages),
            },
        )
        summarizer = ConversationSummarizer(self.db)
        try:
            summary = await summarizer.summarize(
                conversation_id=conversation_id,
                user_id=user_id,
                messages=memory_messages,
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                fallback_chain=fallback_chain,
            )
        except Exception as exc:
            # V6.8: summarize() now retries + falls over across the user's
            # whole fallback_chain before ever raising (call_with_full_fallback),
            # so this is only reached when every candidate is down. Before,
            # this exception had no local handler and propagated all the way
            # up through worker.py's outer blanket catch, silently dropping
            # the user's entire turn (no reply, discovered only via the
            # execution timeout watchdog). Degrading to "no summary" instead
            # means the turn still gets a reply, using the raw recent-message
            # window selected below instead of a compressed summary of the
            # older messages that fall outside it.
            logger.warning(
                "Summarization failed after exhausting fallback_chain — "
                "degrading to raw recent-message context",
                exc_info=exc,
                extra={"conversation_id": conversation_id, "user_id": user_id},
            )
            summary = None

        used_tokens = TokenCounter.estimate(system_prompt, model_name)

        if summary:
            logger.info(
                "Summary created",
                extra={
                    "conversation_id": conversation_id,
                    "summary_length": len(summary.summary_text),
                    "last_summarized_message_id": summary.last_message_id,
                },
            )
            summary_content = (
                "Conversation summary so far:\n"
                + summary.summary_text
            )
            summary_tokens = TokenCounter.estimate(summary_content, model_name)

            context.append(
                {
                    "role": "system",
                    "content": summary_content,
                }
            )
            used_tokens += summary_tokens
        else:
            logger.debug(
                "No summary available",
                extra={"conversation_id": conversation_id},
            )

        # ------------------------------------------------------------------
        # 6. Select recent messages (token-aware, after summary boundary)
        # ------------------------------------------------------------------
        recent: List[MemoryMessage] = []
        recent_tokens = 0

        for m in reversed(memory_messages):
            if summary and m.message_id == summary.last_message_id:
                break

            if (
                used_tokens + recent_tokens + m.token_estimate
                > self.policy.max_context_tokens
                and len(recent) >= self.policy.min_recent_messages
            ):
                break

            recent.append(m)
            recent_tokens += m.token_estimate

        recent.reverse()
        logger.debug(
            "Selected recent messages",
            extra={
                "conversation_id": conversation_id,
                "recent_message_count": len(recent),
                "recent_tokens": recent_tokens,
                "total_context_tokens": used_tokens + recent_tokens,
            },
        )

        # Map flattened recent memory back to original messages
        msg_by_id = {m.id: m for m in messages}

        # Reconstruct recent messages into context
        for mem in recent:
            original_msg = msg_by_id.get(mem.message_id)
            if not original_msg:
                continue

            self._append_message_with_tools(
                context,
                original_msg,
                tool_calls_by_message,
                image_by_message_id,
            )

        logger.debug(
            "Context built successfully",
            extra={
                "conversation_id": conversation_id,
                "context_messages": len(context),
                "total_tokens": used_tokens + recent_tokens,
            },
        )
        return context, True
