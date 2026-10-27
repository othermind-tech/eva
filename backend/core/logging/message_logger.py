# core/logging/message_logger.py

import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from core.db.repository import DBRepo
from typing import Optional, Dict, Any, Union


class MessageLogger:
    """
    Helper for saving the final aggregated message plus usage and tool info.
    It intentionally DOES NOT persist streaming chunks (by design).

    user_id is required for the real-time JSONL journal. Pass it from
    the execution context; defaults to "" which disables journal writes.
    """

    def __init__(self, db: AsyncSession, conversation_id: str, user_id: str = ""):
        self.db = db
        self.conversation_id = conversation_id
        self.user_id = user_id

    async def save_final_message(
        self,
        role: str,
        content: str,
        reasoning: Optional[Union[str, list]] = None,
        usage: Optional[Dict[str, Any]] = None,
        finish_reason: Optional[str] = None,
        chunked: bool = False,
        save_reasoning: bool = False,
    ):
        """
        Save aggregated final message and associated usage.
        usage: {"prompt_tokens":. "completion_tokens":. "reasoning_tokens":. "total_tokens":. "provider":. "model":.}

        By default we do NOT persist reasoning. Set save_reasoning=True to persist
        a joined reasoning string (useful for debugging).

        Tool-call steps are NOT saved through here -- they go through
        worker.py's _save_tool_step(), which runs mid-ReAct-loop (before this
        finalizer ever sees the turn) and writes both the tool_call/tool_result
        messages and their ToolCall rows directly.
        """
        # Decide whether to persist reasoning. Default: don't persist.
        reasoning_to_save = None
        if save_reasoning and reasoning:
            # Accept either a list of fragments or a single string
            if isinstance(reasoning, list):
                reasoning_to_save = " ".join([str(x) for x in reasoning]).strip()
            else:
                reasoning_to_save = str(reasoning)

            if reasoning_to_save == "":
                reasoning_to_save = None
        # Guard against empty assistant responses.
        # There are two distinct cases:
        #   1. finish_reason == "length": context overflow — the model ran out of
        #      output tokens before producing any text. Do NOT persist; an ErrorChunk
        #      was already yielded upstream. Persisting here would grow history and
        #      make the overflow worse on every retry.
        #   2. Any other empty response (timeout, provider error, bad parse, etc.):
        #      persist a visible placeholder so the preceding user message is
        #      "answered" in the DB.  Without this the next MemoryEngine build
        #      will include the unanswered user turn, causing the LLM to reply to
        #      the wrong input on the following turn.
        if role == "assistant" and not (content or "").strip():
            if finish_reason == "length":
                return None  # type: ignore[return-value]
            # Non-overflow empty response: replace with visible placeholder so the
            # conversation history stays consistent and the next turn starts clean.
            content = (
                "I wasn't able to generate a response for this request. "
                "Please try rephrasing or ask again."
            )

        msg = await DBRepo.add_message(
            db=self.db,
            conversation_id=self.conversation_id,
            role=role,
            content=content,
            reasoning=reasoning_to_save,
            mtype="text",
            finish_reason=finish_reason,
            chunked=chunked,
        )

        # Journal: append to real-time JSONL (sync, fire-and-forget, never raises)
        if self.user_id and self.conversation_id:
            from core.memory.conversation_journal import ConversationJournal
            ConversationJournal.log(self.user_id, self.conversation_id, {
                "type": "assistant_message",
                "message_id": msg.id,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "content": content or "",
                "finish_reason": finish_reason,
                "tokens": usage or {},
            })

        return msg
