# providers/usage.py
#
# Turning a provider's usage object into EVA's UsageChunk (V6.59 Phase 6).
#
# Every OpenAI-compatible provider (OpenAI, Groq, Ollama Cloud, anything routed
# through LiteLLM) reports usage in the same shape, and each handler used to
# read it by hand and got two things wrong:
#
#   * reasoning tokens were read as `usage.reasoning_tokens`, which does not
#     exist on these objects -- the real field is
#     `usage.completion_tokens_details.reasoning_tokens` -- so reasoning tokens
#     were always recorded as None for OpenAI, Groq and LiteLLM;
#   * cached prompt tokens (`usage.prompt_tokens_details.cached_tokens`, e.g. 68
#     of a 69-token prompt on Ollama Cloud) were never read at all, so cache
#     savings were invisible and cost was overstated.
#
# Convention used by UsageChunk everywhere: `prompt_tokens` is the TOTAL input
# including any cached portion; `cached_tokens` is the subset served from the
# provider's prompt cache. (LiteLLM's cost_per_token expects exactly this.)

from __future__ import annotations

from typing import Any, Optional, Tuple

from core.types import UsageChunk


def _get(obj: Any, name: str) -> Any:
    """getattr that also reads dict-shaped usage payloads."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def usage_details(u: Any) -> Tuple[Optional[int], Optional[int]]:
    """(reasoning_tokens, cached_tokens) from an OpenAI-shaped usage object."""
    reasoning = _as_int(_get(_get(u, "completion_tokens_details"), "reasoning_tokens"))
    if reasoning is None:
        reasoning = _as_int(_get(u, "reasoning_tokens"))   # a few providers put it at top level

    cached = _as_int(_get(_get(u, "prompt_tokens_details"), "cached_tokens"))
    if cached is None:
        # DeepSeek-native field, in case LiteLLM did not normalise it.
        cached = _as_int(_get(u, "prompt_cache_hit_tokens"))
    return reasoning, cached


def make_usage_chunk(u: Any, *, provider: str, model: str) -> UsageChunk:
    """UsageChunk from an OpenAI-shaped usage object."""
    reasoning, cached = usage_details(u)
    prompt = _as_int(_get(u, "prompt_tokens")) or 0
    completion = _as_int(_get(u, "completion_tokens")) or 0
    total = _as_int(_get(u, "total_tokens")) or (prompt + completion)
    return UsageChunk(
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        cached_tokens=cached,
        total_tokens=total,
        provider=provider,
        model=model,
    )


def make_anthropic_usage_chunk(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    provider: str,
    model: str,
) -> UsageChunk:
    """
    UsageChunk from Anthropic's usage block. Anthropic's `input_tokens` EXCLUDES
    the prompt-cache portions, so the total input is input + cache reads + cache
    writes; only the reads count as `cached_tokens` (a write is billed at a
    premium, not a discount).
    """
    prompt = (input_tokens or 0) + (cache_read_tokens or 0) + (cache_creation_tokens or 0)
    completion = output_tokens or 0
    return UsageChunk(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cache_read_tokens or 0,
        total_tokens=prompt + completion,
        provider=provider,
        model=model,
    )
