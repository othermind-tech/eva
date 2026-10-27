# core/security/pii_redactor.py
"""
S-03 — Offline, NLP-level PII redaction (Microsoft Presidio).

Wraps Presidio's AnalyzerEngine (detection) + AnonymizerEngine (replacement)
behind a single function, redact_pii(). Everything runs locally -- no network
call, no third-party API -- matching EVA's data-stays-on-device principle.

Why Presidio over an LLM call for this: the entities we care about (SSN,
credit card, email, phone, bank/passport/license numbers) have fixed,
checksummable shapes. A pattern+NER engine finds these deterministically and
faster than round-tripping through an LLM, and never sends the sensitive
text itself to a remote model just to ask it to find the sensitive parts.

Scoped entity allowlist -- NOT Presidio's full default set
────────────────────────────────────────────────────────────────────────────
Presidio also recognizes PERSON, LOCATION, DATE_TIME, NRP, URL, and
IP_ADDRESS out of the box. None of those are redacted by default here:
EVA needs a user's location/timezone for scheduling and environment context
(core/execution/worker.py's Environment Context block), needs dates/times
for daemon scheduling and wakeups, and needs person names for completely
ordinary assistant usage ("email John about the meeting"). Redacting any of
those would break real functionality far more than it protects privacy.
DEFAULT_PII_ENTITIES below is a deliberate subset: pure identifiers with
near-zero everyday conversational use and high leak risk if they reach a
log file or a third-party LLM provider's API.

The allowlist is overridable per call (and, via the PostToolUse hook config
in core/tools/hooks.py, per user) -- a stricter deployment can opt back into
PERSON or others, but the default stays usable for normal conversation.
"""

from __future__ import annotations

import logging
import threading
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Default entity allowlist ──────────────────────────────────────────────────
# Redacted by default: pure identifiers, essentially never needed for EVA to
# function, high leak risk (financial/government ID data).
DEFAULT_PII_ENTITIES: List[str] = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "IBAN_CODE",
    "US_BANK_NUMBER",
    "CRYPTO",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "MEDICAL_LICENSE",
]

# Never redacted by default -- documented here so the exclusion is a
# deliberate, visible decision, not an accidental omission.
#   PERSON      -- ordinary conversational use ("email John about...")
#   LOCATION    -- needed for scheduling / environment context
#   DATE_TIME   -- needed for daemon scheduling, wakeups, "remind me tomorrow"
#   NRP         -- nationality/religion/political-group; too broad a chill
#   URL         -- routine and frequently task-relevant
#   IP_ADDRESS  -- routine for technical/support tasks

# Matches below this confidence are dropped -- filters out Presidio's
# "very weak" (0.05) catch-all patterns (e.g. any bare 9-digit number is a
# 0.05 SSN candidate) while keeping "medium" (0.5) and higher matches, which
# is what a real SSN/bank-number-shaped string scores even without
# surrounding context words.
DEFAULT_SCORE_THRESHOLD = 0.35


class _PresidioEngines:
    """
    Lazily-initialised, process-wide singleton for the analyzer + anonymizer.

    Loading the spaCy NLP model has real startup cost (~5-10s) -- this must
    happen once per process, not once per redact_pii() call. Thread-safe via
    double-checked locking since multiple concurrent requests could race on
    first use.
    """

    _analyzer = None
    _anonymizer = None
    _lock = threading.Lock()

    @classmethod
    def get(cls):
        if cls._analyzer is None:
            with cls._lock:
                if cls._analyzer is None:
                    cls._analyzer, cls._anonymizer = cls._build()
        return cls._analyzer, cls._anonymizer

    @staticmethod
    def _build():
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        # Explicit model config -- without this, presidio-analyzer's default
        # AnalyzerEngine() falls back to "en_core_web_lg" (a ~400MB model)
        # and silently downloads it on first use. en_core_web_sm (~13MB,
        # already a pinned dependency -- see requirements.txt) is what EVA
        # actually ships; naming it explicitly avoids an unplanned,
        # unrelated multi-hundred-MB download the first time this runs.
        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
        provider = NlpEngineProvider(nlp_configuration=nlp_config)
        nlp_engine = provider.create_engine()

        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        anonymizer = AnonymizerEngine()
        logger.info("[PII] Presidio analyzer/anonymizer engines initialised (en_core_web_sm)")
        return analyzer, anonymizer


def redact_pii(
    text: str,
    entities: Optional[List[str]] = None,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> Tuple[str, int]:
    """
    Detect and redact PII in *text*, returning (redacted_text, count).

    Parameters
    ----------
    text            : Text to scan. Non-string or empty input is returned
                      unchanged with a count of 0 -- callers (hooks, chat
                      input wiring) don't need to guard this themselves.
    entities        : Entity types to look for. Defaults to
                      DEFAULT_PII_ENTITIES. Passing an explicit list (e.g.
                      from a hook's `config`) fully replaces the default --
                      it does not merge with it.
    score_threshold : Minimum confidence to redact a match. Default 0.35 --
                      see DEFAULT_SCORE_THRESHOLD.

    Returns
    -------
    (redacted_text, count) -- count is the number of entities redacted (0 if
    none found, or if Presidio fails to load -- see below).

    Failure mode: if the Presidio/spaCy engines fail to initialise (e.g. the
    model wheel wasn't installed), this logs a warning and returns the
    original text unredacted rather than raising -- a broken redactor must
    never crash the chat/tool-call path it's attached to. This is a
    fail-open design: it prioritizes availability over the redaction
    guarantee when the feature itself is broken, which is the same
    trade-off the existing redact_env_vars hook makes implicitly by only
    matching a fixed regex (it doesn't fail the tool call either).
    """
    if not isinstance(text, str) or not text:
        return text, 0

    entity_list = entities if entities is not None else DEFAULT_PII_ENTITIES

    try:
        analyzer, anonymizer = _PresidioEngines.get()
    except Exception as exc:
        logger.warning("[PII] Presidio engine initialisation failed -- redaction skipped: %s", exc)
        return text, 0

    try:
        results = analyzer.analyze(
            text=text,
            language="en",
            entities=entity_list,
            score_threshold=score_threshold,
        )
        if not results:
            return text, 0
        anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
        return anonymized.text, len(results)
    except Exception as exc:
        logger.warning("[PII] Redaction failed for this text -- returning original: %s", exc)
        return text, 0
