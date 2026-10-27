from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.types import MemoryMessage
from core.memory.token_counter import TokenCounter
from core.db.models import ConversationSummary
from core.tools.user_registry import UserToolRegistry
from core.providers.fallback_call import call_with_full_fallback
from prompts.memory import SUMMARY_SYSTEM_PROMPT, SUMMARY_UPDATE_SYSTEM_PROMPT


class ConversationSummarizer:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _build_prompt(self, messages: List[MemoryMessage]) -> List[dict]:
        content = "\n".join(
            f"{m.role.upper()}: {m.content}"
            for m in messages
            # Skip raw tool result messages — they contain full JSON payloads
            # that can be enormous and will cause 413 errors when sent to the
            # LLM for summarisation.  Tool context is visible through the
            # preceding assistant stub (⚙ tool_name(args)) and its response.
            if m.content and m.role != "tool"
        )

        return [
            {"role": "user", "content": content},   
        ]

    async def summarize(
        self,
        user_id: str,
        conversation_id: str,
        messages: List[MemoryMessage],
        model_name: str,
        provider: str,
        api_key: str,
        fallback_chain: Optional[List[dict]] = None,
    ) -> ConversationSummary:
        stmt = select(ConversationSummary).where(
            ConversationSummary.conversation_id == conversation_id
        )
        result = await self.db.execute(stmt)
        summary = result.scalar_one_or_none()

        if summary:
            idx = next(
                (i for i, m in enumerate(messages) if m.message_id == summary.last_message_id),
                None,
            )
            new_messages = messages[idx + 1 :] if idx is not None else messages
        else:
            new_messages = messages

        if not new_messages:
            return summary

        prompt_messages = self._build_prompt(new_messages)

        # Choose prompt based on whether a summary already exists.
        # - Initial: comprehensive first-time summary of all messages.
        # - Update: LLM receives the existing summary + new messages and produces
        #   ONE integrated document (not an append). This keeps the summary coherent
        #   across multiple summarization passes within a long conversation.
        if summary:
            system_prompt = SUMMARY_UPDATE_SYSTEM_PROMPT.format(
                existing_summary=summary.summary_text
            )
        else:
            system_prompt = SUMMARY_SYSTEM_PROMPT

        # Reactive Loop tier (V6.8): a failure here isn't cosmetic -- it's
        # only reached on context overflow, and core/memory/engine.py has
        # no degrade path for a raised exception (only for "summary is
        # None"), so an unrecovered failure drops the user's entire turn
        # silently (see Notes discussion / build_context's hard dependency
        # on this succeeding). Retries the same candidate with backoff on
        # Tier A errors, then walks fallback_chain, before ever raising.
        summary_text = await call_with_full_fallback(
            session={
                "provider": provider,
                "model": model_name,
                "api_key": api_key,
                "fallback_chain": fallback_chain or [],
            },
            system_prompt=system_prompt,
            messages=prompt_messages,
            user_id=user_id,
            tools=UserToolRegistry(default_tools=False, enabled_tools=[]),
            caller_tag="ConversationSummarizer",
        )
        token_estimate = TokenCounter.estimate(summary_text, model_name)

        if summary:
            # Replace with the fully integrated summary — not a raw append.
            # The update prompt instructs the LLM to produce the complete merged document.
            summary.summary_text = summary_text
            summary.last_message_id = new_messages[-1].message_id
            summary.token_estimate = token_estimate
        else:
            summary = ConversationSummary(
                user_id=user_id,
                conversation_id=conversation_id,
                summary_text=summary_text,
                last_message_id=new_messages[-1].message_id,
                token_estimate=token_estimate,
            )
            self.db.add(summary)
        # Commit summary immediately to ensure memory consistency
        await self.db.commit()
        return summary
