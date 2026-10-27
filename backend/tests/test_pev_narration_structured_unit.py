# tests/test_pev_narration_structured_unit.py
"""
Unit tests for the V6.14 PEV narration restructure: pev_runner.py no longer
mixes hand-authored markdown narration into the TextChunk answer stream --
every phase transition now travels as a structured StepProgressChunk (or,
for genuine error conditions, an ErrorChunk). See core/types.py's
StepProgressChunk docstring and core/execution/pev_runner.py's module
docstring for the full design.

Covers:
  - StepProgressChunk's new fields (plan_steps, verification_summary,
    verification_issues, replan_reason, error_detail, steps_completed,
    steps_failed, replan_count) and new step_status values
  - pev_runner.py source no longer contains any narrative TextChunk yields
  - _plan_steps_payload() field extraction
  - api/executions.py's _trace_from_shadow_buffer() step_progress handling
    (the fix that makes this data visible via GET /trace, not just the
    live SSE stream -- without it this whole change would be a silent
    regression for anything polling the trace endpoint)
  - V6.14.1 regression: run_pev_execution()'s consumer loop only checks
    cancel_token and performs the CANCELLED FSM transition *inside* its
    `async for chunk in buffered.stream_and_buffer()` loop body. A
    generator that sets the flag and returns with no further yield exits
    via plain StopAsyncIteration and silently skips that transition. The
    first pass of the V6.14 narration removal broke exactly this (caught
    live via test_apis.py section 14i: rejecting a plan left the execution
    stuck at awaiting_plan_approval forever instead of transitioning to
    cancelled) because the old narrative TextChunk yields at each
    cancel-and-return site were unintentionally pumping the loop one more
    time. Fixed by (a) yielding a StepProgressChunk(step_status="cancelled")
    at every such site, and (b) a defensive post-loop check in
    run_pev_execution() itself as a backstop against any future site
    making the same mistake.

Run with:
    cd <project_root>
    pytest tests/test_pev_narration_structured_unit.py -v
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from core.broker import local_broker as _local_broker  # the singleton; patch.object avoids the module-vs-instance ambiguity of the string path (resolves differently on py3.10 vs 3.11)


# ===========================================================================
# StepProgressChunk schema
# ===========================================================================

class TestStepProgressChunkNewFields(unittest.TestCase):

    def test_new_fields_exist_and_default_to_none(self):
        from core.types import StepProgressChunk
        chunk = StepProgressChunk()
        for field in (
            "plan_steps", "verification_summary", "verification_issues",
            "replan_reason", "error_detail", "steps_completed",
            "steps_failed", "replan_count",
        ):
            self.assertTrue(hasattr(chunk, field), f"missing field: {field}")
            self.assertIsNone(getattr(chunk, field))

    def test_accepts_plan_steps_payload(self):
        from core.types import StepProgressChunk
        steps = [{"index": 0, "title": "Edited Step", "description": "d", "tool_hint": None}]
        chunk = StepProgressChunk(step_status="planning", plan_steps=steps)
        self.assertEqual(chunk.plan_steps, steps)

    def test_accepts_plan_complete_summary_fields(self):
        from core.types import StepProgressChunk
        chunk = StepProgressChunk(
            step_status="plan_complete",
            steps_completed=2, steps_failed=1, replan_count=1,
        )
        self.assertEqual(chunk.steps_completed, 2)
        self.assertEqual(chunk.steps_failed, 1)
        self.assertEqual(chunk.replan_count, 1)

    def test_type_defaults_to_step_progress(self):
        from core.types import StepProgressChunk, ChunkType
        chunk = StepProgressChunk()
        self.assertEqual(chunk.type, ChunkType.STEP_PROGRESS)


# ===========================================================================
# pev_runner.py source no longer narrates via TextChunk
# ===========================================================================

class TestPevRunnerNoNarrativeTextChunks(unittest.TestCase):
    """
    Source-inspection pins (matching this file's established convention,
    e.g. TestB082MultiActiveProjects) confirming the 19 hand-authored
    narration/error TextChunk yields identified in the V6.14 audit are
    actually gone, not just moved around.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = Path("core/execution/pev_runner.py").read_text(encoding="utf-8")

    def test_no_planning_narration(self):
        self.assertNotIn('TextChunk(content="## 🤔 Planning', self.src)

    def test_no_plan_render_narration(self):
        self.assertNotIn("format_plan_for_display", self.src)

    def test_no_verifying_narration(self):
        # The word "verifying" legitimately still appears (step_status value,
        # this file's own explanatory module docstring quoting the old
        # pattern for context) -- what must be gone is the actual TextChunk
        # yield call that used to render it.
        self.assertNotIn('yield TextChunk(content="\\n\\n_🔍 Verifying step', self.src)

    def test_no_adaptive_replanning_narration(self):
        # "Adaptive Re-planning" legitimately still appears as a section-
        # header comment (# Adaptive Re-planning (only when steps remain))
        # and in this file's own explanatory docstring -- what must be gone
        # is the old TextChunk narration wrapping "**Adaptive Re-planning**".
        self.assertNotIn("**Adaptive Re-planning**", self.src)

    def test_no_max_replan_narration_as_text(self):
        # The phrase itself is now allowed to exist as a replan_reason string
        # (structured field), but must not be wrapped in a TextChunk(...) call.
        self.assertNotIn('TextChunk(\n                        content=(\n                            f"\\n\\n_⚠️ **Max re-plan', self.src)

    def test_no_execution_complete_narration(self):
        self.assertNotIn("## ✅ Execution Complete", self.src)

    def test_no_plan_approved_rejected_edited_text_notices(self):
        self.assertNotIn("Plan approved. Starting execution", self.src)
        self.assertNotIn("_❌ Plan rejected", self.src)
        self.assertNotIn("_✏️ **Plan edited", self.src)

    def test_no_step_header_formatter(self):
        self.assertNotIn("_format_step_header", self.src)

    def test_no_verification_result_formatter(self):
        self.assertNotIn("_format_verification_result", self.src)

    def test_no_execution_cancelled_text_notice(self):
        self.assertNotIn("_Execution cancelled._", self.src)

    def test_edited_plan_invalid_is_error_chunk_not_text(self):
        idx = self.src.find("Edited plan was invalid")
        self.assertGreater(idx, 0, "the message itself should still exist")
        # Walk backwards to the nearest yield statement and confirm it's ErrorChunk
        preceding = self.src[max(0, idx - 200):idx]
        self.assertIn("ErrorChunk", preceding)
        self.assertNotIn("TextChunk", preceding)

    def test_step_exception_is_not_a_text_notice(self):
        self.assertNotIn('f"\\n\\n_Step {step_idx + 1} encountered an error', self.src)


# ===========================================================================
# _plan_steps_payload()
# ===========================================================================

class TestPlanStepsPayload(unittest.TestCase):

    def test_extracts_display_fields_only(self):
        from core.execution.pev_runner import _plan_steps_payload
        steps = [{
            "index": 0, "title": "T", "description": "D", "tool_hint": "shell",
            "status": "completed", "result_summary": "ok", "verified": True,
            "verification_notes": None,
        }]
        payload = _plan_steps_payload(steps)
        self.assertEqual(payload, [{"index": 0, "title": "T", "description": "D", "tool_hint": "shell"}])
        # Runtime bookkeeping fields must not leak into the SSE-facing payload.
        self.assertNotIn("status", payload[0])
        self.assertNotIn("result_summary", payload[0])
        self.assertNotIn("verified", payload[0])
        self.assertNotIn("verification_notes", payload[0])

    def test_handles_missing_tool_hint(self):
        from core.execution.pev_runner import _plan_steps_payload
        payload = _plan_steps_payload([{"index": 0, "title": "T", "description": "D"}])
        self.assertIsNone(payload[0]["tool_hint"])


# ===========================================================================
# api/executions.py _trace_from_shadow_buffer step_progress handling
# ===========================================================================

class TestTraceFromShadowBufferStepProgress(unittest.IsolatedAsyncioTestCase):

    async def test_step_progress_chunk_surfaces_as_trace_step(self):
        from api.executions import _trace_from_shadow_buffer

        raw_item = {
            "type": "step_progress",
            "step_index": 0,
            "total_steps": 1,
            "step_title": "EDITED-STEP: compute 2+2",
            "step_status": "running",
            "goal": "EDITED-BY-TEST: report the value of 2+2",
            "plan_steps": [{"index": 0, "title": "EDITED-STEP: compute 2+2",
                             "description": "State that 2+2=4.", "tool_hint": None}],
            "verification_summary": None,
            "verification_issues": None,
            "replan_reason": None,
            "error_detail": None,
            "steps_completed": None,
            "steps_failed": None,
            "replan_count": None,
        }

        class _FakeBuf:
            def chunks_from(self, start):
                return [raw_item]

        with patch.object(_local_broker, "get_shadow_buffer", return_value=_FakeBuf()):
            trace = await _trace_from_shadow_buffer("exec-1")

        self.assertEqual(len(trace), 1)
        step = trace[0]
        self.assertEqual(step.type, "step_progress")
        self.assertEqual(step.content["step_status"], "running")
        self.assertEqual(step.content["step_title"], "EDITED-STEP: compute 2+2")
        self.assertEqual(step.content["plan_steps"][0]["title"], "EDITED-STEP: compute 2+2")

    async def test_plan_complete_summary_fields_surface(self):
        from api.executions import _trace_from_shadow_buffer

        raw_item = {
            "type": "step_progress",
            "step_index": 0, "total_steps": 5, "step_title": "", "goal": "g",
            "step_status": "plan_complete",
            "plan_steps": None, "verification_summary": None,
            "verification_issues": None, "replan_reason": None,
            "error_detail": None,
            "steps_completed": 4, "steps_failed": 1, "replan_count": 2,
        }

        class _FakeBuf:
            def chunks_from(self, start):
                return [raw_item]

        with patch.object(_local_broker, "get_shadow_buffer", return_value=_FakeBuf()):
            trace = await _trace_from_shadow_buffer("exec-2")

        self.assertEqual(trace[0].content["steps_completed"], 4)
        self.assertEqual(trace[0].content["steps_failed"], 1)
        self.assertEqual(trace[0].content["replan_count"], 2)

    async def test_step_progress_mixed_with_other_chunk_types(self):
        """step_progress must not interfere with existing text/tool_call handling."""
        from api.executions import _trace_from_shadow_buffer

        items = [
            {"type": "text", "content": "Hello"},
            {"type": "step_progress", "step_index": 0, "total_steps": 1,
             "step_title": "S", "step_status": "running", "goal": "g",
             "plan_steps": None, "verification_summary": None,
             "verification_issues": None, "replan_reason": None,
             "error_detail": None, "steps_completed": None,
             "steps_failed": None, "replan_count": None},
            {"type": "tool_call", "tool_name": "shell", "arguments": {"cmd": "ls"}},
        ]

        class _FakeBuf:
            def chunks_from(self, start):
                return items

        with patch.object(_local_broker, "get_shadow_buffer", return_value=_FakeBuf()):
            trace = await _trace_from_shadow_buffer("exec-3")

        types = [s.type for s in trace]
        self.assertEqual(types, ["text", "step_progress", "tool_call"])


# ===========================================================================
# V6.14.1 regression: run_pev_execution() must actually reach CANCELLED
# ===========================================================================

class TestRunPevExecutionRejectReachesCancelled(unittest.IsolatedAsyncioTestCase):
    """
    Drives the *outer* run_pev_execution() wrapper (not just _pev_stream() in
    isolation) with a mocked _gate_plan_approval() returning "reject", and
    asserts the execution's in-process FSM status actually reaches CANCELLED
    and mark_execution_cancelled() is actually called on the broker.

    This is the layer where the real bug lived: tests that only exercised
    _pev_stream() directly (see TestPevStreamPlanApprovalDecisions in
    test_features.py) could pass even with the underlying regression,
    because they only checked which chunks _pev_stream() yielded -- not
    whether run_pev_execution()'s consumer loop actually acted on them.
    """

    async def test_reject_transitions_execution_to_cancelled(self):
        import core.execution.pev_runner as mod
        from core.execution.models import ConversationExecution, ExecutionStatus
        from core.broker.shadow_buffer import LocalShadowBuffer

        execution = ConversationExecution(
            execution_id="exec-reject-e2e",
            conversation_id="conv-reject-e2e",
            user_id="user-1",
            session_id="sess-1",
            status=ExecutionStatus.PENDING,
            buffer=None,
        )

        original_plan = {
            "goal": "Original goal",
            "steps": [{
                "index": 0, "title": "Original Step", "description": "orig",
                "tool_hint": None, "status": "pending", "result_summary": None,
                "verified": False, "verification_notes": None,
            }],
        }

        class _NoOpAgent:
            async def call(self, messages, stream=True):
                self.was_called = True
                if False:
                    yield  # pragma: no cover -- never reached; makes this an async generator

        fake_agent = _NoOpAgent()
        finalizer_calls = []

        async def fake_finalizer(final):
            finalizer_calls.append(final)

        real_shadow_buffer = LocalShadowBuffer()
        mark_cancelled_calls = []

        async def fake_mark_cancelled(execution_id, reason=""):
            mark_cancelled_calls.append((execution_id, reason))

        with patch.object(mod, "get_user_session",
                           return_value={"require_plan_approval": True}), \
             patch.object(mod.redis_execution_registry, "mark_execution_planning", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "update_execution", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "mark_execution_running", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "heartbeat", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "connect", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "mark_execution_cancelled",
                           new=fake_mark_cancelled), \
             patch.object(_local_broker, "get_shadow_buffer", return_value=real_shadow_buffer), \
             patch.object(mod.planner_engine, "generate", new=AsyncMock(return_value=original_plan)), \
             patch.object(mod, "AsyncSessionLocal") as mock_session_factory, \
             patch.object(mod.PlanRepo, "create", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_status", new=AsyncMock()), \
             patch.object(mod, "_gate_plan_approval",
                           new=AsyncMock(return_value=mod.PlanApprovalResult(
                               decision="reject", plan=None))), \
             patch.object(mod.ExecutionStateMachine, "assert_transition_allowed"), \
             patch.object(mod.event_bus, "publish"):

            db_ctx = MagicMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session_factory.return_value = db_ctx

            await mod.run_pev_execution(
                execution=execution,
                agent=fake_agent,
                messages=[{"role": "user", "content": "Do the thing"}],
                finalizer=fake_finalizer,
            )

        # The actual regression: this must be CANCELLED, not stuck at
        # whatever transition() last set it to before the reject branch
        # returned (PLANNING -> RUNNING, per run_pev_execution()'s own
        # unconditional pre-stream transition).
        self.assertEqual(execution.status, ExecutionStatus.CANCELLED,
                          "rejecting a plan must transition the execution to CANCELLED")
        self.assertEqual(len(mark_cancelled_calls), 1,
                          "mark_execution_cancelled() must actually be called on the broker")
        self.assertEqual(mark_cancelled_calls[0][0], "exec-reject-e2e")
        # V6.15.3: finalizer() now runs on the cancellation path too (via
        # _finalize_as_cancelled()), not just normal completion -- this is
        # what makes execution_metrics record a real (possibly zero, as
        # here since nothing streamed before the reject) token-usage row
        # instead of never writing one at all for a cancelled execution.
        # See the matching fix in core/execution/runner.py.
        self.assertEqual(len(finalizer_calls), 1,
                          "the finalizer must run once on cancellation, with a "
                          "best-effort snapshot of whatever was accumulated")
        self.assertIsNone(finalizer_calls[0].get("usage"),
                           "no LLM turn ran before the reject, so there is "
                           "genuinely no usage to recover in this specific case")


# ===========================================================================
# V6.15.3: partial token usage must be recovered on cancellation, not
# silently discarded -- see core/execution/runner.py and pev_runner.py's
# cancellation branches, and core/stream_buffer.py's aggregated(require_
# ended=False). Before this fix, a cancelled execution's finalizer() never
# ran at all, so execution_metrics recorded null token counts even when
# real tokens had already been spent on an LLM turn before the tool call
# (or step) that got cancelled -- a real billing/observability gap, not
# just a frontend cosmetic one (the frontend usage display was downstream
# of the same missing data).
# ===========================================================================

class TestRunPevExecutionRecoversPartialUsageOnCancel(unittest.IsolatedAsyncioTestCase):

    async def test_mid_step_cancel_recovers_streamed_usage(self):
        import json as _json
        import core.execution.pev_runner as mod
        from core.execution.models import ConversationExecution, ExecutionStatus
        from core.broker.shadow_buffer import LocalShadowBuffer
        from core.types import TextChunk, UsageChunk, StepProgressChunk

        execution = ConversationExecution(
            execution_id="exec-usage-recover", conversation_id="conv-usage-recover",
            user_id="user-1", session_id="sess-1", status=ExecutionStatus.PENDING, buffer=None,
        )

        async def _fake_pev_stream(**kwargs):
            # Simulates a real run: an LLM turn produces text + usage
            # (genuine token spend), THEN cancellation lands mid-step --
            # exactly the scenario live-tested against the shell tool.
            yield TextChunk(content="partial answer before cancel")
            yield UsageChunk(
                prompt_tokens=120, completion_tokens=40, total_tokens=160,
                provider="anthropic", model="claude-x",
            )
            execution.cancel_token.cancelled = True
            yield StepProgressChunk(step_status="cancelled", goal="goal")

        finalizer_calls = []

        async def fake_finalizer(final):
            finalizer_calls.append(final)

        real_shadow_buffer = LocalShadowBuffer()
        update_execution_calls = []

        async def fake_update_execution(execution_id, **fields):
            update_execution_calls.append((execution_id, fields))

        with patch.object(mod, "_pev_stream", new=_fake_pev_stream), \
             patch.object(mod, "get_user_session",
                           return_value={"require_plan_approval": False}), \
             patch.object(mod.redis_execution_registry, "mark_execution_planning", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "update_execution", new=fake_update_execution), \
             patch.object(mod.redis_execution_registry, "mark_execution_running", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "heartbeat", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "connect", new=AsyncMock()), \
             patch.object(mod.redis_execution_registry, "mark_execution_cancelled", new=AsyncMock()), \
             patch.object(_local_broker, "get_shadow_buffer", return_value=real_shadow_buffer), \
             patch.object(mod.ExecutionStateMachine, "assert_transition_allowed"), \
             patch.object(mod.event_bus, "publish"):

            await mod.run_pev_execution(
                execution=execution,
                agent=MagicMock(),  # unused -- _pev_stream is patched directly
                messages=[{"role": "user", "content": "do things"}],
                finalizer=fake_finalizer,
            )

        self.assertEqual(execution.status, ExecutionStatus.CANCELLED)
        self.assertEqual(len(finalizer_calls), 1,
                          "finalizer must run exactly once on cancellation")

        usage = finalizer_calls[0].get("usage")
        self.assertIsNotNone(
            usage,
            "the UsageChunk streamed before cancellation must be recovered "
            "into the finalizer's payload, not discarded by buffered.clear()",
        )
        self.assertEqual(usage.get("prompt_tokens"), 120)
        self.assertEqual(usage.get("completion_tokens"), 40)

        usage_persist_calls = [
            c for c in update_execution_calls if "usage_json" in c[1]
        ]
        self.assertTrue(
            usage_persist_calls,
            "partial usage must be persisted to the broker as usage_json so "
            "the SSE endpoint can emit it as a real `event: usage` frame",
        )
        persisted = _json.loads(usage_persist_calls[0][1]["usage_json"])
        self.assertEqual(persisted.get("prompt_tokens"), 120)
        self.assertEqual(persisted.get("completion_tokens"), 40)


# ===========================================================================
# BufferedStream.aggregated(require_ended=False)
# ===========================================================================

class TestBufferedStreamPartialAggregation(unittest.IsolatedAsyncioTestCase):

    async def test_aggregated_raises_when_not_ended_and_require_ended_true(self):
        from core.stream_buffer import BufferedStream
        from core.types import TextChunk

        async def _gen():
            yield TextChunk(content="hello")
            # deliberately never finishes iterating in this test

        buffered = BufferedStream(_gen())
        agen = buffered.stream_and_buffer()
        await agen.__anext__()  # consume one chunk, generator NOT exhausted

        with self.assertRaises(RuntimeError):
            await buffered.aggregated()  # default require_ended=True

    async def test_aggregated_require_ended_false_returns_partial_snapshot(self):
        from core.stream_buffer import BufferedStream
        from core.types import TextChunk, UsageChunk

        async def _gen():
            yield TextChunk(content="partial ")
            yield UsageChunk(prompt_tokens=10, completion_tokens=5, total_tokens=15)
            yield TextChunk(content="never reached in this test")

        buffered = BufferedStream(_gen())
        agen = buffered.stream_and_buffer()
        await agen.__anext__()  # TextChunk
        await agen.__anext__()  # UsageChunk
        # Stop consuming here -- generator is NOT exhausted, self._ended
        # stays False, exactly like a cancelled execution's abandoned loop.

        result = await buffered.aggregated(require_ended=False)

        self.assertEqual(result["text"], "partial ")
        self.assertIsNotNone(result["usage"])
        self.assertEqual(result["usage"]["prompt_tokens"], 10)
        self.assertEqual(result["usage"]["completion_tokens"], 5)


if __name__ == "__main__":
    unittest.main()
