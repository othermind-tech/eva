# core/execution/intent_router.py

"""
3-Layer Intent Router — classifies an incoming user message as either
CONVERSATIONAL (simple Q&A, chit-chat, factual lookup) or PLAN_EXECUTE
(tasks that benefit from the Plan → Execute → Verify loop).

Layer 1  — Heuristic pre-filter (zero tokens, zero latency).
           Catches the overwhelming majority of clearly conversational or
           clearly task-oriented messages using keyword / length signals.

Layer 2  — LLM classifier (~150-200 tokens).
           Fires only when Layer 1 returns None (ambiguous message).
           Uses the user's own configured provider so the call is cheap
           and stays within their existing rate limits.

Layer 3  — Session agent_mode override (checked first, before any work).
           "chat"         → always CONVERSATIONAL
           "plan_execute" → always PLAN_EXECUTE
           "auto"         → proceed through Layers 1 + 2
"""

import logging
import re
from enum import Enum
from typing import Optional

from config.settings import INTENT_CLASSIFIER_MAX_TOKENS, INTENT_CLASSIFIER_TEMPERATURE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public Enums
# ---------------------------------------------------------------------------

class ExecutionMode(str, Enum):
    """
    The routing decision returned by IntentRouter.

    CONVERSATIONAL – handled by a single LLM call (current AgentEngine path).
    PLAN_EXECUTE   – will be routed through the Plan→Execute→Verify loop
                     once that module is built.  For now both modes use the
                     same AgentEngine path; the value is stored in the broker and
                     logged so the fork can be added without touching routing
                     logic again.
    """
    CONVERSATIONAL = "conversational"
    PLAN_EXECUTE   = "plan_execute"


class AgentMode(str, Enum):
    """
    User-level preference stored in UserPreferences and the in-memory session.

    AUTO         – IntentRouter decides dynamically (Layers 1 + 2).
    CHAT         – Always treat messages as CONVERSATIONAL.
    PLAN_EXECUTE – Always treat messages as PLAN_EXECUTE.
    """
    AUTO         = "auto"
    CHAT         = "chat"
    PLAN_EXECUTE = "plan_execute"


# ---------------------------------------------------------------------------
# Layer 1 — Heuristic patterns
# ---------------------------------------------------------------------------

# Messages matching ANY of these patterns are immediately classified as
# CONVERSATIONAL.  Ordered from most- to least-specific.
_CONVERSATIONAL_PATTERNS = [
    # Greetings / pleasantries
    re.compile(
        r"^(hi|hey|hello|howdy|yo|good\s+(morning|afternoon|evening|night))[!?.,:;\s]*$",
        re.IGNORECASE,
    ),
    # Simple social / wellbeing queries
    re.compile(
        r"^how\s+(are\s+you|r\s+u|have\s+you\s+been|is\s+it\s+going)[?!.\s]*$",
        re.IGNORECASE,
    ),
    # Thank-you / acknowledgements
    re.compile(
        r"^(thanks?|thank\s+you|cheers|perfect|great|awesome|cool|ok(ay)?|got\s+it|sounds?\s+good)[!?.\s]*$",
        re.IGNORECASE,
    ),
    # "What is X?" / "Who is X?" — factual one-liners
    re.compile(
        r"^(what|who|where|when|why|how)\s+(is|are|was|were|do|does|did)\s+\w",
        re.IGNORECASE,
    ),
    # "Can you explain …" / "Tell me about …"
    re.compile(
        r"^(can\s+you\s+explain|tell\s+me\s+(about|more)|explain\s+|describe\s+)",
        re.IGNORECASE,
    ),
]

# Messages matching ANY of these patterns are immediately classified as
# PLAN_EXECUTE.
_PLAN_EXECUTE_PATTERNS = [
    # File / code creation
    re.compile(
        r"\b(create|generate|write|build|make|scaffold|implement|develop|code|program)\b"
        r".*\b(file|script|function|class|module|api|app|application|service|server|"
        r"test|report|document|presentation|spreadsheet|database|schema|migration)\b",
        re.IGNORECASE,
    ),
    # Git operations
    re.compile(
        r"\b(git\s+(add|commit|push|pull|merge|checkout|branch|diff|log|status)|"
        r"commit\s+(all|the\s+changes?|my\s+work)|push\s+(to\s+(origin|remote)|the\s+changes?))\b",
        re.IGNORECASE,
    ),
    # File-system operations
    re.compile(
        r"\b(read|open|edit|update|modify|delete|remove|rename|move|copy)\b"
        r".*\b(file|folder|directory|dir)\b",
        re.IGNORECASE,
    ),
    # Multi-step imperatives with "and then", "then", "after that"
    re.compile(
        r"\b(and\s+then|then\s+(do|run|execute|make|create|save)|"
        r"after\s+that|finally\s+(save|create|run))\b",
        re.IGNORECASE,
    ),
    # Explicit research / analysis tasks
    re.compile(
        r"\b(research|analyse|analyze|summarize|summarise|compare|"
        r"benchmark|evaluate|audit|review)\b.*\b(and\s+(write|save|create|output)|"
        r"report|document|results?|findings?)\b",
        re.IGNORECASE,
    ),
    # Deployment / infrastructure
    re.compile(
        r"\b(deploy|install|setup|configure|set\s+up|run|start|stop|restart)\b"
        r".*\b(server|service|container|docker|kubernetes|k8s|app|application)\b",
        re.IGNORECASE,
    ),
    # Web / HTTP requests
    re.compile(
        r"\b(fetch|download|scrape|crawl|call|hit|request)\b"
        r".*\b(url|endpoint|api|website|page|http)\b",
        re.IGNORECASE,
    ),
]

# Short messages (≤ this many tokens) are likely conversational when no
# strong task signal is present.
_SHORT_MESSAGE_THRESHOLD = 12  # approximate word count


def _heuristic_classify(message: str) -> Optional[ExecutionMode]:
    """
    Layer 1: pure-regex, zero-latency classification.

    Returns:
        ExecutionMode.CONVERSATIONAL  — clearly conversational
        ExecutionMode.PLAN_EXECUTE    — clearly a task
        None                          — ambiguous; escalate to Layer 2
    """
    stripped = message.strip()

    # Check conversational patterns first (faster; most messages are chat).
    for pat in _CONVERSATIONAL_PATTERNS:
        if pat.search(stripped):
            logger.debug(
                "[IntentRouter] Layer1 → CONVERSATIONAL (regex match)",
                extra={"pattern": pat.pattern[:60]},
            )
            return ExecutionMode.CONVERSATIONAL

    # Check task patterns.
    for pat in _PLAN_EXECUTE_PATTERNS:
        if pat.search(stripped):
            logger.debug(
                "[IntentRouter] Layer1 → PLAN_EXECUTE (regex match)",
                extra={"pattern": pat.pattern[:60]},
            )
            return ExecutionMode.PLAN_EXECUTE

    # Short messages with no task signal default to conversational.
    word_count = len(stripped.split())
    if word_count <= _SHORT_MESSAGE_THRESHOLD:
        logger.debug(
            "[IntentRouter] Layer1 → CONVERSATIONAL (short message, no task signal)",
            extra={"word_count": word_count},
        )
        return ExecutionMode.CONVERSATIONAL

    # Ambiguous — escalate.
    return None


# ---------------------------------------------------------------------------
# Layer 2 — LLM Classifier
# ---------------------------------------------------------------------------

_CLASSIFIER_SYSTEM_PROMPT = """You are a message intent classifier for an AI assistant backend.

Classify the user message as exactly one of:
  CONVERSATIONAL — simple questions, chit-chat, greetings, factual lookups,
                   "explain X", "what is Y" — no file I/O or multi-step work.
  PLAN_EXECUTE   — any task requiring file creation/editing, code generation,
                   git operations, web requests, deployments, or multi-step
                   execution workflows.

Do not deliberate or explain your reasoning. Answer immediately with your
first instinct. This is a simple lookup, not a problem to solve.
Reply with ONLY the single word: CONVERSATIONAL or PLAN_EXECUTE
No explanation. No punctuation. No other text."""

_CLASSIFIER_MAX_TOKENS = INTENT_CLASSIFIER_MAX_TOKENS
_CLASSIFIER_TEMPERATURE = INTENT_CLASSIFIER_TEMPERATURE


async def _llm_classify(message: str, user_id: str) -> ExecutionMode:
    """
    Layer 2: lightweight LLM call using the user's configured provider,
    with fallback_chain support (core/providers/fallback_call.py, V6.5
    Phase 3), routed through EVA's standard provider-handler system
    underneath.

    Falls back to CONVERSATIONAL on any error so routing is never a hard
    blocker.
    """
    try:
        # Lazy imports to avoid circular imports at module load time.
        from config.settings import get_user_session
        from core.providers.fallback_call import call_llm_with_fallback

        session  = get_user_session(user_id)
        provider = session.get("provider")
        model    = session.get("model")

        # No hardcoded provider/model fallback (V6.4): EVA has no default
        # model of its own -- a genuinely unconfigured session degrades to
        # the same safe default (CONVERSATIONAL) as any other classifier
        # failure, rather than guessing a provider/model to call.
        if not provider or not model:
            logger.warning(
                "[IntentRouter] No provider/model configured for user — "
                "defaulting to CONVERSATIONAL",
                extra={"user_id": user_id},
            )
            return ExecutionMode.CONVERSATIONAL

        raw_text = await call_llm_with_fallback(
            session=session,
            system_prompt=_CLASSIFIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message}],
            max_tokens=_CLASSIFIER_MAX_TOKENS,
            temperature=_CLASSIFIER_TEMPERATURE,
            user_id=user_id,
            caller_tag="IntentRouter._llm_classify",
        )

        raw = raw_text.strip().upper()

        if "PLAN" in raw:
            logger.debug(
                "[IntentRouter] Layer2 → PLAN_EXECUTE (LLM)",
                extra={"provider": provider, "model": model, "raw": raw},
            )
            return ExecutionMode.PLAN_EXECUTE

        logger.debug(
            "[IntentRouter] Layer2 → CONVERSATIONAL (LLM)",
            extra={"provider": provider, "model": model, "raw": raw},
        )
        return ExecutionMode.CONVERSATIONAL

    except Exception as exc:
        logger.warning(
            "[IntentRouter] Layer2 LLM classification failed — defaulting to CONVERSATIONAL",
            exc_info=exc,
        )
        return ExecutionMode.CONVERSATIONAL


# ---------------------------------------------------------------------------
# Public API — IntentRouter
# ---------------------------------------------------------------------------

class IntentRouter:
    """
    Stateless intent router.  All state lives in the caller's session store.

    Usage::

        router = IntentRouter()
        mode = await router.route(message, user_id=user_id)
        # mode is ExecutionMode.CONVERSATIONAL or ExecutionMode.PLAN_EXECUTE
    """

    async def route(
        self,
        message: str,
        user_id: str,
    ) -> ExecutionMode:
        """
        Classify *message* and return an ExecutionMode.

        Evaluation order:
          1. Layer 3 — session agent_mode override (wins immediately)
          2. Layer 1 — heuristic regex  (zero cost)
          3. Layer 2 — LLM classifier   (only when Layer 1 is ambiguous)
        """
        from config.settings import get_user_session

        # ----------------------------------------------------------------
        # Layer 3: session override
        # ----------------------------------------------------------------
        session = get_user_session(user_id)
        agent_mode = session.get("agent_mode", AgentMode.AUTO.value)

        if agent_mode == AgentMode.CHAT.value:
            logger.debug(
                "[IntentRouter] Layer3 override → CONVERSATIONAL",
                extra={"user_id": user_id, "agent_mode": agent_mode},
            )
            return ExecutionMode.CONVERSATIONAL

        if agent_mode == AgentMode.PLAN_EXECUTE.value:
            logger.debug(
                "[IntentRouter] Layer3 override → PLAN_EXECUTE",
                extra={"user_id": user_id, "agent_mode": agent_mode},
            )
            return ExecutionMode.PLAN_EXECUTE

        # ----------------------------------------------------------------
        # Layer 1: heuristic pre-filter
        # ----------------------------------------------------------------
        heuristic_result = _heuristic_classify(message)
        if heuristic_result is not None:
            return heuristic_result

        # ----------------------------------------------------------------
        # Layer 2: LLM classifier (only for genuinely ambiguous messages)
        # ----------------------------------------------------------------
        logger.debug(
            "[IntentRouter] Layer1 ambiguous — escalating to LLM classifier",
            extra={"user_id": user_id, "message_length": len(message)},
        )
        return await _llm_classify(message, user_id)


# ---------------------------------------------------------------------------
# Module-level singleton (imported by api/chat.py)
# ---------------------------------------------------------------------------
intent_router = IntentRouter()
