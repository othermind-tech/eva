# core/stream_buffer.py

from typing import AsyncGenerator, List, Optional, Dict, Any
from core.types import (
    StreamChunk, TextChunk, UsageChunk,
    ReasoningChunk, ErrorChunk, StepProgressChunk,
    ToolCallChunk, ToolResultChunk, StatusChunk,
    ProviderFallbackChunk,
)


def _add_usage(total: Dict[str, Any], chunk: UsageChunk) -> None:
    """Accumulate one UsageChunk into `total` (provider/model: the last call's)."""
    for field in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "total_tokens"):
        value = getattr(chunk, field, None)
        if value is not None or field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total[field] = (total.get(field) or 0) + (value or 0)
    total["provider"] = chunk.provider
    total["model"] = chunk.model
    total["calls"] = total.get("calls", 0) + 1


class BufferedStream:
    """
    Wraps a StreamChunk async generator.
    Allows:
      - streaming chunks to UI as they come
      - buffering internally all chunks (text, usage, reasoning, error)
      - producing a final aggregated result after stream ends
    """
    def __init__(self, source: AsyncGenerator[StreamChunk, None]):
        self._source = source
        self._buffer: List[StreamChunk] = []
        self._ended = False
        self._closed = False

    async def stream_and_buffer(self) -> AsyncGenerator[StreamChunk, None]:
        """
        Yields streamable chunks:
          - TextChunks (always)
          - ReasoningChunks (if emitted by handler)
          - StepProgressChunks (PEV mode: structured per-step progress events)
        Buffers all chunks regardless of type.
        """
        try:
            async for chunk in self._source:
                self._buffer.append(chunk)
                # only TEXT and REASONING are forwarded live
                if isinstance(chunk, TextChunk) and chunk.content:
                    yield chunk
                # live-stream reasoning ONLY if handlers produce it
                elif isinstance(chunk, ReasoningChunk):
                    yield chunk
                # PEV: forward step-progress events so the LocalShadowBuffer
                # captures them and the SSE endpoint can replay them as
                # `event: step_progress` frames.
                elif isinstance(chunk, StepProgressChunk):
                    yield chunk
                # Tool calls and their results must also reach the shadow buffer
                # so that the trace endpoint can surface them even after the
                # execution reaches a terminal state (the DB finalizer does not
                # persist tool_call=None placeholder).
                elif isinstance(chunk, (ToolCallChunk, ToolResultChunk)):
                    yield chunk
                # ErrorChunks must be forwarded so the SSE endpoint can surface
                # them to the frontend instead of showing an empty response.
                elif isinstance(chunk, ErrorChunk):
                    yield chunk
                # UsageChunks must be forwarded so the SSE endpoint emits a
                # `event: usage` frame — providers emit this at end of stream
                # (or per-chunk for OpenAI/Groq/LiteLLM).
                elif isinstance(chunk, UsageChunk):
                    yield chunk
                # StatusChunks are pre-stream system notifications (e.g.
                # "Summarising conversation…").  Forward immediately so the
                # SSE endpoint emits `event: status` before any text arrives.
                elif isinstance(chunk, StatusChunk):
                    yield chunk
                # V6.5 Phase 5: ProviderFallbackChunks must reach the SSE
                # endpoint live (`event: provider_fallback`) so the frontend
                # can notify the user mid-execution, not just after the fact
                # via GET /executions/{id}/metrics' fallback_details.
                elif isinstance(chunk, ProviderFallbackChunk):
                    yield chunk
        finally:
            self._ended = True
    async def replay(self):
        """
        Replay already-buffered chunks for late subscribers.
        Does NOT consume the source generator.
        """
        for chunk in self._buffer:
            if isinstance(chunk, (TextChunk, ReasoningChunk)):
                yield chunk
    async def aggregated(self, *, require_ended: bool = True) -> Dict[str, Any]:
        """
        Produces a final result object after stream ends.
        Includes:
          - full text
          - reasoning list (if any)
          - usage object
          - final finish_reason
          - error object (if any)

        require_ended=False (V6.15.3): produces a best-effort snapshot from
        whatever chunks have been buffered so far, without requiring the
        source stream to have been fully consumed. Used when cancellation
        cuts a run short -- the async-for loop iterating stream_and_buffer()
        is abandoned via `return`, not naturally exhausted, so self._ended
        never becomes True. Without this, any usage/text accumulated before
        cancellation was previously silently discarded.
        """
        if require_ended and not self._ended:
            raise RuntimeError("Stream has not ended yet.")
        
        full_text = ""
        reasoning_list = []
        usage_obj = None
        usage_total: Dict[str, Any] = {}
        finish_reason = None
        error_obj = None

        for chunk in self._buffer:

            if isinstance(chunk, ErrorChunk):
                error_obj = chunk

            elif isinstance(chunk, TextChunk):
                if chunk.content:
                    full_text += chunk.content
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

            elif isinstance(chunk, ReasoningChunk):
                reasoning_list.append(chunk.content)

            elif isinstance(chunk, UsageChunk):
                usage_obj = chunk
                _add_usage(usage_total, chunk)
        # Convert reasoning list -> single string (avoid saving lists into DB)
        reasoning_str = None
        if reasoning_list:
            # join using space — preserves tokens but makes a readable string
            reasoning_str = " ".join(reasoning_list).strip()
            if reasoning_str == "":
                reasoning_str = None
        # Return aggregated result
        return {
            "text": full_text,
            "reasoning": reasoning_str,
            # The LAST model call's usage: what the final message cost (kept
            # as before -- it is stored on the assistant message).
            "usage": usage_obj.__dict__ if usage_obj else None,
            # Usage summed over EVERY model call in this execution (each ReAct
            # step, each fallback attempt that reported usage). This -- not the
            # last call -- is what the execution's metrics and cost must use;
            # recording only the last call undercounted any multi-step run.
            "usage_total": usage_total or None,
            "finish_reason": finish_reason,
            "error": error_obj.__dict__ if error_obj else None,
        }
    
    def clear(self):
        """
        Clear the buffer.
        """
        self._buffer.clear()
        self._closed = True
    
    @property
    def is_closed(self) -> bool:
        """
        Whether the buffer is closed.
        """
        return self._closed
    