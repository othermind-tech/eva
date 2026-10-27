# core/memory/provider_hybrid.py
"""
DB-backed memory provider — LocalBroker edition.

All memory reads come directly from SQLite via DBMemoryProvider.
The optional Redis acceleration cache has been removed along with
the Redis dependency.
"""
from typing import List, Dict, Optional
import logging

from core.memory.provider import DBMemoryProvider

logger = logging.getLogger(__name__)


class HybridMemoryProvider:
    """
    DB-backed memory provider.

    The `redis_ttl` parameter is accepted for API compatibility but ignored —
    there is no Redis cache in LocalBroker mode.
    """

    def __init__(
        self,
        *,
        db_provider: DBMemoryProvider,
        conversation_id: str,
        redis_ttl: Optional[int] = None,
    ):
        self.db_provider = db_provider
        self.conversation_id = conversation_id
        # redis_ttl accepted for API compatibility — ignored

    async def build_context(self) -> List[Dict]:
        return await self.db_provider.build_context()
