# tests/test_active_tools_line_unit.py
"""
Plan steps are told which tools the run has already activated, so they stop re-describing them.

Found live (2026-09-21, `gpt-oss:120b`, browser debug loop): 23 of 49 tool calls in one run were
describe_tool, for only 6 distinct tools -- 17 were repeats of tools that were already active,
about 10 s of model time each. Every plan step starts with a fresh context, so it has no memory of
the describe_tool calls earlier steps made, while the activated tools are still in its tool list
(promotion lasts for the whole execution) and the manifest told it to describe before use.

The fix changes NOTHING about what is promoted or how big the prompt is: it only names the tools
(names, not schemas) so the model does not have to guess. Also covers the softer manifest /
describe_tool wording and the new log fields that show what progressive disclosure costs a run.

Run with:
    pytest tests/test_active_tools_line_unit.py -v
"""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.execution.step_record import active_tool_names, build_active_tools_line
from core.types import TextChunk


class _Registry:
    """The bits of UserToolRegistry the runner reads."""

    def __init__(self, active, progressive=True):
        self._is_progressive = progressive
        self._active = {"describe_tool": object(), **{n: object() for n in active}}
        self._promoted_tools = set(active)

    def promote(self, name):
        self._active[name] = object()
        self._promoted_tools.add(name)

    def get_active_tool_classes(self):
        return dict(self._active)


class TestActiveToolNames(unittest.TestCase):

    def test_names_exclude_the_describe_gateway(self):
        self.assertEqual(active_tool_names(_Registry(["read_file", "shell"])), {"read_file", "shell"})

    def test_a_non_progressive_registry_has_nothing_to_say(self):
        self.assertEqual(active_tool_names(_Registry(["read_file"], progressive=False)), set())

    def test_no_registry_is_fine(self):
        self.assertEqual(active_tool_names(None), set())

    def test_a_registry_that_raises_never_breaks_the_run(self):
        class Boom:
            _is_progressive = True

            def get_active_tool_classes(self):
                raise RuntimeError("registry exploded")

        self.assertEqual(active_tool_names(Boom()), set())

    def test_a_mock_registry_is_harmless(self):
        self.assertEqual(active_tool_names(MagicMock()), set())


class TestBuildActiveToolsLine(unittest.TestCase):

    def test_nothing_new_means_no_line(self):
        self.assertEqual(build_active_tools_line({"a", "b"}, {"a", "b"}), "")
        self.assertEqual(build_active_tools_line(set(), set()), "")

    def test_only_the_runs_own_additions_are_named(self):
        line = build_active_tools_line({"coordinate", "read_file", "browser_tabs"}, {"coordinate"})
        self.assertIn("browser_tabs, read_file", line)      # sorted
        self.assertNotIn("coordinate", line)                # was active from the start -> not named

    def test_it_tells_the_model_what_to_do_and_what_still_needs_describe_tool(self):
        line = build_active_tools_line({"read_file"}, set())
        self.assertIn("do not call describe_tool for them again", line)
        self.assertIn("still needs describe_tool first", line)

    def test_a_long_list_is_capped_and_the_rest_counted(self):
        names = {f"tool_{i:02d}" for i in range(55)}
        line = build_active_tools_line(names, set())
        self.assertIn("tool_00", line)
        self.assertNotIn("tool_54", line)
        self.assertIn("(and 15 more)", line)

    def test_it_names_tools_only_no_schemas(self):
        line = build_active_tools_line({"read_file"}, set())
        self.assertLess(len(line), 400)


class TestStepPromptCarriesTheLine(unittest.TestCase):

    def _prompt(self, **kw):
        from core.execution.pev_runner import _build_step_messages
        return _build_step_messages([], {"goal": "g"}, {"index": 0, "title": "T", "description": "d"}, **kw)[-1]["content"]

    def test_line_appears_before_the_step_itself(self):
        line = build_active_tools_line({"browser_tabs"}, set())
        prompt = self._prompt(active_tools_line=line)
        self.assertIn("Tools already active in this run", prompt)
        self.assertLess(prompt.index("Tools already active"), prompt.index("**This step:**"))

    def test_no_line_no_section(self):
        self.assertNotIn("Tools already active", self._prompt())


# ── the real step loop ───────────────────────────────────────────────────────

def _plan(*titles):
    return {"goal": "g", "steps": [{"index": i, "title": t, "description": "d", "status": "pending"}
                                   for i, t in enumerate(titles)]}


async def _drive(agent, plan, registry):
    from core.execution import pev_runner
    from core.execution.models import ExecutionStatus
    from core.execution.verifier import VerificationResult

    async def fake_verify(**kwargs):
        return VerificationResult(passed=True, summary="s")

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    repo = MagicMock()
    for n in ("create", "update_steps", "update_status", "update_goal_verification"):
        setattr(repo, n, AsyncMock())
    execution = MagicMock(execution_id="e1", conversation_id="c1")
    execution.cancel_token = MagicMock(cancelled=False)
    execution.status = ExecutionStatus.RUNNING
    execution.user_registry = registry
    with patch.object(pev_runner.planner_engine, "generate", new=AsyncMock(return_value=plan)), \
         patch.object(pev_runner.verifier_engine, "verify", new=fake_verify), \
         patch.object(pev_runner, "PlanRepo", repo), \
         patch.object(pev_runner, "AsyncSessionLocal", return_value=cm):
        async for _ in pev_runner._pev_stream(
            execution=execution, agent=agent, base_messages=[{"role": "user", "content": "go"}],
            user_id="u", require_plan_approval=False,
        ):
            pass


class _PromotingAgent:
    """Step 1 activates browser_tabs, step 2 activates read_file, like a model calling describe_tool."""

    def __init__(self, registry, promotions):
        self.registry, self.promotions, self.prompts = registry, promotions, []

    async def call(self, messages, stream=True):
        self.prompts.append(messages[-1]["content"])
        for name in self.promotions.get(len(self.prompts), []):
            self.registry.promote(name)
        yield TextChunk(content="done")


class TestRealStepLoop(unittest.IsolatedAsyncioTestCase):

    async def test_later_steps_are_told_what_earlier_steps_activated(self):
        reg = _Registry(["coordinate"])                       # active from the start (orchestration pre-seed)
        agent = _PromotingAgent(reg, {1: ["browser_tabs"], 2: ["read_file"]})
        await _drive(agent, _plan("one", "two", "three"), reg)

        p1, p2, p3 = agent.prompts
        self.assertNotIn("Tools already active", p1)          # nothing activated yet
        self.assertIn("browser_tabs", p2.split("**This step:**")[0])
        self.assertNotIn("read_file", p2.split("**This step:**")[0])
        self.assertIn("browser_tabs, read_file", p3.split("**This step:**")[0])

    async def test_tools_active_from_the_start_are_never_named(self):
        reg = _Registry(["coordinate", "delegate_to_agent"])
        agent = _PromotingAgent(reg, {1: ["browser_tabs"]})
        await _drive(agent, _plan("one", "two"), reg)
        section = agent.prompts[1].split("**This step:**")[0]
        self.assertIn("browser_tabs", section)
        self.assertNotIn("coordinate", section)
        self.assertNotIn("delegate_to_agent", section)

    async def test_a_run_that_activates_nothing_gets_no_line(self):
        reg = _Registry(["coordinate"])
        agent = _PromotingAgent(reg, {})
        await _drive(agent, _plan("one", "two"), reg)
        self.assertNotIn("Tools already active", agent.prompts[1])

    async def test_a_non_progressive_run_gets_no_line(self):
        reg = _Registry([], progressive=False)
        agent = _PromotingAgent(reg, {1: ["anything"]})
        await _drive(agent, _plan("one", "two"), reg)
        self.assertNotIn("Tools already active", agent.prompts[1])


# ── wording ──────────────────────────────────────────────────────────────────

class TestManifestAndDescribeToolWording(unittest.TestCase):

    def test_manifest_says_active_tools_are_called_directly(self):
        from core.execution.worker_context import _build_manifest
        from core.tools.loader import load_all_tools
        from core.tools.registry import DefaultToolRegistry
        load_all_tools()
        text = " ".join(_build_manifest({"read_file": DefaultToolRegistry.all()["read_file"]}, "plan_execute").split())
        self.assertIn("not yet active needs `describe_tool", text)
        self.assertIn("already described or used in this run are active", text)
        self.assertIn("describing an active tool again only wastes a turn", text)
        self.assertNotIn("then call the tool directly on your next turn", text)   # the old, always-describe wording

    def test_describe_tool_description_says_only_for_inactive_tools(self):
        from core.tools.loader import load_all_tools
        from core.tools.registry import DefaultToolRegistry
        load_all_tools()
        desc = " ".join(DefaultToolRegistry.all()["describe_tool"].description.split())
        self.assertIn("and only then", desc)
        self.assertNotIn("Always call this first", desc)


# ── the log line that shows what progressive disclosure costs ────────────────

class TestFinalizeLogsProgressiveDisclosureCost(unittest.IsolatedAsyncioTestCase):

    async def _flush(self, registry, tool_stats):
        from core.execution import worker_finalize
        from core.execution.models import ExecutionStatus

        snapshot = SimpleNamespace(
            tool_stats=tool_stats, termination_reason="success", error_message=None, duration_ms=1000,
            total_steps=5, fallbacks_triggered=0, fallback_details=None, subagent_calls=0, subagent_failures=0)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=MagicMock())
        cm.__aexit__ = AsyncMock(return_value=False)
        execution = SimpleNamespace(status=ExecutionStatus.COMPLETED, started_at=None, completed_at=None,
                                    user_registry=registry)
        with patch.object(worker_finalize.metrics_collector, "pop_snapshot", return_value=snapshot), \
             patch.object(worker_finalize, "AsyncSessionLocal", return_value=cm), \
             patch.object(worker_finalize.ExecutionMetricsRepo, "upsert", new=AsyncMock()), \
             self.assertLogs(worker_finalize.logger, level=logging.INFO) as captured:
            await worker_finalize._flush_metrics(
                execution_id="e1", conversation_id="c1", user_id="u", execution=execution, session={},
                captured_context={"usage": {"prompt_tokens": 1234, "completion_tokens": 56}})
        rec = next(r for r in captured.records if r.getMessage() == "Execution metrics persisted")
        return rec

    async def test_promoted_set_and_describe_calls_and_tokens_are_logged(self):
        stat = lambda name, n: SimpleNamespace(tool_name=name, call_count=n, avg_latency_ms=1, failure_count=0,
                                               success_count=n, last_denial_reason=None)
        reg = _Registry(["read_file", "shell", "browser_tabs"])
        rec = await self._flush(reg, {"describe_tool": stat("describe_tool", 7), "read_file": stat("read_file", 3)})
        self.assertEqual(rec.promoted_count, 3)
        self.assertEqual(rec.promoted_tools, ["browser_tabs", "read_file", "shell"])
        self.assertEqual(rec.describe_tool_calls, 7)
        self.assertEqual(rec.prompt_tokens, 1234)
        self.assertEqual(rec.completion_tokens, 56)

    async def test_a_run_without_a_registry_logs_zeros(self):
        rec = await self._flush(None, {})
        self.assertEqual(rec.promoted_count, 0)
        self.assertEqual(rec.describe_tool_calls, 0)


if __name__ == "__main__":
    unittest.main()
