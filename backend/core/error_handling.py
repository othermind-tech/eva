from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

# Tier A retry counts are eva.env-overridable (pydantic Settings class, not
# a bare os.getenv() constant -- see config/settings.py's note on why).
# Defensive fallback matches the Field() defaults on Settings exactly, in
# case settings is ever unavailable this early in import order.
try:
    from config.settings import settings as _settings
    _MAX_RETRIES_RATE_LIMIT    = _settings.FALLBACK_MAX_RETRIES_RATE_LIMIT
    _MAX_RETRIES_TIMEOUT       = _settings.FALLBACK_MAX_RETRIES_TIMEOUT
    _MAX_RETRIES_PROVIDER_DOWN = _settings.FALLBACK_MAX_RETRIES_PROVIDER_DOWN
except Exception:
    _MAX_RETRIES_RATE_LIMIT    = 2
    _MAX_RETRIES_TIMEOUT       = 1
    _MAX_RETRIES_PROVIDER_DOWN = 2


class ErrorType(Enum):
    RATE_LIMIT    = "rate_limit"
    TIMEOUT       = "timeout"
    AUTH          = "auth"
    PROVIDER_DOWN = "provider_down"
    INVALID_MODEL = "invalid_model"    # 404 / model-not-found class of errors
    CONTENT_POLICY = "content_policy"  # moderation / safety-filter refusal
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"  # V6.11
    INVALID_REQUEST = "invalid_request"
    INTERNAL      = "internal"
    UNKNOWN       = "unknown"


def _classify_structured_error(exc: Exception) -> Optional[ErrorType]:
    """
    Provider-SDK-aware classification, tried before the generic string
    heuristic below.

    Researched each handler's actual SDK rather than guessing:
      - openai (openai_handler.py, ollama_cloud_handler.py) and groq
        (groq_handler.py) are both Stainless-generated clients with an
        essentially identical exception hierarchy: every 4xx/5xx response
        raises a subclass of APIStatusError carrying `.status_code`
        (openai/groq: 400/401/403/404/409/422/429/500+); openai (and thus
        ollama_cloud, which proxies through openai's SDK) additionally
        parses the response body into `.type` / `.code` attributes.
      - anthropic (anthropic_handler.py) follows the same Stainless shape
        (`.status_code`, plus `.type` populated from the documented
        invalid_request_error / authentication_error / billing_error /
        permission_error / not_found_error / rate_limit_error / api_error /
        timeout_error / overloaded_error body values) but adds its own
        409/413/429/529 codes not present in openai/groq.
      - litellm (litellm_handler.py) normalises across providers into its
        *own* exception classes, deliberately richer than any raw SDK:
        distinct ContentPolicyViolationError / ContextWindowExceededError
        classes exist specifically so callers don't have to string-sniff a
        generic 400 to find those cases -- checked by class name here.
      - ollama-python's ResponseError (ollama_handler.py, local/self-hosted)
        is a different SDK entirely, but happens to carry the same
        `.status_code` attribute name (docs.ollama.com/api/errors: 400/404/
        429/500/502), with no `.type` field.

    In short: five different SDKs, but four of them expose enough of a
    shared `.status_code` shape that one duck-typed function handles all
    of them correctly without importing any provider SDK into this module.
    Returns None (never guesses) when `exc` doesn't carry a usable
    `.status_code` -- callers fall back to the string heuristic below,
    which still covers connection-level errors and anything unstructured.
    """
    cls_name = type(exc).__name__

    # litellm's own, more specific classes -- must be checked before the
    # generic status_code=400 bucket below, since both content-policy and
    # context-window violations surface as plain BadRequestError (400) on
    # every underlying provider SDK; litellm's whole point is that callers
    # shouldn't have to re-derive that distinction themselves.
    if cls_name == "ContentPolicyViolationError":
        return ErrorType.CONTENT_POLICY

    # litellm's ContextWindowExceededError (V6.11) -- same reasoning as
    # ContentPolicyViolationError above: both fold into a generic 400 on
    # every underlying provider SDK, so the specific class must be checked
    # before the status_code=400 bucket below ever gets a chance to
    # misclassify it as plain INVALID_REQUEST (which is non-retried,
    # non-fallback-eligible -- exactly the silent-dead-end this was hitting
    # before this fix; see execution 9e029fc2).
    if cls_name == "ContextWindowExceededError":
        return ErrorType.CONTEXT_LENGTH_EXCEEDED

    # Connection-level failures never get a `.status_code` (no HTTP
    # response was ever received) -- openai/anthropic/groq all name these
    # identically; litellm's Timeout class does not subclass its own
    # APITimeoutError-alike, so it's checked by name too. httpx.ReadError
    # (and other httpx transport-level exceptions that occasionally
    # surface unwrapped through a handler, e.g. a mid-stream connection
    # drop to Ollama Cloud) is the same class of transient, no-response-
    # received failure -- confirmed via a real production case (execution
    # 045e8038, 2026-07-09) that previously fell through to UNKNOWN with
    # zero retry.
    if cls_name == "APIConnectionError":
        return ErrorType.PROVIDER_DOWN
    if cls_name in ("APITimeoutError", "Timeout"):
        return ErrorType.TIMEOUT
    if cls_name in ("ReadError", "ConnectError", "WriteError", "ConnectTimeout", "ReadTimeout"):
        return ErrorType.PROVIDER_DOWN

    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int) or status_code <= 0:
        return None  # not a structured status error -- fall back

    if status_code == 404:
        return ErrorType.INVALID_MODEL
    if status_code == 429:
        return ErrorType.RATE_LIMIT
    if status_code in (401, 403):
        return ErrorType.AUTH
    if status_code == 402:
        # Anthropic's billing_error -- account/credential-adjacent, not
        # worth retrying or falling over to a different model on the same
        # account, but a *different provider's* account may be fine.
        return ErrorType.AUTH
    if status_code in (408, 504):
        return ErrorType.TIMEOUT
    if status_code == 400:
        # A handful of providers fold content-policy refusals into a
        # plain 400 with a hint in the parsed body rather than a distinct
        # status code (openai does this; ollama_cloud, same SDK, confirmed
        # empirically to match) -- check before defaulting to INVALID_REQUEST.
        body_hint = f"{getattr(exc, 'type', '') or ''} {getattr(exc, 'code', '') or ''}".lower()
        if any(k in body_hint for k in ("content", "moderation", "safety")):
            return ErrorType.CONTENT_POLICY
        return ErrorType.INVALID_REQUEST
    if status_code == 422:
        return ErrorType.INVALID_REQUEST
    if status_code == 529 or status_code >= 500:
        # Anthropic's overloaded_error (529) is deliberately bucketed with
        # 5xx here -- both mean "this provider can't serve the request
        # right now," which is exactly PROVIDER_DOWN's Tier A semantics
        # (retry with backoff, then fail over).
        return ErrorType.PROVIDER_DOWN

    return None  # unrecognised status code -- fall back to string heuristic


def classify_error(exc: Exception) -> ErrorType:
    """
    Maps exceptions to ErrorType enum values.

    Tries the structured, SDK-aware classifier first (see
    _classify_structured_error) -- it's based on documented, stable
    exception attributes (status_code / type / class name) rather than
    prose, so it can't be thrown off by a provider rewording a message.
    Falls back to the string heuristic below for anything it doesn't
    recognise (bare network exceptions, an SDK not shaped this way, etc).
    """
    structured = _classify_structured_error(exc)
    if structured is not None:
        return structured

    msg = str(exc).lower()

    if "rate limit" in msg or "429" in msg or "too many requests" in msg:
        return ErrorType.RATE_LIMIT

    if "timeout" in msg or "timed out" in msg:
        return ErrorType.TIMEOUT

    if "401" in msg or "unauthorized" in msg or "api key" in msg:
        return ErrorType.AUTH

    if (
        "model not found" in msg
        or "model_not_found" in msg
        or "no such model" in msg
        or "invalid model" in msg
        or "does not exist" in msg and "model" in msg
    ):
        return ErrorType.INVALID_MODEL

    # Checked before the generic INVALID_REQUEST bucket below: providers
    # commonly return content-policy refusals as a 400/"bad request" too,
    # so the specific phrase must win over the generic one (same reasoning
    # as INVALID_MODEL being checked before INVALID_REQUEST above).
    if (
        "content policy" in msg
        or "content_policy" in msg
        or "content management policy" in msg
        or "content_filter" in msg
        or "content filter" in msg
        or "moderation" in msg
        or "safety system" in msg
    ):
        return ErrorType.CONTENT_POLICY

    # Checked before the generic INVALID_REQUEST bucket below, same
    # reasoning as CONTENT_POLICY/INVALID_MODEL above: providers that
    # aren't caught by the structured ContextWindowExceededError check
    # (a raw SDK not shaped that way, or litellm not in the exception's
    # MRO) still describe context overflow in the message text itself.
    if (
        "context length" in msg
        or "context window" in msg
        or "maximum context length" in msg
        or "too many tokens" in msg
        or "reduce the length of the messages" in msg
    ):
        return ErrorType.CONTEXT_LENGTH_EXCEEDED

    if "invalid request" in msg or "bad request" in msg or "400" in msg:
        return ErrorType.INVALID_REQUEST

    if (
        "server error" in msg
        or "503" in msg
        or "502" in msg
        or "500" in msg
        or "service unavailable" in msg
        or "connection error" in msg
        or "connection refused" in msg
    ):
        return ErrorType.PROVIDER_DOWN

    return ErrorType.UNKNOWN


# ---------------------------------------------------------------------------
# Per-error-type handling policy — consulted by AgentEngine._gen_with_fallback().
#
# Three-tier model (not a flat "does this trigger fallback?" boolean):
#
#   Tier A — retryable=True:  transient; worth retrying the SAME provider
#            (with backoff) before giving up on it.
#            RATE_LIMIT, TIMEOUT, PROVIDER_DOWN.
#
#   Tier B — retryable=False, fallback_eligible=True: deterministic/structural
#            failure on THIS provider — retrying it can never succeed, but a
#            different provider may not have the same problem, so skip
#            straight to the next candidate with zero retries.
#            INVALID_MODEL (model doesn't exist here),
#            CONTENT_POLICY (this provider's moderation refused it; another
#            provider's policy may differ).
#
#   Tier B' — AUTH: fallback_eligible=True but skip_same_provider=True.
#            Only ever reached reactively (a key WAS sent and the provider
#            rejected it — e.g. revoked/expired since the session was
#            configured; a fully-missing credential is now caught earlier,
#            at POST /set-session save time — see api/session.py's V6.5
#            credential gate, which drops/rejects any provider entry with
#            no resolvable API key before it can ever reach AgentEngine).
#            Because the same credential/account likely governs every
#            candidate on that provider, every remaining candidate sharing
#            the failed provider is skipped without being attempted; a
#            candidate on a *different* provider (a separate credential)
#            is still tried. If every remaining candidate shares the
#            failed provider, this naturally exhausts the chain and
#            surfaces as an error — no special-case needed for that.
#
#   Tier C — retryable=False, fallback_eligible=False: hard stop. Nothing
#            downstream (retry or a different provider) fixes these.
#            INVALID_REQUEST, INTERNAL, UNKNOWN (UNKNOWN stays
#            conservative rather than guessing — an unclassified error is
#            exactly the case where blind retry/fallback could mask a real
#            EVA-side bug).
#
# NOTE: every field here is consulted by AgentEngine._gen_with_fallback()
# (retryable / max_retries since Phase 2 — same-provider retry-with-backoff;
# fallback_eligible / skip_same_provider since Phase 1). Tier A's
# max_retries values, and the backoff timing (_backoff_sleep's base/cap in
# core/agent_engine.py), are sourced from config/settings.py — overridable
# via eva.env, same convention as every other runtime-tunable constant in
# EVA (see FALLBACK_MAX_RETRIES_* / FALLBACK_RETRY_*_DELAY_S there).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ErrorPolicy:
    retryable: bool
    max_retries: int
    fallback_eligible: bool
    # Only meaningful when fallback_eligible=True. When set, candidates
    # sharing the same provider as the one that just failed are skipped
    # without being attempted, rather than tried in order like any other
    # fallback-eligible error. AUTH is the only type that sets this today.
    skip_same_provider: bool = False


# Tier A retry counts come from config/settings.py (eva.env-overridable). A
# type whose configured count is 0 simply never satisfies
# `retries_used < policy.max_retries`, so it behaves exactly like a
# non-retryable type -- no separate on/off flag needed.
ERROR_POLICIES: Dict[ErrorType, ErrorPolicy] = {
    ErrorType.RATE_LIMIT:      ErrorPolicy(retryable=True,  max_retries=_MAX_RETRIES_RATE_LIMIT,    fallback_eligible=True),
    ErrorType.TIMEOUT:         ErrorPolicy(retryable=True,  max_retries=_MAX_RETRIES_TIMEOUT,       fallback_eligible=True),
    ErrorType.PROVIDER_DOWN:   ErrorPolicy(retryable=True,  max_retries=_MAX_RETRIES_PROVIDER_DOWN, fallback_eligible=True),
    ErrorType.INVALID_MODEL:   ErrorPolicy(retryable=False, max_retries=0, fallback_eligible=True),
    ErrorType.CONTENT_POLICY:  ErrorPolicy(retryable=False, max_retries=0, fallback_eligible=True),
    # V6.11: this provider/model's context window rejected the request --
    # not retryable on the same candidate (the input is still too big),
    # but a different candidate in the fallback chain may have more
    # headroom, so fallback-eligible same as INVALID_MODEL/CONTENT_POLICY.
    ErrorType.CONTEXT_LENGTH_EXCEEDED: ErrorPolicy(retryable=False, max_retries=0, fallback_eligible=True),
    ErrorType.AUTH:            ErrorPolicy(retryable=False, max_retries=0, fallback_eligible=True, skip_same_provider=True),
    ErrorType.INVALID_REQUEST: ErrorPolicy(retryable=False, max_retries=0, fallback_eligible=False),
    ErrorType.INTERNAL:        ErrorPolicy(retryable=False, max_retries=0, fallback_eligible=False),
    ErrorType.UNKNOWN:         ErrorPolicy(retryable=False, max_retries=0, fallback_eligible=False),
}

# Defensive default for any ErrorType somehow missing from the table above
# (e.g. a future enum member added without a matching policy entry) — treat
# it the same as UNKNOWN: no retry, no fallback, surface the error as-is.
_DEFAULT_POLICY = ErrorPolicy(retryable=False, max_retries=0, fallback_eligible=False)


def get_error_policy(error_type: ErrorType) -> ErrorPolicy:
    """Look up the handling policy for an ErrorType enum value."""
    return ERROR_POLICIES.get(error_type, _DEFAULT_POLICY)


def get_error_policy_by_value(value: "str | None") -> ErrorPolicy:
    """
    Look up the handling policy from an error_type *string* (e.g. the value
    stored on ErrorChunk.error_type, which is always a plain str, not an
    ErrorType instance). Unrecognised or missing values fall back to the
    same conservative no-retry/no-fallback default as UNKNOWN.
    """
    if not value:
        return _DEFAULT_POLICY
    try:
        return get_error_policy(ErrorType(value))
    except ValueError:
        return _DEFAULT_POLICY
