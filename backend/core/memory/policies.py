from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MemoryPolicy:
    # Hard limit for total context sent to the model
    max_context_tokens: int

    # Trigger summarization when memory grows too large
    summary_trigger_tokens: int

    # Failsafe trigger (non-token-based, defensive)
    summary_trigger_message_count: int

    # Minimum number of recent messages to always include verbatim
    min_recent_messages: int

    # Whether tool results are allowed in memory
    include_tool_results: bool = True


# Fallback used when context_window is unknown (e.g. Ollama, unregistered models).
# Conservative: 120k max, trigger at 84k (~70%).
DEFAULT_MEMORY_POLICY = MemoryPolicy(
    max_context_tokens=120_000,
    summary_trigger_tokens=84_000,
    summary_trigger_message_count=50,
    min_recent_messages=6,
    include_tool_results=True,
)


def policy_for(context_window: Optional[int]) -> MemoryPolicy:
    """
    Return a MemoryPolicy derived from the model's actual context window.

    Thresholds are proportional — no hardcoded per-provider values:
      - usable     = context_window * 0.75   (reserve 25% for output + system overhead)
      - max        = usable
      - trigger    = usable * 0.80           (summarise when 80% of usable is consumed)

    Falls back to DEFAULT_MEMORY_POLICY when context_window is None
    (Ollama, models that don't report their window).

    Examples:
      context_window=32_768  (Groq 32k) → max=24k,  trigger=~19k
      context_window=131_072 (128k)     → max=98k,  trigger=~79k
      context_window=200_000 (Claude)   → max=150k, trigger=120k
    """
    if not context_window:
        return DEFAULT_MEMORY_POLICY

    usable  = int(context_window * 0.75)
    trigger = int(usable * 0.80)

    return MemoryPolicy(
        max_context_tokens=usable,
        summary_trigger_tokens=trigger,
        summary_trigger_message_count=50,   # defensive failsafe regardless of window size
        min_recent_messages=6,
        include_tool_results=True,
    )
