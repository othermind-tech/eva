# tests/test_coordinate_dynamic_agents_unit.py
"""
Unit tests for A-05 (gap report): the coordinate tool's planning prompt used
to forbid inventing agent names outright ("Use ONLY agent names from the
provided subagent list -- never invent or assume names"), a restriction that
predates delegate_to_agent's dynamic-subagent mechanism (V5.0 Sprint 3,
shipped after coordinate's original V4.11 build) and was never revisited.

This suite covers three layers of the fix:
  1. core/agents/dynamic.py -- the config builder consolidated out of
     delegate_tool.py and parallel_delegate_tool.py's own private copies,
     now shared by all three orchestration tools.
  2. prompts/coordinator.py -- build_coordinator_plan_system() now takes a
     second (max_dynamic_agents) argument and no longer forbids invention.
  3. tools/default/coordinate_tool.py -- CoordinateTool.run()'s plan
     validation step now resolves an unrecognized agent_name into a dynamic
     config (reusing core.agents.dynamic) instead of silently dropping the
     task, capped by MAX_COORDINATE_DYNAMIC_AGENTS and reused (not
     re-counted) across repeated references to the same invented name.

No live server, no real LLM calls -- the coordinator's own planning/
synthesis LLM calls and _run_one (actual subagent execution) are mocked;
this suite verifies coordinate_tool.py's own resolution/validation logic.

Run with:
    cd <project_root>
    pytest tests/test_coordinate_dynamic_agents_unit.py -v
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


# ===========================================================================
# core/agents/dynamic.py -- shared config builder
# ===========================================================================

class TestSharedDynamicConfigBuilder(unittest.TestCase):

    def test_build_dynamic_config_basic_shape(self):
        from core.agents.dynamic import build_dynamic_config

        cfg = build_dynamic_config("researcher", {})
        self.assertEqual(cfg.name, "researcher")
        self.assertIsNone(cfg.id)
        self.assertEqual(cfg.max_steps, 10)
        self.assertIsNone(cfg.tools)
        self.assertIn("researcher", cfg.system_prompt)

    def test_explicit_fields_honoured(self):
        from core.agents.dynamic import build_dynamic_config

        cfg = build_dynamic_config("coder", {
            "system_prompt": "You write code.",
            "allowed_tools": ["shell", "git_tool"],
            "max_steps": 20,
            "model": "openai/gpt-4o",
            "provider": "openai",
        })
        self.assertEqual(cfg.system_prompt, "You write code.")
        self.assertEqual(cfg.tools, ["shell", "git_tool"])
        self.assertEqual(cfg.max_steps, 20)
        self.assertEqual(cfg.model, "openai/gpt-4o")
        self.assertEqual(cfg.provider, "openai")

    def test_delegate_tool_and_parallel_delegate_tool_use_the_same_function(self):
        """Both original call sites must resolve to the identical shared
        implementation, not divergent copies (the duplication this fix
        consolidated)."""
        from core.agents.dynamic import build_dynamic_config
        import tools.default.delegate_tool as dt
        import tools.default.parallel_delegate_tool as pdt

        self.assertIs(dt._build_dynamic_config, build_dynamic_config)
        self.assertIs(pdt._build_dynamic_config, build_dynamic_config)

    def test_coordinate_tool_uses_the_same_shared_function_too(self):
        """The third consumer added by this fix."""
        from core.agents.dynamic import build_dynamic_config
        import tools.default.coordinate_tool as ct

        self.assertIs(ct.build_dynamic_config, build_dynamic_config)


# ===========================================================================
# config/settings.py -- MAX_COORDINATE_DYNAMIC_AGENTS
# ===========================================================================

class TestDynamicAgentCapSetting(unittest.TestCase):

    def test_default_is_three(self):
        from config.settings import MAX_COORDINATE_DYNAMIC_AGENTS
        self.assertEqual(MAX_COORDINATE_DYNAMIC_AGENTS, 3)

    def test_coordinate_tool_reads_the_live_setting(self):
        import tools.default.coordinate_tool as ct
        from config.settings import MAX_COORDINATE_DYNAMIC_AGENTS
        self.assertEqual(ct._MAX_DYNAMIC_AGENTS, MAX_COORDINATE_DYNAMIC_AGENTS)


# ===========================================================================
# prompts/coordinator.py -- planning prompt no longer forbids invention
# ===========================================================================

class TestCoordinatorPromptAllowsInvention(unittest.TestCase):

    def test_never_invent_language_is_gone(self):
        from prompts.coordinator import build_coordinator_plan_system
        text = build_coordinator_plan_system(5, 3)
        self.assertNotIn("never invent or assume names", text)

    def test_invention_is_explicitly_permitted_with_conditions(self):
        from prompts.coordinator import build_coordinator_plan_system
        text = build_coordinator_plan_system(5, 3)
        self.assertIn("Only invent a new agent_name", text)
        self.assertIn("system_prompt", text)

    def test_dynamic_agent_cap_is_injected(self):
        from prompts.coordinator import build_coordinator_plan_system
        text = build_coordinator_plan_system(5, 4)
        self.assertIn("At most 4 distinct invented agent", text)
        self.assertNotIn("{max_dynamic_agents}", text)

    def test_output_format_example_includes_persona_fields(self):
        from prompts.coordinator import build_coordinator_plan_system
        text = build_coordinator_plan_system(5, 3)
        for field in ("system_prompt", "allowed_tools", "max_steps", "model", "provider"):
            self.assertIn(f'"{field}"', text)


# ===========================================================================
# tools/default/coordinate_tool.py -- _PlanTask schema
# ===========================================================================

class TestPlanTaskDynamicFields(unittest.TestCase):

    def test_new_optional_fields_exist_and_default_none(self):
        from tools.default.coordinate_tool import _PlanTask
        t = _PlanTask(agent_name="x", task="do something")
        self.assertIsNone(t.system_prompt)
        self.assertIsNone(t.allowed_tools)
        self.assertIsNone(t.max_steps)
        self.assertIsNone(t.model)
        self.assertIsNone(t.provider)

    def test_fields_are_settable(self):
        from tools.default.coordinate_tool import _PlanTask
        t = _PlanTask(
            agent_name="invented-researcher",
            task="find the answer",
            system_prompt="You are a researcher.",
            allowed_tools=["web_search"],
            max_steps=8,
            model="openai/gpt-4o",
            provider="openai",
        )
        self.assertEqual(t.system_prompt, "You are a researcher.")
        self.assertEqual(t.allowed_tools, ["web_search"])
        self.assertEqual(t.max_steps, 8)


# ===========================================================================
# tools/default/coordinate_tool.py -- CoordinateTool.run() end-to-end
# resolution logic (planning + synthesis LLM mocked, _run_one mocked)
# ===========================================================================

def _make_execution(user_id="u1"):
    execution = MagicMock()
    execution.user_id = user_id
    return execution


def _plan_json(phases):
    return json.dumps({
        "goal_summary": "test goal",
        "phases": phases,
        "synthesis_instruction": "combine everything",
    })


class TestCoordinateResolvesDynamicAgents(unittest.IsolatedAsyncioTestCase):

    async def _run_coordinate(self, plan_phases, registered_agents=None, max_dynamic=None):
        """
        Drives the REAL CoordinateTool.run() with:
          - subagent_registry mocked to return only `registered_agents`
          - the planning LLM call mocked to return `plan_phases`
          - the synthesis LLM call mocked to return a fixed string
          - _run_one mocked (no real AgentEngine/tool dispatch) so this
            suite tests coordinate_tool.py's own resolution/validation
            logic in isolation, not subagent execution itself.
        Returns (result, run_one_mock) so tests can inspect both the
        tool's return value and exactly what _run_one was called with.
        """
        from tools.default.coordinate_tool import CoordinateTool

        registered_agents = registered_agents or {}

        # CoordinateTool.run() step 4 hard-errors before planning even
        # starts if zero subagents are registered at all ("No subagents
        # are configured") -- a real, pre-existing precondition unrelated
        # to A-05's dynamic-invention fix. Ensure subagent_registry.list()
        # never returns empty here so tests actually reach the planning
        # call; subagent_registry.get() below still only resolves names
        # the caller explicitly registered, so invented names correctly
        # miss the registry and fall into the dynamic-resolution path.
        _baseline = {"__baseline_agent__": SimpleNamespace(
            name="__baseline_agent__", description="baseline, not referenced by any task",
            system_prompt="baseline", tools=[], max_steps=5, model=None, provider=None,
        )}
        _list_view = {**_baseline, **registered_agents}

        async def fake_registry_get(user_id, name):
            return registered_agents.get(name)

        async def fake_registry_list(user_id):
            return list(_list_view.values())

        run_one_mock = AsyncMock(side_effect=lambda task, view, cfg, *a, **kw: {
            "agent_name": task.agent_name,
            "status": "completed",
            "output": f"result for {task.agent_name}",
        })

        patches = [
            patch("config.settings.get_user_session", return_value={
                "provider": "groq", "model": "m", "api_key": "k",
            }),
            patch("core.agents.registry.subagent_registry.get", side_effect=fake_registry_get),
            patch("core.agents.registry.subagent_registry.list", side_effect=fake_registry_list),
            patch(
                "core.providers.fallback_call.call_with_full_fallback",
                new=AsyncMock(side_effect=[
                    _plan_json(plan_phases),          # planning call
                    "synthesized final answer",        # synthesis call
                ]),
            ),
            patch("tools.default.coordinate_tool._run_one", new=run_one_mock),
        ]
        if max_dynamic is not None:
            patches.append(patch("tools.default.coordinate_tool._MAX_DYNAMIC_AGENTS", max_dynamic))

        tool = CoordinateTool()
        tool.execution = _make_execution()

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            if max_dynamic is not None:
                with patches[5]:
                    result = await tool.run({"goal": "test goal", "strategy": "adaptive"})
            else:
                result = await tool.run({"goal": "test goal", "strategy": "adaptive"})

        return result, run_one_mock

    async def test_invented_agent_task_is_not_dropped(self):
        registered = {"researcher": SimpleNamespace(
            name="researcher", description="researches things",
            system_prompt="You research.", tools=[], max_steps=5,
            model=None, provider=None,
        )}
        plan_phases = [{
            "phase": 1, "execution": "parallel",
            "tasks": [{
                "agent_name": "poet",
                "task": "write a haiku",
                "context": None,
                "system_prompt": "You are a poet.",
                "allowed_tools": None,
                "max_steps": None,
                "model": None,
                "provider": None,
            }],
        }]
        result, run_one_mock = await self._run_coordinate(plan_phases, registered)

        self.assertNotIn("error", result.result)
        self.assertEqual(result.result["total_tasks"], 1)
        self.assertEqual(result.result["completed"], 1)
        run_one_mock.assert_awaited_once()
        called_task = run_one_mock.await_args.args[0]
        self.assertEqual(called_task.agent_name, "poet")

    async def test_invented_agent_gets_a_dynamic_config_with_its_persona(self):
        plan_phases = [{
            "phase": 1, "execution": "parallel",
            "tasks": [{
                "agent_name": "poet", "task": "write a haiku", "context": None,
                "system_prompt": "You are a poet.", "allowed_tools": ["web_search"],
                "max_steps": 7, "model": None, "provider": None,
            }],
        }]
        result, run_one_mock = await self._run_coordinate(plan_phases, {})

        called_cfg = run_one_mock.await_args.args[2]
        self.assertEqual(called_cfg.system_prompt, "You are a poet.")
        self.assertEqual(called_cfg.tools, ["web_search"])
        self.assertEqual(called_cfg.max_steps, 7)
        self.assertIsNone(called_cfg.id)  # dynamic, not a DB row

    async def test_invented_agent_flagged_dynamic_true(self):
        plan_phases = [{
            "phase": 1, "execution": "parallel",
            "tasks": [{
                "agent_name": "poet", "task": "write a haiku", "context": None,
                "system_prompt": "You are a poet.", "allowed_tools": None,
                "max_steps": None, "model": None, "provider": None,
            }],
        }]
        _, run_one_mock = await self._run_coordinate(plan_phases, {})
        self.assertTrue(run_one_mock.await_args.kwargs.get("dynamic"))

    async def test_registered_agent_is_not_flagged_dynamic(self):
        registered = {"researcher": SimpleNamespace(
            name="researcher", description="researches things",
            system_prompt="You research.", tools=[], max_steps=5,
            model=None, provider=None,
        )}
        plan_phases = [{
            "phase": 1, "execution": "parallel",
            "tasks": [{
                "agent_name": "researcher", "task": "find facts", "context": None,
                "system_prompt": None, "allowed_tools": None,
                "max_steps": None, "model": None, "provider": None,
            }],
        }]
        _, run_one_mock = await self._run_coordinate(plan_phases, registered)
        self.assertFalse(run_one_mock.await_args.kwargs.get("dynamic"))
        called_cfg = run_one_mock.await_args.args[2]
        self.assertIs(called_cfg, registered["researcher"])  # real config, untouched

    async def test_mixed_registered_and_invented_in_one_phase(self):
        registered = {"researcher": SimpleNamespace(
            name="researcher", description="researches things",
            system_prompt="You research.", tools=[], max_steps=5,
            model=None, provider=None,
        )}
        plan_phases = [{
            "phase": 1, "execution": "parallel",
            "tasks": [
                {"agent_name": "researcher", "task": "find facts", "context": None,
                 "system_prompt": None, "allowed_tools": None, "max_steps": None,
                 "model": None, "provider": None},
                {"agent_name": "poet", "task": "write a haiku", "context": None,
                 "system_prompt": "You are a poet.", "allowed_tools": None,
                 "max_steps": None, "model": None, "provider": None},
            ],
        }]
        result, run_one_mock = await self._run_coordinate(plan_phases, registered)
        self.assertEqual(result.result["total_tasks"], 2)
        self.assertEqual(run_one_mock.await_count, 2)

    async def test_cap_enforced_excess_distinct_invented_names_dropped(self):
        plan_phases = [{
            "phase": 1, "execution": "parallel",
            "tasks": [
                {"agent_name": f"invented-{i}", "task": f"task {i}", "context": None,
                 "system_prompt": f"You are invented-{i}.", "allowed_tools": None,
                 "max_steps": None, "model": None, "provider": None}
                for i in range(5)
            ],
        }]
        result, run_one_mock = await self._run_coordinate(plan_phases, {}, max_dynamic=2)

        # Only the first 2 distinct invented names should survive validation.
        self.assertEqual(result.result["total_tasks"], 2)
        self.assertEqual(run_one_mock.await_count, 2)
        called_names = {c.args[0].agent_name for c in run_one_mock.await_args_list}
        self.assertEqual(called_names, {"invented-0", "invented-1"})

    async def test_same_invented_name_reused_across_tasks_counts_once(self):
        """Reusing an already-invented name (per the prompt's own
        instruction) must not consume a second slot in the cap."""
        plan_phases = [{
            "phase": 1, "execution": "parallel",
            "tasks": [
                {"agent_name": "poet", "task": "write a haiku", "context": None,
                 "system_prompt": "You are a poet.", "allowed_tools": None,
                 "max_steps": None, "model": None, "provider": None},
                {"agent_name": "poet", "task": "write a limerick", "context": None,
                 "system_prompt": "You are a poet.", "allowed_tools": None,
                 "max_steps": None, "model": None, "provider": None},
            ],
        }]
        # Cap of 1 -- both tasks reference the SAME invented name, so both
        # must survive (only 1 distinct name was ever synthesized).
        result, run_one_mock = await self._run_coordinate(plan_phases, {}, max_dynamic=1)

        self.assertEqual(result.result["total_tasks"], 2)
        self.assertEqual(run_one_mock.await_count, 2)

    async def test_plan_with_only_invented_names_and_zero_cap_yields_error(self):
        plan_phases = [{
            "phase": 1, "execution": "parallel",
            "tasks": [{
                "agent_name": "poet", "task": "write a haiku", "context": None,
                "system_prompt": "You are a poet.", "allowed_tools": None,
                "max_steps": None, "model": None, "provider": None,
            }],
        }]
        result, run_one_mock = await self._run_coordinate(plan_phases, {}, max_dynamic=0)

        self.assertIn("error", result.result)
        run_one_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
