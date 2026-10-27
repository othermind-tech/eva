# tests/test_fallback_coverage_unit.py
"""
Unit tests for V6.5 Phase 3: fallback_chain coverage fix.

Before this phase, `session.fallback_chain` was silently NOT applied to
4+ call sites despite being user-configured: tools/default/delegate_tool.py,
tools/default/parallel_delegate_tool.py, core/daemons/runner.py (all three
build an AgentEngine directly and just needed `fallback_chain=` threaded
through), and the 3 V6.4-rerouted meta-LLM calls (verifier.py, planner.py,
intent_router.py) which call create_provider_handler() directly with no
AgentEngine and therefore no fallback awareness at all -- these needed a
new, lighter-weight fallback mechanism: core/providers/fallback_call.py.

Covers:
  - call_llm_with_fallback() (the new shared helper) directly: primary
    success, primary-fails-fallback-succeeds, all-candidates-fail raises,
    no-provider-configured raises, malformed fallback_chain entries are
    skipped, an empty response (no ErrorChunk) still counts as a failure.
  - VerifierEngine / PlannerEngine (generate + replan) / IntentRouter all
    genuinely recover via session.fallback_chain end-to-end.
  - delegate_tool.py / parallel_delegate_tool.py / core/daemons/runner.py
    all pass session.fallback_chain through to AgentEngine's constructor.

Mocked throughout -- no live server, no real API calls.

Run with:
    cd <project_root>
    python tests/test_fallback_coverage_unit.py
    (or: pytest tests/test_fallback_coverage_unit.py -v)
"""

from __future__ import annotations

import pytest

import sys
import os
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from core.broker import local_broker as _local_broker  # the singleton; patch.object avoids the module-vs-instance ambiguity of the string path (resolves differently on py3.10 vs 3.11)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _text_stream(*texts):
    from core.types import TextChunk

    async def gen(*_a, **_kw):
        for t in texts:
            yield TextChunk(content=t)
    return gen


def _error_stream(message="handler failed"):
    from core.types import ErrorChunk

    async def gen(*_a, **_kw):
        yield ErrorChunk(message=message, provider="test", error_type="provider_down")
        if False:
            yield  # pragma: no cover — makes this a generator
    return gen


def _empty_stream():
    async def gen(*_a, **_kw):
        return
        yield  # pragma: no cover — makes this a generator
    return gen


def _sequence_stream(*factories):
    state = {"n": 0}

    def create_message(*args, **kwargs):
        idx = min(state["n"], len(factories) - 1)
        state["n"] += 1
        return factories[idx](*args, **kwargs)

    return create_message


class TestCallLLMWithFallback(unittest.IsolatedAsyncioTestCase):
    """Direct tests of core/providers/fallback_call.py's shared helper."""

    async def test_primary_succeeds_no_fallback_needed(self):
        from core.providers.fallback_call import call_llm_with_fallback

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream("primary result")

        with patch("utils.handler_utils.create_provider_handler", return_value=mock_handler) as mock_cph:
            result = await call_llm_with_fallback(
                session={"provider": "groq", "model": "m", "api_key": "k", "fallback_chain": []},
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="TestCaller",
            )

        self.assertEqual(result, "primary result")
        mock_cph.assert_called_once()
        self.assertEqual(mock_cph.call_args.kwargs["provider"], "groq")

    async def test_primary_fails_fallback_succeeds(self):
        from core.providers.fallback_call import call_llm_with_fallback

        primary_handler = MagicMock()
        primary_handler.create_message = _error_stream("provider down")
        fallback_handler = MagicMock()
        fallback_handler.create_message = _text_stream("fallback result")

        handlers = iter([primary_handler, fallback_handler])
        with patch("utils.handler_utils.create_provider_handler", side_effect=lambda **kw: next(handlers)):
            result = await call_llm_with_fallback(
                session={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude", "api_key": "k2"}],
                },
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="TestCaller",
            )

        self.assertEqual(result, "fallback result")

    async def test_primary_error_chunk_fallback_succeeds(self):
        """Same as above but the primary raises an exception building the
        handler rather than returning an ErrorChunk -- both paths must
        advance to the next candidate."""
        from core.providers.fallback_call import call_llm_with_fallback

        fallback_handler = MagicMock()
        fallback_handler.create_message = _text_stream("fallback result")

        def _cph(**kw):
            if kw["provider"] == "groq":
                raise ConnectionError("connection refused")
            return fallback_handler

        with patch("utils.handler_utils.create_provider_handler", side_effect=_cph):
            result = await call_llm_with_fallback(
                session={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                },
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="TestCaller",
            )

        self.assertEqual(result, "fallback result")

    async def test_all_candidates_fail_raises_runtime_error(self):
        from core.providers.fallback_call import call_llm_with_fallback

        mock_handler = MagicMock()
        mock_handler.create_message = _error_stream("still down")

        with patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            with self.assertRaises(RuntimeError) as ctx:
                await call_llm_with_fallback(
                    session={
                        "provider": "groq", "model": "m", "api_key": "k",
                        "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                    },
                    system_prompt="sys",
                    messages=[{"role": "user", "content": "hi"}],
                    caller_tag="TestCaller",
                )
        self.assertIn("TestCaller", str(ctx.exception))

    async def test_no_provider_configured_raises_runtime_error(self):
        from core.providers.fallback_call import call_llm_with_fallback

        with patch("utils.handler_utils.create_provider_handler") as mock_cph:
            with self.assertRaises(RuntimeError):
                await call_llm_with_fallback(
                    session={},
                    system_prompt="sys",
                    messages=[{"role": "user", "content": "hi"}],
                    caller_tag="TestCaller",
                )
        mock_cph.assert_not_called()

    async def test_malformed_fallback_chain_entries_are_skipped(self):
        """An entry missing provider or model must not be treated as a
        candidate at all -- confirms the list-comprehension filter."""
        from core.providers.fallback_call import call_llm_with_fallback

        good_handler = MagicMock()
        good_handler.create_message = _text_stream("good result")

        seen_providers = []

        def _cph(**kw):
            seen_providers.append(kw["provider"])
            if kw["provider"] == "groq":
                raise ConnectionError("down")
            return good_handler

        with patch("utils.handler_utils.create_provider_handler", side_effect=_cph):
            result = await call_llm_with_fallback(
                session={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [
                        {"provider": "", "model": "claude"},       # missing provider -- skipped
                        {"provider": "openai"},                    # missing model -- skipped
                        {"provider": "anthropic", "model": "claude"},  # valid
                    ],
                },
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="TestCaller",
            )

        self.assertEqual(result, "good result")
        self.assertEqual(seen_providers, ["groq", "anthropic"])

    async def test_empty_response_counts_as_failure_and_falls_back(self):
        from core.providers.fallback_call import call_llm_with_fallback

        primary_handler = MagicMock()
        primary_handler.create_message = _empty_stream()
        fallback_handler = MagicMock()
        fallback_handler.create_message = _text_stream("fallback result")

        handlers = iter([primary_handler, fallback_handler])
        with patch("utils.handler_utils.create_provider_handler", side_effect=lambda **kw: next(handlers)):
            result = await call_llm_with_fallback(
                session={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                },
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                caller_tag="TestCaller",
            )

        self.assertEqual(result, "fallback result")


class TestVerifierPlannerIntentRouterRecoverViaFallback(unittest.IsolatedAsyncioTestCase):
    """End-to-end: a failing primary + working fallback_chain entry must
    let each of the 3 PEV-support engines actually recover, not just fall
    through to their own safe default."""

    async def test_verifier_recovers_via_fallback_chain(self):
        from core.execution.verifier import verifier_engine

        # V6.8: call_llm_with_fallback now records/consults cross-execution
        # cooldown when given a user_id, backed by a real, process-wide
        # local_broker singleton (not mocked here) -- a fixed user_id shared
        # across this class's tests would let one test's recorded failure
        # poison the next test's candidate resolution. Same fix as
        # test_fallback_cooldown_unit.py's _unique_id() convention.
        user_id = f"u-{uuid.uuid4().hex}"

        primary = MagicMock()
        primary.create_message = _error_stream("down")
        fallback = MagicMock()
        fallback.create_message = _text_stream('{"passed": true, "summary": "ok", "issues": null}')

        handlers = iter([primary, fallback])
        with patch("config.settings.get_user_session", return_value={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                }), \
             patch("utils.handler_utils.create_provider_handler", side_effect=lambda **kw: next(handlers)):
            result = await verifier_engine.verify(
                step={"index": 0, "title": "t", "description": "d"},
                execution_output="output",
                user_id=user_id,
            )

        self.assertTrue(result.passed)

    async def test_planner_generate_recovers_via_fallback_chain(self):
        from core.execution.planner import planner_engine

        user_id = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        primary = MagicMock()
        primary.create_message = _error_stream("down")
        fallback = MagicMock()
        fallback.create_message = _text_stream(
            '{"goal": "recovered goal", "steps": [{"index": 0, "title": "s", "description": "d"}]}'
        )

        handlers = iter([primary, fallback])
        with patch("config.settings.get_user_session", return_value={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                }), \
             patch("utils.handler_utils.create_provider_handler", side_effect=lambda **kw: next(handlers)):
            plan = await planner_engine.generate(user_message="do something", user_id=user_id)

        self.assertEqual(plan["goal"], "recovered goal")

    async def test_planner_replan_recovers_via_fallback_chain(self):
        from core.execution.planner import planner_engine

        user_id = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        primary = MagicMock()
        primary.create_message = _error_stream("down")
        fallback = MagicMock()
        fallback.create_message = _text_stream('[{"title": "recovered step", "description": "d"}]')

        handlers = iter([primary, fallback])
        with patch("config.settings.get_user_session", return_value={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                }), \
             patch("utils.handler_utils.create_provider_handler", side_effect=lambda **kw: next(handlers)):
            steps = await planner_engine.replan(
                goal="g",
                completed_steps=[],
                failed_step={"index": 0, "title": "failed", "description": "d"},
                remaining_steps=[],
                user_id=user_id,
            )

        self.assertEqual(steps[0]["title"], "recovered step")

    async def test_planner_revise_recovers_via_fallback_chain(self):
        from core.execution.planner import planner_engine

        user_id = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        primary = MagicMock()
        primary.create_message = _error_stream("down")
        fallback = MagicMock()
        fallback.create_message = _text_stream(
            '{"goal": "revised goal", "steps": [{"index": 0, "title": "s", "description": "d"}]}'
        )

        handlers = iter([primary, fallback])
        with patch("config.settings.get_user_session", return_value={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                }), \
             patch("utils.handler_utils.create_provider_handler", side_effect=lambda **kw: next(handlers)):
            plan = await planner_engine.revise(
                goal="original goal",
                rejected_steps=[{"index": 0, "title": "orig step", "description": "d"}],
                feedback="too narrow, cover edge cases",
                user_id=user_id,
            )

        self.assertEqual(plan["goal"], "revised goal")

    async def test_intent_router_recovers_via_fallback_chain(self):
        from core.execution.intent_router import _llm_classify, ExecutionMode

        user_id = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        primary = MagicMock()
        primary.create_message = _error_stream("down")
        fallback = MagicMock()
        fallback.create_message = _text_stream("PLAN_EXECUTE")

        handlers = iter([primary, fallback])
        with patch("config.settings.get_user_session", return_value={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                }), \
             patch("utils.handler_utils.create_provider_handler", side_effect=lambda **kw: next(handlers)):
            mode = await _llm_classify("build me a web scraper", user_id)

        self.assertEqual(mode, ExecutionMode.PLAN_EXECUTE)


@pytest.mark.usefixtures("stored_provider_keys")
class TestAgentEngineToolsThreadFallbackChain(unittest.IsolatedAsyncioTestCase):
    """delegate_tool.py / parallel_delegate_tool.py / daemons/runner.py must
    all pass session.fallback_chain through to AgentEngine's constructor."""

    async def test_delegate_tool_passes_session_fallback_chain(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        tool = DefaultToolRegistry.all()["delegate_to_agent"]()
        execution = MagicMock()
        execution.user_id = "u1"
        execution.execution_id = "e1"
        execution.conversation_id = "c1"
        execution.parent_project_id = None
        execution.shadow = None
        tool.execution = execution

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)  # dynamic subagent

        mock_agent_instance = MagicMock()
        mock_agent_instance.call = MagicMock(return_value=_empty_stream()())

        with patch("config.settings.get_user_session", return_value={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                }), \
             patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("core.agent_engine.AgentEngine", return_value=mock_agent_instance) as mock_agent_cls, \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"), \
             patch("core.event_bus.event_bus.publish"):
            await tool.run({"agent_name": "helper", "task": "do x"})

        mock_agent_cls.assert_called_once()
        self.assertEqual(
            mock_agent_cls.call_args.kwargs["fallback_chain"],
            [{"provider": "anthropic", "model": "claude"}],
        )

    async def test_parallel_delegate_tool_passes_session_fallback_chain(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        tool = DefaultToolRegistry.all()["parallel_delegate"]()
        execution = MagicMock()
        execution.user_id = "u1"
        execution.execution_id = "e1"
        execution.shadow = None
        execution.blackboard = None
        execution.cancel_token = None  # MagicMock() default is truthy -- would trip the cancel-check
        tool.execution = execution

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)  # dynamic subagent

        mock_agent_instance = MagicMock()
        mock_agent_instance.call = MagicMock(return_value=_empty_stream()())

        with patch("config.settings.get_user_session", return_value={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                }), \
             patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("core.agent_engine.AgentEngine", return_value=mock_agent_instance) as mock_agent_cls:
            await tool.run({"tasks": [{"agent_name": "helper", "task": "do x"}]})

        mock_agent_cls.assert_called_once()
        self.assertEqual(
            mock_agent_cls.call_args.kwargs["fallback_chain"],
            [{"provider": "anthropic", "model": "claude"}],
        )

    async def test_daemon_runner_passes_session_fallback_chain(self):
        from core.daemons.runner import run_daemon

        mock_daemon = MagicMock()
        mock_daemon.id = "daemon-1"
        mock_daemon.user_id = "u1"
        mock_daemon.name = "test-daemon"
        mock_daemon.description = "desc"
        mock_daemon.model = None
        mock_daemon.provider = None
        mock_daemon.trigger_message = None
        mock_daemon.system_prompt = None
        mock_daemon.project_id = None
        mock_daemon.tools = []
        mock_daemon.max_steps = 5

        mock_metrics_db = AsyncMock()
        mock_agent_instance = MagicMock()
        mock_agent_instance.call = MagicMock(return_value=_empty_stream()())

        with patch("core.daemons.runner.ConversationJournal"), \
             patch("core.execution.redis_registry.redis_execution_registry") as mock_redis_reg, \
             patch("core.db.session.AsyncSessionLocal") as mock_session_local, \
             patch("core.db.repository.ExecutionMetricsRepo") as mock_metrics_repo, \
             patch("core.blackboard.ExecutionBlackboard"), \
             patch.object(_local_broker, "connect", new=AsyncMock()), \
             patch("config.settings.get_user_session", return_value={
                    "provider": "groq", "model": "m", "api_key": "k",
                    "fallback_chain": [{"provider": "anthropic", "model": "claude"}],
                }), \
             patch("core.agent_engine.AgentEngine", return_value=mock_agent_instance) as mock_agent_cls, \
             patch("core.events_publisher.publish_user_event", new=AsyncMock()):

            mock_redis_reg.create_execution = AsyncMock()
            mock_redis_reg.mark_execution_running = AsyncMock()
            mock_redis_reg.mark_execution_finished = AsyncMock()
            mock_redis_reg.mark_execution_failed = AsyncMock()
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_metrics_db)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_metrics_repo.upsert = AsyncMock()
            mock_metrics_repo.purge_old_daemon_runs = AsyncMock()

            await run_daemon(daemon=mock_daemon, execution_id="exec-1")

        mock_agent_cls.assert_called_once()
        self.assertEqual(
            mock_agent_cls.call_args.kwargs["fallback_chain"],
            [{"provider": "anthropic", "model": "claude"}],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
