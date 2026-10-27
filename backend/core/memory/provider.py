# core/memory/provider.py

from typing import List, Dict, Optional
from logging import getLogger
from sqlalchemy.ext.asyncio import AsyncSession

from core.memory.engine import MemoryEngine

logger = getLogger(__name__)


class MemoryProvider:
    """
    Interface-like base for memory providers.
    """

    async def build_context(self) -> List[Dict]:
        raise NotImplementedError


class DBMemoryProvider(MemoryProvider):
    """
    DB-backed memory provider using MemoryEngine.
    """

    def __init__(
        self,
        db: AsyncSession,
        conversation_id: str,
        user_id: str,
        model_name: str,
        provider: str,
        api_key: str,
        system_prompt: str,
        fallback_chain: Optional[List[Dict]] = None,
    ):
        self.engine = MemoryEngine(db)
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.model_name = model_name
        self.provider = provider
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.fallback_chain = fallback_chain

    async def build_context(self) -> List[Dict]:
        logger.debug(
            "DBMemoryProvider building context",
            extra={
                "conversation_id": self.conversation_id,
                "user_id": self.user_id,
            },
        )
        context = await self.engine.build_context(
            conversation_id=self.conversation_id,
            system_prompt=self.system_prompt,
            model_name=self.model_name,
            provider=self.provider,
            api_key=self.api_key,
            user_id=self.user_id,
            fallback_chain=self.fallback_chain,
        )
        logger.debug(
            "DBMemoryProvider context ready",
            extra={
                "conversation_id": self.conversation_id,
                "context_length": len(context),
            },
        )
        return context
