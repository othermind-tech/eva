# core/broker/shadow_buffer.py
"""
In-memory, asyncio-native replacement for RedisStreamShadow.

Key differences from the Redis version:
- Zero polling delay: replay_from() uses asyncio.Event instead of a
  fixed 100 ms sleep, so chunks are delivered within microseconds of
  being appended.
- No network overhead: all reads/writes are local list operations.
- close() signals all waiting consumers to stop cleanly.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)


class LocalShadowBuffer:
    """
    Per-execution in-memory stream buffer.

    append() is called by the runner; replay_from() is consumed by
    api/eva_response.py (SSE generator) or any other read-side.

    Thread-safety: asyncio single-threaded — no locks needed.
    """

    def __init__(self) -> None:
        self._chunks: list[dict] = []
        self._event: asyncio.Event = asyncio.Event()
        self._closed: bool = False

    # ------------------------------------------------------------------
    # Write side
    # ------------------------------------------------------------------

    async def append(self, payload: dict) -> None:
        """Append a chunk and immediately wake any waiting replay_from() consumer."""
        self._chunks.append(payload)
        self._event.set()

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    async def replay_from(
        self,
        start_index: int = 0,
        poll_interval: float = 0.1,   # kept for API compatibility — not used
    ) -> AsyncGenerator[dict, None]:
        """
        Yield all existing chunks starting at start_index, then continue
        yielding new ones as they arrive, until close() is called.

        poll_interval is accepted for drop-in compatibility with
        RedisStreamShadow.replay_from() but is not used — delivery
        is event-driven with zero sleep.
        """
        index = start_index

        while not self._closed:
            length = len(self._chunks)
            if index < length:
                for chunk in self._chunks[index:length]:
                    yield chunk
                index = length
            else:
                # Clear then wait — avoids a race where a new chunk arrives
                # between the length check and the wait() call:
                #   1. clear() marks event as not-set
                #   2. If append() fires NOW it sets the event again → wait() returns immediately ✅
                #   3. If no append() fires, wait() blocks until one does ✅
                self._event.clear()
                if self._closed:
                    break
                await self._event.wait()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def get_length(self) -> int:
        return len(self._chunks)

    async def clear(self) -> None:
        self._chunks.clear()
        self._event.clear()

    def close(self) -> None:
        """Signal all replay_from() consumers to stop."""
        self._closed = True
        self._event.set()   # unblock any waiting consumer so it can see _closed=True

    # ------------------------------------------------------------------
    # Non-blocking snapshot helpers (used by SSE generator)
    # ------------------------------------------------------------------

    def chunks_from(self, start: int) -> list:
        """
        Return a shallow copy of all chunks from index ``start`` onwards.
        Non-blocking — safe to call at any time.
        """
        return list(self._chunks[start:])

    def is_closed(self) -> bool:
        """True once the runner has called close()."""
        return self._closed

    async def wait_for_more(self, min_count: int, timeout: float = 5.0) -> None:
        """
        Wait until chunk count exceeds ``min_count``, the buffer is closed,
        or ``timeout`` seconds have elapsed — whichever comes first.

        Race-safe: clears the event then re-checks count before sleeping,
        so a chunk appended between the caller's last check and this call
        is never missed (append sets the event; we see it immediately).
        """
        if self._closed or len(self._chunks) > min_count:
            return
        self._event.clear()
        # Re-check after clear in case append() fired between our caller's
        # length check and this clear().
        if self._closed or len(self._chunks) > min_count:
            return
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
