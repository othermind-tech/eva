# tests/test_llm_engine_reroute_unit.py
"""
Unit tests for V6.4 Phase C: VerifierEngine, PlannerEngine, and IntentRouter
rerouted from raw litellm.acompletion() to EVA's standard
create_provider_handler() path. Mocked, no live server, no real API calls.

Covers:
  - Correct routing through create_provider_handler (not litellm directly)
  - No-provider/model-configured fallback (V6.4: no hardcoded default)
  - Handler ErrorChunk / empty-response fallback
  - Happy-path success parsing

Run with:
    cd <project_root>
    python tests/test_llm_engine_reroute_unit.py
    (or: pytest tests/test_llm_engine_reroute_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _text_stream(*texts):
    """Build an async generator yielding TextChunk(s) — mimics a handler's
    create_message() success path."""
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


class TestVerifierEngineReroute(unittest.IsolatedAsyncioTestCase):
    async def test_uses_create_provider_handler_not_litellm(self):
        from core.execution.verifier import verifier_engine

        # V6.8: call_llm_with_fallback records/consults cross-execution
        # cooldown against a real, process-wide local_broker singleton when
        # given a user_id -- a fixed user_id shared across this file's
        # tests would let one test's recorded failure poison another
        # test's candidate resolution. Same fix as
        # test_fallback_cooldown_unit.py's _unique_id() convention.
        uid = f"u-{uuid.uuid4().hex}"

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream('{"passed": true, "summary": "ok", "issues": null}')

        with patch("config.settings.get_user_session",
                    return_value={"provider": "ollama_cloud", "model": "gpt-oss:20b", "api_key": "k"}), \
             patch("utils.handler_utils.create_provider_handler", return_value=mock_handler) as mock_cph, \
             patch("litellm.acompletion", side_effect=AssertionError("must not call litellm directly")):
            result = await verifier_engine.verify(
                step={"index": 0, "title": "t", "description": "d"},
                execution_output="output",
                user_id=uid,
            )

        mock_cph.assert_called_once()
        call_kwargs = mock_cph.call_args.kwargs
        self.assertEqual(call_kwargs["provider"], "ollama_cloud")
        self.assertEqual(call_kwargs["model"], "gpt-oss:20b")
        self.assertEqual(call_kwargs["max_tokens"], 500)  # bumped from 200, see verifier.py
        self.assertEqual(call_kwargs["temperature"], 0.0)
        self.assertTrue(result.passed)

    async def test_no_provider_configured_returns_fallback_without_calling_handler(self):
        from core.execution.verifier import verifier_engine

        uid = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        with patch("config.settings.get_user_session", return_value={}), \
             patch("utils.handler_utils.create_provider_handler") as mock_cph:
            result = await verifier_engine.verify(
                step={"index": 0, "title": "t", "description": "d"},
                execution_output="output",
                user_id=uid,
            )

        mock_cph.assert_not_called()
        self.assertFalse(result.passed)

    async def test_handler_error_chunk_falls_back_to_unverified(self):
        from core.execution.verifier import verifier_engine

        uid = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        mock_handler = MagicMock()
        mock_handler.create_message = _error_stream("provider down")

        with patch("config.settings.get_user_session",
                    return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            result = await verifier_engine.verify(
                step={"index": 0, "title": "t", "description": "d"},
                execution_output="output",
                user_id=uid,
            )

        self.assertFalse(result.passed)
        self.assertIn("unverified", result.summary.lower())

    async def test_bare_passed_true_synthesizes_summary(self):
        # V4.48: the verifier prompt no longer asks the model to generate a
        # summary on the success path (wasted generation tokens -- see
        # prompts/verifier.py). A model that follows instructions sends only
        # {"passed": true}. VerifierEngine must synthesize a deterministic
        # summary from the step title so downstream display (V6.14:
        # StepProgressChunk.verification_summary) is unaffected.
        from core.execution.verifier import verifier_engine

        uid = f"u-{uuid.uuid4().hex}"

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream('{"passed": true}')

        with patch("config.settings.get_user_session",
                    return_value={"provider": "ollama_cloud", "model": "gpt-oss:20b", "api_key": "k"}), \
             patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            result = await verifier_engine.verify(
                step={"index": 0, "title": "Write the report", "description": "d"},
                execution_output="output",
                user_id=uid,
            )

        self.assertTrue(result.passed)
        self.assertEqual(result.summary, "Write the report completed successfully.")
        self.assertIsNone(result.issues)

    async def test_passed_true_with_real_summary_is_preserved(self):
        # If a model doesn't follow the new "omit summary on success"
        # instruction and sends real content anyway, that content must win
        # over the synthesized floor -- this is a floor, not an override.
        from core.execution.verifier import verifier_engine

        uid = f"u-{uuid.uuid4().hex}"

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream(
            '{"passed": true, "summary": "some real text", "issues": null}'
        )

        with patch("config.settings.get_user_session",
                    return_value={"provider": "ollama_cloud", "model": "gpt-oss:20b", "api_key": "k"}), \
             patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            result = await verifier_engine.verify(
                step={"index": 0, "title": "Write the report", "description": "d"},
                execution_output="output",
                user_id=uid,
            )

        self.assertTrue(result.passed)
        self.assertEqual(result.summary, "some real text")

    async def test_passed_false_summary_and_issues_unchanged(self):
        # Regression check: the failure path is completely untouched by the
        # success-path synthesis logic -- summary/issues always come
        # straight from the model, same as before this change.
        from core.execution.verifier import verifier_engine

        uid = f"u-{uuid.uuid4().hex}"

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream(
            '{"passed": false, "summary": "attempted the thing", '
            '"issues": "missing required output file"}'
        )

        with patch("config.settings.get_user_session",
                    return_value={"provider": "ollama_cloud", "model": "gpt-oss:20b", "api_key": "k"}), \
             patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            result = await verifier_engine.verify(
                step={"index": 0, "title": "Write the report", "description": "d"},
                execution_output="output",
                user_id=uid,
            )

        self.assertFalse(result.passed)
        self.assertEqual(result.summary, "attempted the thing")
        self.assertEqual(result.issues, "missing required output file")


class TestPlannerEngineReroute(unittest.IsolatedAsyncioTestCase):
    async def test_generate_uses_create_provider_handler(self):
        from core.execution.planner import planner_engine

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream(
            '{"goal": "test goal", "steps": [{"index": 0, "title": "Step 1", "description": "do it"}]}'
        )

        uid = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        with patch("config.settings.get_user_session",
                    return_value={"provider": "anthropic", "model": "claude-opus-4-8", "api_key": "k"}), \
             patch("utils.handler_utils.create_provider_handler", return_value=mock_handler) as mock_cph, \
             patch("litellm.acompletion", side_effect=AssertionError("must not call litellm directly")):
            plan = await planner_engine.generate(user_message="do something", user_id=uid)

        mock_cph.assert_called_once()
        self.assertEqual(mock_cph.call_args.kwargs["provider"], "anthropic")
        self.assertEqual(plan["goal"], "test goal")
        self.assertEqual(len(plan["steps"]), 1)

    async def test_generate_no_provider_returns_fallback_plan(self):
        from core.execution.planner import planner_engine, _FALLBACK_PLAN

        uid = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        with patch("config.settings.get_user_session", return_value={"provider": None, "model": None}), \
             patch("utils.handler_utils.create_provider_handler") as mock_cph:
            plan = await planner_engine.generate(user_message="do something", user_id=uid)

        mock_cph.assert_not_called()
        self.assertEqual(plan["goal"], _FALLBACK_PLAN["goal"])

    async def test_replan_uses_create_provider_handler(self):
        from core.execution.planner import planner_engine

        uid = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream(
            '[{"title": "Retry differently", "description": "new approach"}]'
        )

        with patch("config.settings.get_user_session",
                    return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("utils.handler_utils.create_provider_handler", return_value=mock_handler) as mock_cph:
            steps = await planner_engine.replan(
                goal="goal",
                completed_steps=[],
                failed_step={"index": 0, "title": "failed step"},
                remaining_steps=[],
                user_id=uid,
            )

        mock_cph.assert_called_once()
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["title"], "Retry differently")

    async def test_replan_no_provider_returns_single_step_fallback(self):
        from core.execution.planner import planner_engine

        uid = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        with patch("config.settings.get_user_session", return_value={}), \
             patch("utils.handler_utils.create_provider_handler") as mock_cph:
            steps = await planner_engine.replan(
                goal="goal",
                completed_steps=[],
                failed_step={"index": 0, "title": "failed step"},
                remaining_steps=[],
                user_id=uid,
            )

        mock_cph.assert_not_called()
        self.assertEqual(len(steps), 1)
        self.assertIn("Retry failed step", steps[0]["title"])


class TestIntentRouterReroute(unittest.IsolatedAsyncioTestCase):
    async def test_llm_classify_uses_create_provider_handler(self):
        from core.execution.intent_router import _llm_classify, ExecutionMode

        mock_handler = MagicMock()
        mock_handler.create_message = _text_stream("PLAN_EXECUTE")

        uid = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        with patch("config.settings.get_user_session",
                    return_value={"provider": "ollama_cloud", "model": "gpt-oss:20b", "api_key": "k"}), \
             patch("utils.handler_utils.create_provider_handler", return_value=mock_handler) as mock_cph, \
             patch("litellm.acompletion", side_effect=AssertionError("must not call litellm directly")):
            mode = await _llm_classify("do a complex task", uid)

        mock_cph.assert_called_once()
        self.assertEqual(mode, ExecutionMode.PLAN_EXECUTE)

    async def test_no_provider_configured_defaults_to_conversational(self):
        from core.execution.intent_router import _llm_classify, ExecutionMode

        uid = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        with patch("config.settings.get_user_session", return_value={}), \
             patch("utils.handler_utils.create_provider_handler") as mock_cph:
            mode = await _llm_classify("ambiguous message", uid)

        mock_cph.assert_not_called()
        self.assertEqual(mode, ExecutionMode.CONVERSATIONAL)

    async def test_handler_error_defaults_to_conversational(self):
        from core.execution.intent_router import _llm_classify, ExecutionMode

        uid = f"u-{uuid.uuid4().hex}"  # V6.8: see note above

        mock_handler = MagicMock()
        mock_handler.create_message = _error_stream("provider down")

        with patch("config.settings.get_user_session",
                    return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("utils.handler_utils.create_provider_handler", return_value=mock_handler):
            mode = await _llm_classify("ambiguous message", uid)

        self.assertEqual(mode, ExecutionMode.CONVERSATIONAL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
