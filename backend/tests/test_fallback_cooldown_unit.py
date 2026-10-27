# tests/test_fallback_cooldown_unit.py
"""
Unit tests for V6.5 Phase 4: cross-execution cooldown / circuit-breaker
for the provider fallback chain.

Background: EVA does NOT use real Redis (verified before designing this).
Cooldown state
is backed by core/broker/local_broker.py's LocalRedisAdapter, an
in-memory, single-process, TTL-aware key-value store. These tests use the
REAL local_broker.client (not mocked) since it's pure in-memory Python
with no I/O -- but because it's a process-wide singleton, every test uses
a unique (user_id, provider, model) tuple (via a per-test UUID) to avoid
any cross-test state pollution.

Covers:
  - core.agent_engine._is_in_cooldown / _record_cooldown_failure /
    _clear_cooldown directly: basic set/check/clear, escalating cooldown
    duration on repeated failures, the cap, safe no-op clearing something
    that was never recorded.
  - AgentEngine._gen_with_fallback() end-to-end: a Tier A failure that
    exhausts retries records a cooldown; a SEPARATE AgentEngine instance
    (simulating the next execution) correctly skips that candidate
    without attempting it; Tier B/C failures do NOT record a cooldown; a
    genuine success clears any stale cooldown; cooldown is a no-op when
    there's no execution context (no user_id available).

Mocked only where necessary (backoff sleep, to keep retry-exhaustion
tests fast) -- the cooldown store itself is real.

Run with:
    cd <project_root>
    python tests/test_fallback_cooldown_unit.py
    (or: pytest tests/test_fallback_cooldown_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import uuid
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


def _unique_id() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


def _build_agent_with_execution(user_id, primary, fallback_entries):
    """Construct an AgentEngine with a mocked primary handler, a pre-built
    fallback handler list, and self.execution set with the given user_id
    -- needed for cooldown scoping (getattr(self.execution, 'user_id')).
    """
    from core.agent_engine import AgentEngine

    primary_provider, primary_model = primary
    primary_handler = MagicMock()
    fallback_handlers = [MagicMock() for _ in fallback_entries]

    execution = MagicMock()
    execution.user_id = user_id

    with patch("core.agent_engine.create_provider_handler", return_value=primary_handler):
        agent = AgentEngine(
            provider=primary_provider,
            api_key="k1",
            model=primary_model,
            reasoning=None,
            execution=execution,
            fallback_chain=[
                {"provider": p, "model": m, "api_key": "k"} for p, m in fallback_entries
            ],
        )

    agent._fallback_handlers = [
        (h, p, m) for h, (p, m) in zip(fallback_handlers, fallback_entries)
    ]
    return agent, primary_handler, fallback_handlers


class TestCooldownHelpersDirect(unittest.IsolatedAsyncioTestCase):
    """Direct tests of the cooldown helpers against the real (in-memory)
    local_broker.client -- unique IDs per test avoid cross-test pollution
    on this process-wide singleton."""

    async def test_not_in_cooldown_initially(self):
        from core.agent_engine import _is_in_cooldown

        user_id, provider, model = _unique_id(), "provider_a", "model_1"
        self.assertFalse(await _is_in_cooldown(user_id, provider, model))

    async def test_record_failure_puts_it_in_cooldown(self):
        from core.agent_engine import _is_in_cooldown, _record_cooldown_failure

        user_id, provider, model = _unique_id(), "provider_a", "model_1"
        await _record_cooldown_failure(user_id, provider, model)
        self.assertTrue(await _is_in_cooldown(user_id, provider, model))

    async def test_clear_cooldown_removes_it(self):
        from core.agent_engine import _is_in_cooldown, _record_cooldown_failure, _clear_cooldown

        user_id, provider, model = _unique_id(), "provider_a", "model_1"
        await _record_cooldown_failure(user_id, provider, model)
        self.assertTrue(await _is_in_cooldown(user_id, provider, model))

        await _clear_cooldown(user_id, provider, model)
        self.assertFalse(await _is_in_cooldown(user_id, provider, model))

    async def test_clear_cooldown_is_safe_when_nothing_was_ever_recorded(self):
        from core.agent_engine import _clear_cooldown

        user_id, provider, model = _unique_id(), "provider_a", "model_1"
        await _clear_cooldown(user_id, provider, model)  # must not raise

    async def test_different_users_do_not_interfere(self):
        from core.agent_engine import _is_in_cooldown, _record_cooldown_failure

        provider, model = "provider_a", "model_1"
        user_a, user_b = _unique_id(), _unique_id()

        await _record_cooldown_failure(user_a, provider, model)

        self.assertTrue(await _is_in_cooldown(user_a, provider, model))
        self.assertFalse(await _is_in_cooldown(user_b, provider, model))

    async def test_escalating_cooldown_duration_on_repeated_failures(self):
        """Consecutive failures for the same (user, provider, model) must
        produce a strictly increasing cooldown TTL, up to the cap."""
        from core.broker import local_broker
        from core.agent_engine import _record_cooldown_failure, _cooldown_key

        user_id, provider, model = _unique_id(), "provider_a", "model_1"
        key = _cooldown_key(user_id, provider, model)

        ttls = []
        real_set = local_broker.client.set

        async def _capture_set(k, v, ex=None, **kw):
            if k == key:
                ttls.append(ex)
            return await real_set(k, v, ex=ex, **kw)

        with patch.object(local_broker.client, "set", side_effect=_capture_set):
            await _record_cooldown_failure(user_id, provider, model)
            await _record_cooldown_failure(user_id, provider, model)
            await _record_cooldown_failure(user_id, provider, model)

        self.assertEqual(len(ttls), 3)
        self.assertLess(ttls[0], ttls[1])
        self.assertLess(ttls[1], ttls[2])

    async def test_cooldown_cap_respected(self):
        """After enough consecutive failures, the TTL must stop growing
        past FALLBACK_COOLDOWN_MAX_SECONDS."""
        from core.broker import local_broker
        from core.agent_engine import (
            _record_cooldown_failure, _cooldown_key, _FALLBACK_COOLDOWN_MAX_SECONDS,
        )

        user_id, provider, model = _unique_id(), "provider_a", "model_1"
        key = _cooldown_key(user_id, provider, model)

        ttls = []
        real_set = local_broker.client.set

        async def _capture_set(k, v, ex=None, **kw):
            if k == key:
                ttls.append(ex)
            return await real_set(k, v, ex=ex, **kw)

        with patch.object(local_broker.client, "set", side_effect=_capture_set):
            for _ in range(10):  # far more than enough to hit the cap
                await _record_cooldown_failure(user_id, provider, model)

        self.assertTrue(all(t <= _FALLBACK_COOLDOWN_MAX_SECONDS for t in ttls))
        self.assertEqual(ttls[-1], int(_FALLBACK_COOLDOWN_MAX_SECONDS))


class TestAgentEngineCooldownIntegration(unittest.IsolatedAsyncioTestCase):
    """End-to-end: cooldown recorded by one execution must affect a
    SEPARATE AgentEngine instance representing the next execution."""

    async def asyncSetUp(self):
        self._backoff_patcher = patch("core.agent_engine._backoff_sleep", new=AsyncMock())
        self._backoff_patcher.start()

    async def asyncTearDown(self):
        self._backoff_patcher.stop()

    async def test_exhausted_tier_a_failure_records_cooldown(self):
        from core.agent_engine import _is_in_cooldown

        user_id = _unique_id()
        agent, primary_handler, (fb,) = _build_agent_with_execution(
            user_id,
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary_handler.create_message = _error_stream("rate_limit")
        fb.create_message = _text_stream("fallback succeeded")

        await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        self.assertTrue(await _is_in_cooldown(user_id, "provider_a", "model_1"))
        # The fallback candidate that actually succeeded must NOT be cooled down.
        self.assertFalse(await _is_in_cooldown(user_id, "provider_b", "model_2"))

    async def test_cooldown_causes_candidate_to_be_skipped_in_next_execution(self):
        user_id = _unique_id()

        # "Execution 1" — primary fails with rate_limit, exhausts retries,
        # falls back successfully. This records a cooldown for provider_a.
        agent1, primary1, (fb1,) = _build_agent_with_execution(
            user_id,
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary1.create_message = _error_stream("rate_limit")
        fb1.create_message = _text_stream("fallback succeeded")
        await _collect(agent1._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        # "Execution 2" — a brand new AgentEngine, same user, same primary.
        # provider_a must be skipped entirely (cooldown), never attempted --
        # if it WERE attempted this would raise via primary2.create_message.
        agent2, primary2, (fb2,) = _build_agent_with_execution(
            user_id,
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )

        def _should_never_be_called(*a, **kw):
            raise AssertionError("primary2 must not be attempted -- provider_a is in cooldown")
        primary2.create_message = _should_never_be_called
        fb2.create_message = _text_stream("execution 2 also recovered via fallback")

        chunks = await _collect(agent2._gen_with_fallback(messages=[], stream=True, execution_id="e2"))

        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertIn("execution 2 also recovered via fallback", texts)
        self.assertEqual(agent2.provider, "provider_b")

    async def test_cooldown_skip_still_emits_live_notice(self):
        """A candidate silently skipped via cooldown (no active failure in
        THIS execution) must still produce a live ProviderFallbackChunk --
        the emission point lives at "attempt_idx > 0 and succeeded", which
        is true regardless of whether attempt_idx got there via a failure
        or a cooldown skip. This is what makes the live notice correctly
        cover both cases with a single emission point."""
        from core.types import ProviderFallbackChunk
        from core.agent_engine import _record_cooldown_failure

        user_id = _unique_id()
        # Simulate a cooldown already recorded from a PREVIOUS execution --
        # no active failure happens in THIS execution at all.
        await _record_cooldown_failure(user_id, "provider_a", "model_1")

        agent, primary_handler, (fb,) = _build_agent_with_execution(
            user_id,
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )

        def _should_never_be_called(*a, **kw):
            raise AssertionError("primary must not be attempted -- provider_a is in cooldown")
        primary_handler.create_message = _should_never_be_called
        fb.create_message = _text_stream("recovered via cooldown-skip")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        fallback_chunks = [c for c in chunks if isinstance(c, ProviderFallbackChunk)]
        self.assertEqual(len(fallback_chunks), 1)
        self.assertEqual(fallback_chunks[0].provider, "provider_b")
        self.assertEqual(fallback_chunks[0].model, "model_2")

    async def test_tier_b_failure_does_not_record_cooldown(self):
        from core.agent_engine import _is_in_cooldown

        user_id = _unique_id()
        agent, primary_handler, (fb,) = _build_agent_with_execution(
            user_id,
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary_handler.create_message = _error_stream("invalid_model")
        fb.create_message = _text_stream("fallback succeeded")

        await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        self.assertFalse(await _is_in_cooldown(user_id, "provider_a", "model_1"))

    async def test_hard_stop_does_not_record_cooldown(self):
        from core.agent_engine import _is_in_cooldown

        user_id = _unique_id()
        agent, primary_handler, (fb,) = _build_agent_with_execution(
            user_id,
            primary=("provider_a", "model_1"),
            fallback_entries=[("provider_b", "model_2")],
        )
        primary_handler.create_message = _error_stream("internal")
        fb.create_message = _text_stream("SHOULD NEVER APPEAR")

        await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        self.assertFalse(await _is_in_cooldown(user_id, "provider_a", "model_1"))

    async def test_successful_candidate_clears_stale_failure_streak(self):
        """clear_cooldown() must wipe BOTH the cooldown key and the longer-
        lived streak counter on success -- not just the cooldown. The
        streak survives past an individual cooldown's own TTL expiry by
        design (so a failure right after expiry still escalates); a
        success in between must reset it, otherwise a candidate that's
        been healthy for a while would still face an escalated cooldown
        duration on its next unrelated failure, based on stale history."""
        from core.agent_engine import (
            _record_cooldown_failure, _cooldown_key, _cooldown_streak_key,
        )
        from core.broker import local_broker

        user_id = _unique_id()
        # Two past cooldown-triggering failures (streak=2). Simulate the
        # cooldown itself having since naturally expired by deleting just
        # that key -- the streak key has a much longer TTL and survives.
        await _record_cooldown_failure(user_id, "provider_a", "model_1")
        await _record_cooldown_failure(user_id, "provider_a", "model_1")
        await local_broker.client.delete(_cooldown_key(user_id, "provider_a", "model_1"))

        streak_before = await local_broker.client.get(_cooldown_streak_key(user_id, "provider_a", "model_1"))
        self.assertEqual(streak_before, "2")

        agent, primary_handler, _ = _build_agent_with_execution(
            user_id, primary=("provider_a", "model_1"), fallback_entries=[],
        )
        primary_handler.create_message = _text_stream("healthy again")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))
        texts = [c.content for c in chunks if hasattr(c, "content")]
        self.assertIn("healthy again", texts)

        streak_after = await local_broker.client.get(_cooldown_streak_key(user_id, "provider_a", "model_1"))
        self.assertIsNone(streak_after)

    async def test_primary_in_cooldown_with_no_fallback_exhausts_cleanly(self):
        """If the only candidate (primary, no fallback_chain) is in
        cooldown, the loop must not silently produce nothing -- it should
        exhaust cleanly via the existing "all candidates failed" path,
        without making a single real attempt."""
        from core.agent_engine import _record_cooldown_failure

        user_id = _unique_id()
        await _record_cooldown_failure(user_id, "provider_a", "model_1")

        agent, primary_handler, _ = _build_agent_with_execution(
            user_id,
            primary=("provider_a", "model_1"),
            fallback_entries=[],
        )

        def _should_never_be_called(*a, **kw):
            raise AssertionError("primary must not be attempted -- it's in cooldown")
        primary_handler.create_message = _should_never_be_called

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        self.assertEqual(len(chunks), 1)
        self.assertIn("failed", chunks[0].message.lower())

    async def test_no_execution_context_cooldown_is_noop(self):
        """AgentEngine with execution=None (e.g. constructed directly in a
        test/utility context) must not error, and cooldown must simply
        never be consulted."""
        from core.agent_engine import AgentEngine

        primary_handler = MagicMock()
        with patch("core.agent_engine.create_provider_handler", return_value=primary_handler):
            agent = AgentEngine(
                provider="provider_a",
                api_key="k1",
                model="model_1",
                reasoning=None,
                execution=None,
                fallback_chain=[],
            )
        primary_handler.create_message = _error_stream("rate_limit")

        chunks = await _collect(agent._gen_with_fallback(messages=[], stream=True, execution_id="e1"))

        # No fallback candidates and no crash -- exhausted chain yields the
        # primary's own error as the final result.
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].error_type, "rate_limit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
