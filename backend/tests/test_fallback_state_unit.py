# tests/test_fallback_state_unit.py
"""
Unit tests for V6.8: shared fallback-resolution primitives
(core/providers/fallback_state.py) and the two new non-streaming call-shape
tiers built on top of them (core/providers/fallback_call.py's
resolve_and_call_once / call_with_full_fallback).

Background: before V6.8, only AgentEngine's streaming chat/daemon path
(core/agent_engine.py) had cross-execution cooldown awareness and
fallback_chain support. Everything else -- title generator, prompt
enhancer, memory extractor/summarizer, artifact updater, coordinate_tool's
planning/synthesis calls -- either had no fallback awareness at all, or
(Verifier/Planner/IntentRouter) had a lighter version with no cooldown.
This moved the cooldown/backoff/API-key-resolution primitives out of
agent_engine.py into a shared module, and built two new call shapes for
non-streaming single-shot calls: resolve-once-and-record (no auto-retry)
and full-reactive-loop (retry-same-candidate-with-backoff, then advance).

Uses the REAL core.broker.local_broker (not mocked) for cooldown tests,
same as test_fallback_cooldown_unit.py -- it's pure in-memory Python with
no I/O, but is a process-wide singleton, so every test uses a unique
user_id to avoid cross-test pollution.

Run with:
    cd <project_root>
    python tests/test_fallback_state_unit.py
    (or: pytest tests/test_fallback_state_unit.py -v)
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _uid() -> str:
    return f"u-{uuid.uuid4().hex}"


def _text_stream(*texts):
    from core.types import TextChunk

    async def gen(*_a, **_kw):
        for t in texts:
            yield TextChunk(content=t)
    return gen


def _error_stream(message="handler failed", error_type="provider_down"):
    from core.types import ErrorChunk

    async def gen(*_a, **_kw):
        yield ErrorChunk(message=message, provider="test", error_type=error_type)
        if False:
            yield  # pragma: no cover — makes this a generator
    return gen


def _empty_stream():
    async def gen(*_a, **_kw):
        return
        yield  # pragma: no cover — makes this a generator
    return gen


def _raising(exc: Exception):
    async def gen(*_a, **_kw):
        raise exc
        yield  # pragma: no cover — makes this a generator
    return gen


# ---------------------------------------------------------------------------
# resolve_api_key
# ---------------------------------------------------------------------------

class TestResolveApiKey(unittest.TestCase):
    def test_explicit_key_wins(self):
        from core.providers.fallback_state import resolve_api_key
        self.assertEqual(resolve_api_key("explicit-key", "openai"), "explicit-key")

    def test_falls_back_to_stored_key(self):
        from core.providers.fallback_state import resolve_api_key
        from providers import credentials
        credentials.set_api_key("openai", "stored-key")   # isolated in-memory store (conftest)
        self.assertEqual(resolve_api_key(None, "openai"), "stored-key")

    def test_process_env_is_not_consulted(self):
        """A key sitting in os.environ (stale system var, leaked config) must
        never be used -- only the credential store counts."""
        from core.providers.fallback_state import resolve_api_key
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"}, clear=False):
            self.assertEqual(resolve_api_key(None, "openai"), "")

    def test_together_alias_maps_to_together_ai_env(self):
        from core.providers.fallback_state import resolve_api_key
        from providers import credentials
        credentials.set_api_key("together_ai", "tg-key")
        # The legacy "together" alias resolves through the together_ai entry.
        self.assertEqual(resolve_api_key(None, "together"), "tg-key")

    def test_ollama_uses_ollama_host_env_var(self):
        from core.providers.fallback_state import resolve_api_key
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}, clear=False):
            self.assertEqual(resolve_api_key(None, "ollama"), "http://localhost:11434")

    def test_no_key_anywhere_returns_empty_string_not_none(self):
        from core.providers.fallback_state import resolve_api_key
        with patch.dict(os.environ, {}, clear=True):
            result = resolve_api_key(None, "some_unknown_provider")
            self.assertEqual(result, "")


# ---------------------------------------------------------------------------
# build_candidates
# ---------------------------------------------------------------------------

class TestBuildCandidates(unittest.TestCase):
    def test_primary_only_no_fallback_chain(self):
        from core.providers.fallback_state import build_candidates
        candidates = build_candidates("groq", "m", "k", None)
        self.assertEqual(candidates, [{"provider": "groq", "model": "m", "api_key": "k"}])

    def test_primary_plus_fallback_chain_order_preserved(self):
        from core.providers.fallback_state import build_candidates
        candidates = build_candidates(
            "groq", "m", "k",
            [{"provider": "anthropic", "model": "claude", "api_key": "k2"},
             {"provider": "openai", "model": "gpt", "api_key": "k3"}],
        )
        self.assertEqual([c["provider"] for c in candidates], ["groq", "anthropic", "openai"])

    def test_malformed_entries_skipped(self):
        from core.providers.fallback_state import build_candidates
        candidates = build_candidates(
            "groq", "m", "k",
            [{"provider": "", "model": "claude"},   # missing provider
             {"provider": "openai"},                 # missing model
             {"provider": "anthropic", "model": "claude"}],
        )
        self.assertEqual([c["provider"] for c in candidates], ["groq", "anthropic"])

    def test_no_primary_configured_returns_only_fallback_chain(self):
        from core.providers.fallback_state import build_candidates
        candidates = build_candidates(
            "", "", None,
            [{"provider": "anthropic", "model": "claude", "api_key": "k2"}],
        )
        self.assertEqual([c["provider"] for c in candidates], ["anthropic"])


# ---------------------------------------------------------------------------
# filter_cooldown
# ---------------------------------------------------------------------------

class TestFilterCooldown(unittest.IsolatedAsyncioTestCase):
    async def test_noop_when_no_user_id(self):
        from core.providers.fallback_state import filter_cooldown
        candidates = [{"provider": "groq", "model": "m", "api_key": "k"}]
        result = await filter_cooldown(None, candidates)
        self.assertEqual(result, candidates)

    async def test_skips_candidate_in_cooldown(self):
        from core.providers.fallback_state import filter_cooldown, record_cooldown_failure
        user_id = _uid()
        await record_cooldown_failure(user_id, "groq", "m")
        candidates = [
            {"provider": "groq", "model": "m", "api_key": "k"},
            {"provider": "anthropic", "model": "claude", "api_key": "k2"},
        ]
        result = await filter_cooldown(user_id, candidates)
        self.assertEqual([c["provider"] for c in result], ["anthropic"])

    async def test_passes_through_when_nothing_in_cooldown(self):
        from core.providers.fallback_state import filter_cooldown
        user_id = _uid()
        candidates = [{"provider": "groq", "model": "m", "api_key": "k"}]
        result = await filter_cooldown(user_id, candidates)
        self.assertEqual(result, candidates)


# ---------------------------------------------------------------------------
# resolve_and_call_once — Resolve-Attempt-Record tier
# ---------------------------------------------------------------------------

class TestResolveAndCallOnce(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_text(self):
        from core.providers.fallback_call import resolve_and_call_once

        user_id = _uid()
        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream("hello")

        with patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            result = await resolve_and_call_once(
                session={"provider": "groq", "model": "m", "api_key": "k", "fallback_chain": []},
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                user_id=user_id,
                caller_tag="Test",
            )

        self.assertEqual(result, "hello")

    async def test_success_clears_a_stale_cooldown_from_an_earlier_failure(self):
        """Simulates: candidate failed once (recorded), the cooldown TTL
        has since elapsed (so it's attemptable again), and now succeeds —
        the stale cooldown/streak keys must be cleared immediately rather
        than left to expire naturally, matching AgentEngine's own
        _clear_cooldown semantics."""
        from core.providers.fallback_call import resolve_and_call_once
        from core.providers.fallback_state import (
            record_cooldown_failure, is_in_cooldown, _cooldown_key,
        )
        from core.broker import local_broker

        user_id = _uid()
        await record_cooldown_failure(user_id, "groq", "m")
        # Simulate the cooldown TTL having naturally expired (delete just
        # the cooldown key, same technique test_fallback_cooldown_unit.py
        # uses) so this candidate is attemptable again but still has a
        # streak counter that a real success must reset.
        await local_broker.client.delete(_cooldown_key(user_id, "groq", "m"))
        self.assertFalse(await is_in_cooldown(user_id, "groq", "m"))

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream("hello")

        with patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            result = await resolve_and_call_once(
                session={"provider": "groq", "model": "m", "api_key": "k", "fallback_chain": []},
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                user_id=user_id,
                caller_tag="Test",
            )

        self.assertEqual(result, "hello")
        self.assertFalse(await is_in_cooldown(user_id, "groq", "m"))

    async def test_no_provider_configured_raises(self):
        from core.providers.fallback_call import resolve_and_call_once

        with self.assertRaises(RuntimeError):
            await resolve_and_call_once(
                session={},
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="Test",
            )

    async def test_failure_records_cooldown_and_raises_without_trying_next_candidate(self):
        from core.providers.fallback_call import resolve_and_call_once
        from core.providers.fallback_state import is_in_cooldown

        user_id = _uid()
        mock_handler = MagicMock()
        mock_handler.create_message = _error_stream("down", error_type="provider_down")
        fallback_handler_never_called = MagicMock()
        fallback_handler_never_called.create_message = _text_stream("should not be reached")

        handlers_seen = []

        def _cph(**kw):
            handlers_seen.append(kw["provider"])
            return mock_handler

        with patch("utils.handler_utils.create_provider_handler", side_effect=_cph):
            with self.assertRaises(RuntimeError):
                await resolve_and_call_once(
                    session={
                        "provider": "groq", "model": "m", "api_key": "k",
                        "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                    },
                    system_prompt="sys",
                    messages=[{"role": "user", "content": "hi"}],
                    user_id=user_id,
                    caller_tag="Test",
                )

        # Only the primary was tried — this tier does not auto-advance.
        self.assertEqual(handlers_seen, ["groq"])
        self.assertTrue(await is_in_cooldown(user_id, "groq", "m"))

    async def test_next_invocation_after_failure_resolves_to_different_candidate(self):
        """The core V6.8 property: a manual retry (a second, independent
        call) must land on the fallback candidate after the first call
        recorded the primary's failure — proving the "next caller doesn't
        repeat the same failed step" behavior without any auto-advance
        inside a single call."""
        from core.providers.fallback_call import resolve_and_call_once

        user_id = _uid()
        failing_handler = MagicMock()
        failing_handler.create_message = _error_stream("down", error_type="provider_down")
        working_handler = MagicMock()
        working_handler.create_message = _text_stream("fallback worked")

        session = {
            "provider": "groq", "model": "m", "api_key": "k",
            "fallback_chain": [{"provider": "anthropic", "model": "claude", "api_key": "k2"}],
        }

        with patch("utils.handler_utils.create_provider_handler", return_value=failing_handler):
            with self.assertRaises(RuntimeError):
                await resolve_and_call_once(
                    session=session, system_prompt="sys",
                    messages=[{"role": "user", "content": "hi"}],
                    user_id=user_id, caller_tag="Test",
                )

        # Second, independent call — simulates a manual retry / the next
        # background trigger. Primary is now in cooldown from the first
        # call, so this must resolve straight to the fallback candidate.
        seen_providers = []

        def _cph(**kw):
            seen_providers.append(kw["provider"])
            return working_handler

        with patch("utils.handler_utils.create_provider_handler", side_effect=_cph):
            result = await resolve_and_call_once(
                session=session, system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                user_id=user_id, caller_tag="Test",
            )

        self.assertEqual(result, "fallback worked")
        self.assertEqual(seen_providers, ["anthropic"])

    async def test_content_policy_failure_does_not_poison_cooldown(self):
        """A Tier B (non-retryable, fallback_eligible) error like
        content_policy must NOT be recorded into cooldown -- it says
        nothing about whether the provider itself is healthy, and would
        otherwise incorrectly block every OTHER caller sharing this
        (user, provider, model) from ever trying it again until the TTL
        expires."""
        from core.providers.fallback_call import resolve_and_call_once
        from core.providers.fallback_state import is_in_cooldown

        user_id = _uid()
        mock_handler = MagicMock()
        mock_handler.create_message = _error_stream("refused", error_type="content_policy")

        with patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            with self.assertRaises(RuntimeError):
                await resolve_and_call_once(
                    session={"provider": "groq", "model": "m", "api_key": "k", "fallback_chain": []},
                    system_prompt="sys",
                    messages=[{"role": "user", "content": "hi"}],
                    user_id=user_id,
                    caller_tag="Test",
                )

        self.assertFalse(await is_in_cooldown(user_id, "groq", "m"))


# ---------------------------------------------------------------------------
# call_with_full_fallback — Reactive Loop tier
# ---------------------------------------------------------------------------

class TestCallWithFullFallback(unittest.IsolatedAsyncioTestCase):
    async def test_success_no_fallback_needed(self):
        from core.providers.fallback_call import call_with_full_fallback

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream("primary result")

        with patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            result = await call_with_full_fallback(
                session={"provider": "groq", "model": "m", "api_key": "k", "fallback_chain": []},
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="Test",
            )
        self.assertEqual(result, "primary result")

    async def test_tier_a_error_retries_same_candidate_before_advancing(self):
        """provider_down is Tier A (retryable) -- must retry the SAME
        candidate up to its configured max_retries before ever trying the
        fallback_chain."""
        from core.providers.fallback_call import call_with_full_fallback

        call_count = {"n": 0}

        def _make_handler(**kw):
            h = MagicMock()
            if kw["provider"] == "groq":
                call_count["n"] += 1
                if call_count["n"] <= 1:
                    h.create_message = _error_stream("down", error_type="provider_down")
                else:
                    h.create_message = _text_stream("recovered on retry")
            else:
                h.create_message = _text_stream("should not be reached")
            return h

        with patch("utils.handler_utils.create_provider_handler", side_effect=_make_handler), \
             patch("core.providers.fallback_state.backoff_sleep", new=AsyncMock()):
            result = await call_with_full_fallback(
                session={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                },
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="Test",
            )

        self.assertEqual(result, "recovered on retry")
        self.assertEqual(call_count["n"], 2)  # first attempt + 1 retry, same candidate

    async def test_tier_b_error_advances_immediately_without_retry(self):
        """invalid_model is Tier B (not retryable, fallback_eligible) --
        must advance to the next candidate with zero retries on the failed
        one."""
        from core.providers.fallback_call import call_with_full_fallback

        primary = MagicMock()
        primary.create_message = _error_stream("no such model", error_type="invalid_model")
        fallback = MagicMock()
        fallback.create_message = _text_stream("fallback result")

        attempts = []

        def _cph(**kw):
            attempts.append(kw["provider"])
            return primary if kw["provider"] == "groq" else fallback

        with patch("utils.handler_utils.create_provider_handler", side_effect=_cph):
            result = await call_with_full_fallback(
                session={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                },
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="Test",
            )

        self.assertEqual(result, "fallback result")
        self.assertEqual(attempts, ["groq", "anthropic"])  # exactly one attempt each

    async def test_tier_c_error_hard_stops_without_trying_fallback(self):
        """invalid_request is Tier C (not retryable, not fallback_eligible)
        -- must raise immediately without ever trying the fallback_chain,
        since a different provider wouldn't fix a malformed request."""
        from core.providers.fallback_call import call_with_full_fallback

        primary = MagicMock()
        primary.create_message = _error_stream("bad request", error_type="invalid_request")
        fallback_never_called = MagicMock()
        fallback_never_called.create_message = _text_stream("should never be reached")

        attempts = []

        def _cph(**kw):
            attempts.append(kw["provider"])
            return primary if kw["provider"] == "groq" else fallback_never_called

        with patch("utils.handler_utils.create_provider_handler", side_effect=_cph):
            with self.assertRaises(RuntimeError):
                await call_with_full_fallback(
                    session={
                        "provider": "groq", "model": "m", "api_key": "k",
                        "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                    },
                    system_prompt="sys",
                    messages=[{"role": "user", "content": "hi"}],
                    caller_tag="Test",
                )

        self.assertEqual(attempts, ["groq"])  # fallback candidate never attempted

    async def test_exhausted_tier_a_retries_record_cooldown_then_advances(self):
        from core.providers.fallback_call import call_with_full_fallback
        from core.providers.fallback_state import is_in_cooldown

        user_id = _uid()
        primary = MagicMock()
        primary.create_message = _error_stream("down", error_type="provider_down")
        fallback = MagicMock()
        fallback.create_message = _text_stream("fallback result")

        with patch("utils.handler_utils.create_provider_handler",
                    side_effect=lambda **kw: primary if kw["provider"] == "groq" else fallback), \
             patch("core.providers.fallback_state.backoff_sleep", new=AsyncMock()):
            result = await call_with_full_fallback(
                session={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                },
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                user_id=user_id,
                caller_tag="Test",
            )

        self.assertEqual(result, "fallback result")
        self.assertTrue(await is_in_cooldown(user_id, "groq", "m"))

    async def test_all_candidates_exhausted_raises_runtime_error(self):
        from core.providers.fallback_call import call_with_full_fallback

        mock_handler = MagicMock()
        mock_handler.create_message = _error_stream("still down", error_type="provider_down")

        with patch("utils.handler_utils.create_provider_handler", return_value=mock_handler), \
             patch("core.providers.fallback_state.backoff_sleep", new=AsyncMock()):
            with self.assertRaises(RuntimeError) as ctx:
                await call_with_full_fallback(
                    session={
                        "provider": "groq", "model": "m", "api_key": "k",
                        "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                    },
                    system_prompt="sys",
                    messages=[{"role": "user", "content": "hi"}],
                    caller_tag="TestCaller",
                )
        self.assertIn("TestCaller", str(ctx.exception))

    async def test_raised_exception_before_first_chunk_is_classified_same_as_error_chunk(self):
        """A raised exception (e.g. connection refused) building/using the
        handler must be classified and handled the same way as an
        ErrorChunk — both are real failure modes handlers can surface."""
        from core.providers.fallback_call import call_with_full_fallback

        fallback = MagicMock()
        fallback.create_message = _text_stream("fallback result")

        def _cph(**kw):
            if kw["provider"] == "groq":
                raise ConnectionError("connection refused")
            return fallback

        with patch("utils.handler_utils.create_provider_handler", side_effect=_cph):
            result = await call_with_full_fallback(
                session={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                },
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="Test",
            )
        self.assertEqual(result, "fallback result")


if __name__ == "__main__":
    unittest.main(verbosity=2)
