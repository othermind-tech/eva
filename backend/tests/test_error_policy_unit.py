# tests/test_error_policy_unit.py
"""
Unit tests for V6.5 Phase 1: error taxonomy rework for the provider
fallback chain.

Covers:
  - core.error_handling.classify_error() — new CONTENT_POLICY detection,
    plus regression coverage for every pre-existing category so the
    taxonomy rework does not silently reclassify anything.
  - core.error_handling.ERROR_POLICIES / get_error_policy() /
    get_error_policy_by_value() — table completeness, correct tier
    assignment (retryable / max_retries / fallback_eligible), and
    defensive fallback for unrecognised or missing values.
  - core.agent_engine.AgentEngine._gen_with_fallback() — end-to-end
    regression + new-behavior check: every error type that used to be in
    the old flat FALLBACK_ERROR_TYPES set still triggers fallback exactly
    as before, every hard-stop type still does not, and the new
    CONTENT_POLICY type now also triggers fallback (the one intentional
    behavior change in this phase).

Mocked throughout — no live server, no real API calls.

Run with:
    cd <project_root>
    python tests/test_error_policy_unit.py
    (or: pytest tests/test_error_policy_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _error_stream(error_type: str, message: str = "boom"):
    from core.types import ErrorChunk

    async def gen(*_a, **_kw):
        yield ErrorChunk(message=message, provider="primary", error_type=error_type)
    return gen


def _text_stream(*texts):
    from core.types import TextChunk

    async def gen(*_a, **_kw):
        for t in texts:
            yield TextChunk(content=t)
    return gen


async def _collect(agen):
    out = []
    async for chunk in agen:
        out.append(chunk)
    return out


def _sequence_stream(*factories):
    """Returns a callable usable as handler.create_message: each *call*
    advances to the next factory in the sequence (clamped to the last one
    once exhausted). Used to simulate "fails N times, then succeeds" for
    retry tests -- _error_stream/_text_stream alone always behave the same
    on every call, which can't express that."""
    state = {"n": 0}

    def create_message(*args, **kwargs):
        idx = min(state["n"], len(factories) - 1)
        state["n"] += 1
        return factories[idx](*args, **kwargs)

    return create_message


class TestClassifyErrorRegression(unittest.TestCase):
    """Every pre-existing category must still classify exactly as before."""

    def test_rate_limit(self):
        from core.error_handling import classify_error, ErrorType
        self.assertEqual(classify_error(Exception("429 Too Many Requests")), ErrorType.RATE_LIMIT)
        self.assertEqual(classify_error(Exception("rate limit exceeded")), ErrorType.RATE_LIMIT)

    def test_timeout(self):
        from core.error_handling import classify_error, ErrorType
        self.assertEqual(classify_error(Exception("Request timed out")), ErrorType.TIMEOUT)

    def test_auth(self):
        from core.error_handling import classify_error, ErrorType
        self.assertEqual(classify_error(Exception("401 Unauthorized")), ErrorType.AUTH)
        self.assertEqual(classify_error(Exception("Invalid API key provided")), ErrorType.AUTH)

    def test_invalid_model(self):
        from core.error_handling import classify_error, ErrorType
        self.assertEqual(classify_error(Exception("model not found: foo-bar")), ErrorType.INVALID_MODEL)
        self.assertEqual(classify_error(Exception("The model does not exist")), ErrorType.INVALID_MODEL)

    def test_invalid_request(self):
        from core.error_handling import classify_error, ErrorType
        self.assertEqual(classify_error(Exception("400 Bad Request: malformed JSON")), ErrorType.INVALID_REQUEST)

    def test_provider_down(self):
        from core.error_handling import classify_error, ErrorType
        self.assertEqual(classify_error(Exception("503 Service Unavailable")), ErrorType.PROVIDER_DOWN)
        self.assertEqual(classify_error(Exception("Connection refused")), ErrorType.PROVIDER_DOWN)

    def test_unknown(self):
        from core.error_handling import classify_error, ErrorType
        self.assertEqual(classify_error(Exception("something bizarre happened")), ErrorType.UNKNOWN)


class TestClassifyErrorContentPolicy(unittest.TestCase):
    """New in Phase 1."""

    def test_content_policy_phrases(self):
        from core.error_handling import classify_error, ErrorType
        cases = [
            "Your request was rejected due to our content policy",
            "content_policy_violation",
            "blocked by content management policy",
            "response was blocked by the content_filter",
            "flagged by our moderation system",
            "blocked by the safety system",
        ]
        for msg in cases:
            with self.subTest(msg=msg):
                self.assertEqual(classify_error(Exception(msg)), ErrorType.CONTENT_POLICY)

    def test_content_policy_wins_over_generic_400(self):
        """A provider that reports a content-policy refusal as a 400 must
        still classify as CONTENT_POLICY, not the generic INVALID_REQUEST —
        same precedence reasoning as INVALID_MODEL vs INVALID_REQUEST."""
        from core.error_handling import classify_error, ErrorType
        msg = "400 Bad Request: content policy violation detected"
        self.assertEqual(classify_error(Exception(msg)), ErrorType.CONTENT_POLICY)

    def test_does_not_false_positive_on_unrelated_400(self):
        from core.error_handling import classify_error, ErrorType
        self.assertEqual(
            classify_error(Exception("400 Bad Request: missing required field 'messages'")),
            ErrorType.INVALID_REQUEST,
        )


class TestClassifyErrorContextLength(unittest.TestCase):
    """V6.11 — context-budget watchdog's error-classification safety net."""

    def test_structured_context_window_exceeded(self):
        """litellm's ContextWindowExceededError subclasses openai.APIStatusError
        (has .status_code, typically 400) -- must be caught by class name
        before the generic status_code=400 bucket, same precedence as
        ContentPolicyViolationError."""
        from core.error_handling import classify_error, ErrorType

        class ContextWindowExceededError(Exception):
            status_code = 400

        self.assertEqual(
            classify_error(ContextWindowExceededError("this model's maximum context length is 8192 tokens")),
            ErrorType.CONTEXT_LENGTH_EXCEEDED,
        )

    def test_string_heuristic_phrases(self):
        from core.error_handling import classify_error, ErrorType
        cases = [
            "This model's maximum context length is 8192 tokens",
            "prompt is too long: context window exceeded",
            "Please reduce the length of the messages",
            "too many tokens in the request",
        ]
        for msg in cases:
            with self.subTest(msg=msg):
                self.assertEqual(classify_error(Exception(msg)), ErrorType.CONTEXT_LENGTH_EXCEEDED)

    def test_wins_over_generic_400(self):
        from core.error_handling import classify_error, ErrorType
        msg = "400 Bad Request: context length exceeded for this model"
        self.assertEqual(classify_error(Exception(msg)), ErrorType.CONTEXT_LENGTH_EXCEEDED)

    def test_does_not_false_positive_on_unrelated_400(self):
        from core.error_handling import classify_error, ErrorType
        self.assertEqual(
            classify_error(Exception("400 Bad Request: missing required field 'messages'")),
            ErrorType.INVALID_REQUEST,
        )


class TestClassifyErrorConnectionDrops(unittest.TestCase):
    """V6.11 — httpx transport-level exceptions (e.g. a mid-stream drop to
    Ollama Cloud, confirmed in execution 045e8038 / 2026-07-09 logs) must
    classify as PROVIDER_DOWN (transient, retryable), not fall through to
    UNKNOWN with zero retry."""

    def test_httpx_read_error(self):
        from core.error_handling import classify_error, ErrorType

        class ReadError(Exception):
            pass  # httpx.ReadError carries no .status_code -- no response was ever received

        self.assertEqual(classify_error(ReadError("")), ErrorType.PROVIDER_DOWN)

    def test_httpx_connect_error(self):
        from core.error_handling import classify_error, ErrorType

        class ConnectError(Exception):
            pass

        self.assertEqual(classify_error(ConnectError("connection reset")), ErrorType.PROVIDER_DOWN)


class TestErrorPolicyTable(unittest.TestCase):
    def test_every_error_type_has_a_policy(self):
        from core.error_handling import ErrorType, ERROR_POLICIES
        for et in ErrorType:
            with self.subTest(error_type=et):
                self.assertIn(et, ERROR_POLICIES)

    def test_tier_a_retryable_types(self):
        from core.error_handling import ErrorType, get_error_policy
        for et in (ErrorType.RATE_LIMIT, ErrorType.TIMEOUT, ErrorType.PROVIDER_DOWN):
            with self.subTest(error_type=et):
                policy = get_error_policy(et)
                self.assertTrue(policy.retryable)
                self.assertGreater(policy.max_retries, 0)
                self.assertTrue(policy.fallback_eligible)

    def test_tier_b_skip_retry_but_fallback_eligible(self):
        from core.error_handling import ErrorType, get_error_policy
        for et in (ErrorType.INVALID_MODEL, ErrorType.CONTENT_POLICY, ErrorType.CONTEXT_LENGTH_EXCEEDED):
            with self.subTest(error_type=et):
                policy = get_error_policy(et)
                self.assertFalse(policy.retryable)
                self.assertEqual(policy.max_retries, 0)
                self.assertTrue(policy.fallback_eligible)

    def test_tier_c_hard_stop_types(self):
        from core.error_handling import ErrorType, get_error_policy
        for et in (ErrorType.INVALID_REQUEST, ErrorType.INTERNAL, ErrorType.UNKNOWN):
            with self.subTest(error_type=et):
                policy = get_error_policy(et)
                self.assertFalse(policy.retryable)
                self.assertEqual(policy.max_retries, 0)
                self.assertFalse(policy.fallback_eligible)
                self.assertFalse(policy.skip_same_provider)

    def test_tier_b_prime_auth_is_fallback_eligible_but_provider_scoped(self):
        """AUTH is fallback-eligible (Tier B') but scoped: only reached
        reactively (a key was sent and rejected — see api/session.py's
        save-time credential gate for why a fully-missing key never reaches
        AgentEngine at all), and candidates sharing the failed provider must
        be skipped rather than retried."""
        from core.error_handling import ErrorType, get_error_policy
        policy = get_error_policy(ErrorType.AUTH)
        self.assertFalse(policy.retryable)
        self.assertEqual(policy.max_retries, 0)
        self.assertTrue(policy.fallback_eligible)
        self.assertTrue(policy.skip_same_provider)

    def test_no_other_type_sets_skip_same_provider(self):
        from core.error_handling import ErrorType, get_error_policy
        for et in ErrorType:
            if et is ErrorType.AUTH:
                continue
            with self.subTest(error_type=et):
                self.assertFalse(get_error_policy(et).skip_same_provider)

    def test_get_error_policy_by_value_known(self):
        from core.error_handling import get_error_policy_by_value
        self.assertTrue(get_error_policy_by_value("rate_limit").fallback_eligible)
        self.assertTrue(get_error_policy_by_value("auth").fallback_eligible)
        self.assertTrue(get_error_policy_by_value("auth").skip_same_provider)
        self.assertFalse(get_error_policy_by_value("invalid_request").fallback_eligible)

    def test_get_error_policy_by_value_unknown_string_is_safe_default(self):
        from core.error_handling import get_error_policy_by_value
        policy = get_error_policy_by_value("some_made_up_type_that_does_not_exist")
        self.assertFalse(policy.retryable)
        self.assertFalse(policy.fallback_eligible)

    def test_get_error_policy_by_value_none_is_safe_default(self):
        from core.error_handling import get_error_policy_by_value
        policy = get_error_policy_by_value(None)
        self.assertFalse(policy.fallback_eligible)


def _build_agent(fallback_entries):
    """Construct an AgentEngine with a mocked primary handler and a
    pre-built fallback handler list.

    _build_fallback_handlers() runs lazily on first use *inside*
    _gen_with_fallback (by design — see agent_engine.py), which is well
    after any `with patch(...)` block wrapping just the constructor call
    would have exited. Rather than keep create_provider_handler patched
    for the whole test (and exercise _build_fallback_handlers' own
    capability-check logic, which is out of scope for this phase), inject
    the fallback handler tuple directly into the private
    `_fallback_handlers` cache so `_gen_with_fallback` sees it as already
    built and skips the lazy-build call entirely.
    """
    from core.agent_engine import AgentEngine

    primary_handler = MagicMock()
    fallback_handler = MagicMock()

    with patch("core.agent_engine.create_provider_handler", return_value=primary_handler):
        agent = AgentEngine(
            provider="primary_provider",
            api_key="k1",
            model="primary_model",
            reasoning=None,
            fallback_chain=fallback_entries,
        )

    agent._fallback_handlers = [
        (fallback_handler, entry["provider"], entry["model"]) for entry in fallback_entries
    ]
    return agent, primary_handler, fallback_handler


def _build_agent_multi(primary, fallback_entries):
    """Like _build_agent, but supports multiple fallback candidates with
    independently-controllable mock handlers. Needed for the AUTH
    skip_same_provider tests, where different candidates in the same chain
    must behave differently (one must never be attempted at all).

    primary: (provider, model) tuple.
    fallback_entries: list of (provider, model) tuples, in chain order.
    Returns (agent, primary_handler, [fallback_handler, ...]) — the list is
    in the same order as fallback_entries.
    """
    from core.agent_engine import AgentEngine

    primary_provider, primary_model = primary
    primary_handler = MagicMock()
    fallback_handlers = [MagicMock() for _ in fallback_entries]

    with patch("core.agent_engine.create_provider_handler", return_value=primary_handler):
        agent = AgentEngine(
            provider=primary_provider,
            api_key="k1",
            model=primary_model,
            reasoning=None,
            fallback_chain=[
                {"provider": p, "model": m, "api_key": "k"} for p, m in fallback_entries
            ],
        )

    agent._fallback_handlers = [
        (h, p, m) for h, (p, m) in zip(fallback_handlers, fallback_entries)
    ]
    return agent, primary_handler, fallback_handlers


class TestProviderFallbackChunkEmission(unittest.IsolatedAsyncioTestCase):
    """V6.5 Phase 5: a live SSE-visible chunk must be yielded exactly when
    the fallback chain switches candidates -- both from _gen_with_fallback
    directly, and (critically) through the public .call() wrapper, since
    call()'s chunk-dispatch loop was found to silently drop any chunk type
    it doesn't explicitly recognise (no default/else branch) -- confirmed
    by reading the code, not assumed, before deciding a case needed adding
    there too, not just in _gen_with_fallback and stream_buffer.py."""

    async def test_gen_with_fallback_yields_chunk_on_switch(self):
        from core.types import ProviderFallbackChunk

        agent, primary_handler, fallback_handler = _build_agent(
            [{"provider": "fb_provider", "model": "fb_model", "api_key": "k2"}]
        )
        primary_handler.create_message = _error_stream("provider_down")
        fallback_handler.create_message = _text_stream("fallback succeeded")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        fallback_chunks = [c for c in chunks if isinstance(c, ProviderFallbackChunk)]
        self.assertEqual(len(fallback_chunks), 1)
        self.assertEqual(fallback_chunks[0].provider, "fb_provider")
        self.assertEqual(fallback_chunks[0].model, "fb_model")

    async def test_no_chunk_emitted_on_pure_success(self):
        from core.types import ProviderFallbackChunk

        agent, primary_handler, fallback_handler = _build_agent(
            [{"provider": "fb_provider", "model": "fb_model", "api_key": "k2"}]
        )
        primary_handler.create_message = _text_stream("succeeded first try")
        fallback_handler.create_message = _text_stream("SHOULD NEVER APPEAR")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        self.assertFalse(any(isinstance(c, ProviderFallbackChunk) for c in chunks))

    async def test_no_chunk_emitted_on_hard_stop(self):
        from core.types import ProviderFallbackChunk

        agent, primary_handler, fallback_handler = _build_agent(
            [{"provider": "fb_provider", "model": "fb_model", "api_key": "k2"}]
        )
        primary_handler.create_message = _error_stream("internal")
        fallback_handler.create_message = _text_stream("SHOULD NEVER APPEAR")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        self.assertFalse(any(isinstance(c, ProviderFallbackChunk) for c in chunks))

    async def test_survives_through_public_call_wrapper(self):
        """Regression guard for the silent-drop bug: AgentEngine.call()'s
        chunk-dispatch loop must forward ProviderFallbackChunk, not just
        _gen_with_fallback in isolation."""
        from core.types import ProviderFallbackChunk, TextChunk

        agent, primary_handler, fallback_handler = _build_agent(
            [{"provider": "fb_provider", "model": "fb_model", "api_key": "k2"}]
        )
        primary_handler.create_message = _error_stream("provider_down")
        fallback_handler.create_message = _text_stream("fallback succeeded")

        chunks = await _collect(agent.call(messages=[{"role": "user", "content": "hi"}], stream=True))

        fallback_chunks = [c for c in chunks if isinstance(c, ProviderFallbackChunk)]
        self.assertEqual(len(fallback_chunks), 1)
        self.assertEqual(fallback_chunks[0].provider, "fb_provider")
        self.assertEqual(fallback_chunks[0].model, "fb_model")

        text_chunks = [c.content for c in chunks if isinstance(c, TextChunk)]
        self.assertIn("fallback succeeded", text_chunks)


class TestAgentEngineAuthSameProviderSkip(unittest.IsolatedAsyncioTestCase):
    """AUTH failures must skip every remaining candidate sharing the failed
    provider without attempting them, but still try a candidate on a
    different provider — see ERROR_POLICIES' skip_same_provider."""

    async def test_same_provider_fallback_skipped_different_provider_tried(self):
        agent, primary_handler, (fb_same, fb_different) = _build_agent_multi(
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_a", "model_2"), ("provider_b", "model_3")],
        )

        primary_handler.create_message = _error_stream("auth", message="401 unauthorized")
        fb_same.create_message = _text_stream("SHOULD NEVER APPEAR — same provider as primary")
        fb_different.create_message = _text_stream("different provider succeeded")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertIn("different provider succeeded", texts)
        self.assertNotIn("SHOULD NEVER APPEAR — same provider as primary", texts)
        self.assertEqual(agent.provider, "provider_b")
        self.assertEqual(agent.model, "model_3")

    async def test_all_candidates_same_provider_exhausts_with_original_error(self):
        """If every candidate in the chain shares the primary's provider,
        skip_same_provider must leave nothing eligible — the original AUTH
        error is surfaced as-is, not silently swallowed."""
        agent, primary_handler, (fb_same,) = _build_agent_multi(
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_a", "model_2")],
        )

        primary_handler.create_message = _error_stream("auth", message="401 unauthorized: bad key")
        fb_same.create_message = _text_stream("SHOULD NEVER APPEAR")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertNotIn("SHOULD NEVER APPEAR", texts)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].error_type, "auth")
        # Nothing was ever committed — still the original primary.
        self.assertEqual(agent.provider, "provider_a")
        self.assertEqual(agent.model, "model_1")

    async def test_different_provider_auth_failures_both_skip_correctly(self):
        """Two AUTH failures in a row, on two different providers, must
        each add their own provider to the skip set (not just the first)."""
        agent, primary_handler, (fb_b, fb_a_again, fb_c) = _build_agent_multi(
            primary=("provider_a", "model_1"),
            fallback_entries=[
                ("provider_b", "model_2"),
                ("provider_a", "model_3"),   # same as primary -- must be skipped
                ("provider_c", "model_4"),
            ],
        )

        primary_handler.create_message = _error_stream("auth", message="401")
        fb_b.create_message = _error_stream("auth", message="401")
        fb_a_again.create_message = _text_stream("SHOULD NEVER APPEAR")
        fb_c.create_message = _text_stream("provider_c succeeded")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertIn("provider_c succeeded", texts)
        self.assertNotIn("SHOULD NEVER APPEAR", texts)
        self.assertEqual(agent.provider, "provider_c")
        self.assertEqual(agent.model, "model_4")


class TestAgentEngineFallbackEligibility(unittest.IsolatedAsyncioTestCase):
    """Regression + new-behavior check against the real _gen_with_fallback,
    using the policy table instead of the old flat FALLBACK_ERROR_TYPES set.

    Tier A errors (rate_limit/timeout/provider_down) now retry the same
    candidate before falling back (V6.5 Phase 2) -- _backoff_sleep is
    mocked here so those retries don't incur real wall-clock delay; the
    dedicated retry-timing assertions live in
    TestAgentEngineRetryWithBackoff below, which controls it explicitly."""

    FALLBACK_CHAIN = [{"provider": "fb_provider", "model": "fb_model", "api_key": "k2"}]

    async def asyncSetUp(self):
        self._backoff_patcher = patch("core.agent_engine._backoff_sleep", new=AsyncMock())
        self._backoff_patcher.start()

    async def asyncTearDown(self):
        self._backoff_patcher.stop()

    async def _run_and_assert_fallback_occurs(self, error_type: str):
        agent, primary_handler, fallback_handler = _build_agent(self.FALLBACK_CHAIN)
        primary_handler.create_message = _error_stream(error_type)
        fallback_handler.create_message = _text_stream("fallback succeeded")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertIn("fallback succeeded", texts)
        self.assertEqual(agent.provider, "fb_provider")
        self.assertEqual(agent.model, "fb_model")

    async def _run_and_assert_no_fallback(self, error_type: str):
        agent, primary_handler, fallback_handler = _build_agent(self.FALLBACK_CHAIN)
        primary_handler.create_message = _error_stream(error_type, message="hard stop")
        fallback_handler.create_message = _text_stream("should never be reached")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].error_type, error_type)
        # Primary was never abandoned — no fallback commit happened.
        self.assertEqual(agent.provider, "primary_provider")
        self.assertEqual(agent.model, "primary_model")

    # --- Tier A + B (regression: pre-existing fallback-eligible types) ---
    async def test_rate_limit_triggers_fallback(self):
        await self._run_and_assert_fallback_occurs("rate_limit")

    async def test_timeout_triggers_fallback(self):
        await self._run_and_assert_fallback_occurs("timeout")

    async def test_provider_down_triggers_fallback(self):
        await self._run_and_assert_fallback_occurs("provider_down")

    async def test_invalid_model_triggers_fallback(self):
        await self._run_and_assert_fallback_occurs("invalid_model")

    # --- Tier B (new in Phase 1) ---
    async def test_content_policy_triggers_fallback(self):
        await self._run_and_assert_fallback_occurs("content_policy")

    # --- Tier B' (AUTH is now fallback-eligible-but-scoped, not a hard
    # stop — see TestAgentEngineAuthSameProviderSkip above for the
    # same-provider-skip behavior specifically. This just confirms the
    # simple case: primary's provider differs from the (single) fallback
    # candidate's provider, so it's still tried normally.) ---
    async def test_auth_triggers_fallback_to_different_provider(self):
        await self._run_and_assert_fallback_occurs("auth")

    # --- Tier C (regression: hard stops must remain hard stops) ---
    async def test_invalid_request_does_not_trigger_fallback(self):
        await self._run_and_assert_no_fallback("invalid_request")

    async def test_internal_does_not_trigger_fallback(self):
        await self._run_and_assert_no_fallback("internal")


class TestAgentEngineRetryWithBackoff(unittest.IsolatedAsyncioTestCase):
    """V6.5 Phase 2: Tier A errors (rate_limit/timeout/provider_down) must
    retry the SAME candidate up to its policy's max_retries, with backoff,
    before ever falling back to the next candidate in the chain."""

    async def asyncSetUp(self):
        self._backoff_patcher = patch("core.agent_engine._backoff_sleep", new=AsyncMock())
        self.mock_backoff = self._backoff_patcher.start()

    async def asyncTearDown(self):
        self._backoff_patcher.stop()

    async def test_rate_limit_succeeds_on_second_attempt_no_fallback(self):
        """Fails once, succeeds on retry -- must stay on the SAME provider,
        never touching the fallback_chain at all."""
        agent, primary_handler, (fb,) = _build_agent_multi(
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary_handler.create_message = _sequence_stream(
            _error_stream("rate_limit"),
            _text_stream("succeeded after one retry"),
        )
        fb.create_message = _text_stream("SHOULD NEVER APPEAR — fallback not needed")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertIn("succeeded after one retry", texts)
        self.assertNotIn("SHOULD NEVER APPEAR — fallback not needed", texts)
        # Never fell back -- still the primary.
        self.assertEqual(agent.provider, "provider_a")
        self.assertEqual(agent.model, "model_1")
        self.assertEqual(self.mock_backoff.call_count, 1)

    async def test_rate_limit_exhausts_retries_then_falls_back(self):
        """rate_limit's policy is max_retries=2 -- 3 total attempts on the
        primary (1 original + 2 retries), all failing, before falling back."""
        agent, primary_handler, (fb,) = _build_agent_multi(
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary_handler.create_message = _error_stream("rate_limit")
        fb.create_message = _text_stream("fallback succeeded")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertIn("fallback succeeded", texts)
        self.assertEqual(agent.provider, "provider_b")
        self.assertEqual(self.mock_backoff.call_count, 2)  # max_retries for rate_limit

    async def test_timeout_retries_once_then_falls_back(self):
        """timeout's policy is max_retries=1 -- confirms the retry count is
        genuinely per-error-type, not a single hardcoded number."""
        agent, primary_handler, (fb,) = _build_agent_multi(
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary_handler.create_message = _error_stream("timeout")
        fb.create_message = _text_stream("fallback succeeded")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertIn("fallback succeeded", texts)
        self.assertEqual(self.mock_backoff.call_count, 1)  # max_retries for timeout

    async def test_backoff_called_with_increasing_retry_number(self):
        """Confirms the retry_number argument passed to _backoff_sleep
        increases each attempt (0, 1, ...) -- what drives the exponential
        growth in the real (unmocked) implementation."""
        agent, primary_handler, (fb,) = _build_agent_multi(
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary_handler.create_message = _error_stream("provider_down")
        fb.create_message = _text_stream("fallback succeeded")

        await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        calls = [c.args[0] for c in self.mock_backoff.call_args_list]
        self.assertEqual(calls, [0, 1])  # provider_down max_retries=2

    async def test_invalid_model_never_retries(self):
        """Tier B: zero retries by design (deterministic/structural on this
        provider) -- straight to fallback, _backoff_sleep never called."""
        agent, primary_handler, (fb,) = _build_agent_multi(
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary_handler.create_message = _error_stream("invalid_model")
        fb.create_message = _text_stream("fallback succeeded")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertIn("fallback succeeded", texts)
        self.mock_backoff.assert_not_called()

    async def test_hard_stop_never_retries(self):
        """Tier C: zero retries, zero fallback -- surfaces immediately."""
        agent, primary_handler, (fb,) = _build_agent_multi(
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary_handler.create_message = _error_stream("internal")
        fb.create_message = _text_stream("SHOULD NEVER APPEAR")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertNotIn("SHOULD NEVER APPEAR", texts)
        self.mock_backoff.assert_not_called()


class TestBackoffSleepImplementation(unittest.IsolatedAsyncioTestCase):
    """Unmocked checks of the actual _backoff_sleep implementation itself
    (not agent_engine's loop) -- deliberately its own class, with no
    class-level _backoff_sleep patch, unlike TestAgentEngineRetryWithBackoff
    above which needs one to keep its loop-behavior tests fast."""

    async def test_exponential_with_jitter(self):
        from core.agent_engine import _backoff_sleep

        with patch("core.agent_engine.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await _backoff_sleep(0, base=1.0, cap=100.0)
            await _backoff_sleep(1, base=1.0, cap=100.0)
            await _backoff_sleep(2, base=1.0, cap=100.0)

        delays = [c.args[0] for c in mock_sleep.call_args_list]
        # Each delay is base * 2**n plus up to 25% jitter -- strictly
        # increasing and within the expected jitter band.
        self.assertTrue(1.0 <= delays[0] <= 1.25)
        self.assertTrue(2.0 <= delays[1] <= 2.5)
        self.assertTrue(4.0 <= delays[2] <= 5.0)

    async def test_respects_cap(self):
        from core.agent_engine import _backoff_sleep

        with patch("core.agent_engine.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await _backoff_sleep(10, base=1.0, cap=8.0)

        delay = mock_sleep.call_args.args[0]
        self.assertTrue(8.0 <= delay <= 10.0)  # capped at 8, plus up to 25% jitter


if __name__ == "__main__":
    unittest.main(verbosity=2)
