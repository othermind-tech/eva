# core/api.py

from typing import AsyncGenerator, Protocol, List, Dict
from core.types import StreamChunk

ApiStream = AsyncGenerator[StreamChunk, None]


class ApiHandler(Protocol):
    """
    Standard interface all provider handlers must implement.
    Output MUST be AsyncGenerator[StreamChunk].
    """
    async def create_message(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        stream: bool = True
    ) -> ApiStream:
        ...
