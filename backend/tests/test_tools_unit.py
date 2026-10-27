# tests/test_tools_unit.py
"""
EVA Tool Unit Tests — mocked, no live server, no LLM required.

Covers tool-level logic in isolation via unittest.mock, distinct from:
  - test_apis.py       — live-server REST integration tests (requires running server)
  - test_behaviour.py  — live-server + real LLM runtime behaviour tests

Run with:
    cd <project_root>
    python tests/test_tools_unit.py
    (or: pytest tests/test_tools_unit.py -v)

Currently covers:
  - web_search tool consolidation (2026-07): 5 tools (web_search_text/_images/
    _videos/_news/_books) merged into 1 with a search_type parameter and a
    nested advanced_filters escape hatch.
  - trigger_daemon tool consolidation (2026-07): signal_daemon + await_daemon
    merged into 1 tool with a wait parameter.
  - Phase 1 (2026-07) chat-mode visibility fixes: blackboard/messaging tools
    now chat_excluded; delegate_to_agent un-excluded from conversational mode
    while parallel_delegate/coordinate remain PEV-only; description rewrites
    for web_search/http_get/rag_retrieve (delegate_to_agent and coordinate
    descriptions deliberately left unchanged).
  - Phase 4 (2026-07) PEV progressive disclosure: plan_execute mode now uses
    progressive disclosure with 4 orchestration tools (coordinate,
    delegate_to_agent, parallel_delegate, trigger_daemon) pre-seeded active
    on turn 1; tool manifest excludes already-active tools to avoid a
    contradictory "call describe_tool" instruction for tools the model can
    already call natively.
"""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is on sys.path so `from core...` / `from tools...` work
# whether the test is run from tests/ or the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestWebSearchConsolidation(unittest.IsolatedAsyncioTestCase):
    """
    web_search_text/_images/_videos/_news/_books were consolidated into a
    single web_search tool with a search_type parameter. Advanced/type-specific
    filters (region, safesearch, backend, page, and all images/videos-only
    fields) live in a nested advanced_filters dict rather than being flattened
    at the top level, so the default schema a small model sees is just
    query + search_type + max_results + timelimit.

    Critical regression covered here: DDGS().books() does not accept
    region/safesearch/timelimit kwargs at all — a naive merge that forwarded
    the full args dict to every backend would raise a TypeError for books.
    """

    def _mock_ddgs(self, return_value=None, side_effect=None):
        """Patch tools.default.web_search.DDGS so no real network call is made."""
        from tools.default import web_search as web_search_module
        mock_instance = MagicMock()
        for method_name in ("text", "news", "images", "videos", "books"):
            m = MagicMock(return_value=[] if return_value is None else return_value)
            if side_effect is not None:
                m.side_effect = side_effect
            setattr(mock_instance, method_name, m)
        patcher = patch.object(web_search_module, "DDGS", return_value=mock_instance)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mock_instance

    async def test_default_search_type_is_text(self):
        from tools.default.web_search import WebSearchTool
        mock_ddgs = self._mock_ddgs(return_value=[{"title": "result"}])
        result = await WebSearchTool().run({"query": "python asyncio"})
        self.assertEqual(result.result["results"], [{"title": "result"}])
        mock_ddgs.text.assert_called_once()
        kwargs = mock_ddgs.text.call_args.kwargs
        self.assertEqual(kwargs["query"], "python asyncio")
        self.assertEqual(kwargs["region"], "us-en")
        self.assertEqual(kwargs["safesearch"], "moderate")

    async def test_search_type_news_routes_to_news_method(self):
        from tools.default.web_search import WebSearchTool
        mock_ddgs = self._mock_ddgs()
        await WebSearchTool().run({"query": "latest AI news", "search_type": "news"})
        mock_ddgs.news.assert_called_once()
        mock_ddgs.text.assert_not_called()

    async def test_books_search_type_excludes_region_safesearch_timelimit(self):
        """Regression test for the DDGS().books() kwarg-mismatch bug caught
        during design review, before this tool was ever shipped."""
        from tools.default.web_search import WebSearchTool
        mock_ddgs = self._mock_ddgs()
        result = await WebSearchTool().run({"query": "machine learning textbook", "search_type": "books"})
        mock_ddgs.books.assert_called_once()
        kwargs = mock_ddgs.books.call_args.kwargs
        self.assertNotIn("region", kwargs)
        self.assertNotIn("safesearch", kwargs)
        self.assertNotIn("timelimit", kwargs)
        self.assertIn("query", kwargs)
        self.assertIn("max_results", kwargs)
        self.assertIn("page", kwargs)
        self.assertIn("backend", kwargs)
        self.assertNotIn("error", result.result)

    async def test_images_advanced_filters_extracted(self):
        from tools.default.web_search import WebSearchTool
        mock_ddgs = self._mock_ddgs()
        await WebSearchTool().run({
            "query": "sunset photos",
            "search_type": "images",
            "advanced_filters": {"size": "Large", "color": "Red", "license_image": "Public"},
        })
        kwargs = mock_ddgs.images.call_args.kwargs
        self.assertEqual(kwargs["size"], "Large")
        self.assertEqual(kwargs["color"], "Red")
        self.assertEqual(kwargs["license_image"], "Public")
        self.assertNotIn("resolution", kwargs, "videos-only field must not leak into an images call")

    async def test_images_without_advanced_filters_uses_none_defaults(self):
        from tools.default.web_search import WebSearchTool
        mock_ddgs = self._mock_ddgs()
        await WebSearchTool().run({"query": "sunset photos", "search_type": "images"})
        kwargs = mock_ddgs.images.call_args.kwargs
        self.assertIsNone(kwargs["size"])
        self.assertIsNone(kwargs["color"])

    async def test_videos_advanced_filters_extracted(self):
        from tools.default.web_search import WebSearchTool
        mock_ddgs = self._mock_ddgs()
        await WebSearchTool().run({
            "query": "python tutorial",
            "search_type": "videos",
            "advanced_filters": {"resolution": "high", "duration": "long"},
        })
        kwargs = mock_ddgs.videos.call_args.kwargs
        self.assertEqual(kwargs["resolution"], "high")
        self.assertEqual(kwargs["duration"], "long")
        self.assertNotIn("size", kwargs, "images-only field must not leak into a videos call")

    async def test_advanced_filters_override_region_safesearch_backend_page(self):
        from tools.default.web_search import WebSearchTool
        mock_ddgs = self._mock_ddgs()
        await WebSearchTool().run({
            "query": "test",
            "advanced_filters": {"region": "uk-en", "safesearch": "off", "backend": "bing", "page": 2},
        })
        kwargs = mock_ddgs.text.call_args.kwargs
        self.assertEqual(kwargs["region"], "uk-en")
        self.assertEqual(kwargs["safesearch"], "off")
        self.assertEqual(kwargs["backend"], "bing")
        self.assertEqual(kwargs["page"], 2)

    async def test_invalid_search_type_returns_error_without_calling_ddgs(self):
        from tools.default.web_search import WebSearchTool
        mock_ddgs = self._mock_ddgs()
        result = await WebSearchTool().run({"query": "test", "search_type": "shopping"})
        self.assertIn("error", result.result)
        mock_ddgs.text.assert_not_called()
        mock_ddgs.news.assert_not_called()

    async def test_ddgs_exception_returns_error_dict_not_raise(self):
        from tools.default.web_search import WebSearchTool
        self._mock_ddgs(side_effect=RuntimeError("network down"))
        result = await WebSearchTool().run({"query": "test"})
        self.assertIn("error", result.result)
        self.assertIn("network down", result.result["error"])

    async def test_timeout_returns_error_dict_not_raise(self):
        from tools.default.web_search import WebSearchTool
        import asyncio as _asyncio
        self._mock_ddgs()
        with patch("tools.default.web_search.asyncio.wait_for", side_effect=_asyncio.TimeoutError):
            result = await WebSearchTool().run({"query": "test"})
        self.assertIn("error", result.result)
        self.assertIn("timed out", result.result["error"].lower())

    def test_old_tool_names_no_longer_registered(self):
        from core.tools.loader import load_all_tools
        load_all_tools()
        from core.tools.registry import DefaultToolRegistry
        tools = DefaultToolRegistry.all()
        for old_name in ("web_search_text", "web_search_images", "web_search_videos",
                         "web_search_news", "web_search_books"):
            self.assertNotIn(old_name, tools, f"{old_name} should no longer be registered")
        self.assertIn("web_search", tools)


class TestTriggerDaemonConsolidation(unittest.IsolatedAsyncioTestCase):
    """
    signal_daemon (fire-and-forget) and await_daemon (blocking) were
    consolidated into a single trigger_daemon tool with a `wait` boolean
    parameter, since the choice between them was a timing decision rather
    than a purpose decision. Named trigger_daemon (not run_daemon) to avoid
    colliding with core.daemons.runner.run_daemon, the internal executor
    this tool calls into.
    """

    def _make_tool(self, execution_overrides=None):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        tool_cls = DefaultToolRegistry.all()["trigger_daemon"]
        tool = tool_cls()
        execution = MagicMock()
        execution.user_id = "u1"
        execution.caller_agent_name = "orchestrator"
        if execution_overrides:
            for k, v in execution_overrides.items():
                setattr(execution, k, v)
        tool.execution = execution
        return tool

    def test_chat_excluded_is_true(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        tool_cls = DefaultToolRegistry.all()["trigger_daemon"]
        self.assertTrue(
            getattr(tool_cls, "chat_excluded", False),
            "trigger_daemon requires an execution context conversational mode "
            "does not provide, and must stay excluded from chat manifests",
        )

    def test_old_tool_names_no_longer_registered(self):
        from core.tools.loader import load_all_tools
        load_all_tools()
        from core.tools.registry import DefaultToolRegistry
        tools = DefaultToolRegistry.all()
        self.assertNotIn("signal_daemon", tools)
        self.assertNotIn("await_daemon", tools)
        self.assertIn("trigger_daemon", tools)

    async def test_no_execution_context_returns_error(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        tool = DefaultToolRegistry.all()["trigger_daemon"]()
        # tool.execution deliberately left unset
        result = await tool.run({"daemon_name": "some-daemon"})
        self.assertIn("error", result.result)
        self.assertIn("no execution context", result.result["error"])

    async def test_empty_daemon_name_returns_error(self):
        tool = self._make_tool()
        result = await tool.run({"daemon_name": "  "})
        self.assertIn("error", result.result)
        self.assertIn("must not be empty", result.result["error"])

    async def test_unknown_daemon_returns_error(self):
        tool = self._make_tool()
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        with patch("core.daemons.registry.daemon_registry", mock_registry):
            result = await tool.run({"daemon_name": "nonexistent-daemon"})
        self.assertIn("error", result.result)
        self.assertIn("No daemon named", result.result["error"])

    async def test_wait_false_returns_immediately_with_execution_id(self):
        """Default (wait=False): fire-and-forget, must not block on the daemon run."""
        import asyncio
        tool = self._make_tool()

        mock_daemon = MagicMock()
        mock_daemon.user_id = "u1"
        mock_daemon.name = "notifier"
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=mock_daemon)
        mock_registry.set_lifecycle_status = AsyncMock()
        mock_registry.record_success = AsyncMock()
        mock_registry.record_failure = AsyncMock()

        mock_run_daemon_core = AsyncMock(return_value={"success": True, "output": "done"})

        with patch("core.daemons.registry.daemon_registry", mock_registry), \
             patch("core.daemons.runner.run_daemon", mock_run_daemon_core):
            result = await tool.run({"daemon_name": "notifier", "wait": False})
            # Let the background task (asyncio.create_task) run to completion
            # so it doesn't leak into the next test as a pending task warning.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        self.assertTrue(result.result["triggered"])
        self.assertEqual(result.result["daemon_name"], "notifier")
        self.assertIn("execution_id", result.result)
        mock_registry.set_lifecycle_status.assert_awaited_once()

    async def test_wait_true_success_returns_output_directly(self):
        tool = self._make_tool()
        mock_daemon = MagicMock()
        mock_daemon.user_id = "u1"
        mock_daemon.name = "data-fetcher"
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=mock_daemon)
        mock_registry.set_lifecycle_status = AsyncMock()
        mock_registry.record_success = AsyncMock()
        mock_registry.record_failure = AsyncMock()

        mock_run_daemon_core = AsyncMock(return_value={"success": True, "output": "fetched data"})

        with patch("core.daemons.registry.daemon_registry", mock_registry), \
             patch("core.daemons.runner.run_daemon", mock_run_daemon_core):
            result = await tool.run({"daemon_name": "data-fetcher", "wait": True})

        self.assertTrue(result.result["success"])
        self.assertEqual(result.result["output"], "fetched data")
        mock_registry.record_success.assert_awaited_once()

    async def test_wait_true_timeout_returns_soft_failure_not_raise(self):
        import asyncio as _asyncio
        tool = self._make_tool()
        mock_daemon = MagicMock()
        mock_daemon.user_id = "u1"
        mock_daemon.name = "slow-daemon"
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=mock_daemon)
        mock_registry.set_lifecycle_status = AsyncMock()
        mock_registry.record_failure = AsyncMock()

        with patch("core.daemons.registry.daemon_registry", mock_registry), \
             patch("core.daemons.runner.run_daemon", AsyncMock()), \
             patch("tools.default.daemon_tools.asyncio.wait_for", side_effect=_asyncio.TimeoutError):
            result = await tool.run({"daemon_name": "slow-daemon", "wait": True, "timeout_seconds": 5})

        self.assertFalse(result.result["success"])
        self.assertIn("did not finish within", result.result["error"])
        mock_registry.record_failure.assert_awaited_once()

    async def test_message_forwarding_uses_different_wording_for_wait_true_vs_false(self):
        """wait=False builds a 'signalled by' message; wait=True builds an
        'invoked synchronously by' message — both forwarded as override_trigger."""
        tool = self._make_tool()
        mock_daemon = MagicMock()
        mock_daemon.user_id = "u1"
        mock_daemon.name = "reporter"
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=mock_daemon)
        mock_registry.set_lifecycle_status = AsyncMock()
        mock_registry.record_success = AsyncMock()

        captured = {}

        async def _capture_run_daemon(**kwargs):
            captured.update(kwargs)
            return {"success": True, "output": "ok"}

        with patch("core.daemons.registry.daemon_registry", mock_registry), \
             patch("core.daemons.runner.run_daemon", _capture_run_daemon):
            await tool.run({"daemon_name": "reporter", "message": "check the PRs", "wait": True})

        self.assertIn("invoked synchronously by", captured["override_trigger"])
        self.assertIn("check the PRs", captured["override_trigger"])


class TestChatModeVisibilityPhase1(unittest.TestCase):
    """
    Phase 1 (2026-07): chat-mode exposure fixes.

    - blackboard_write/read and send_agent_message/read_agent_messages have
      no valid use outside a multi-agent execution and must not appear in a
      conversational-mode manifest.
    - delegate_to_agent (single-agent delegation) is un-excluded from chat
      mode — conversational executions already carry a full ExecutionContext,
      so single-agent delegation works there. parallel_delegate and
      coordinate (multi-agent orchestration) remain PEV/agent-mode only.
    - Both `chat_excluded` and the legacy `agent_only` alias are checked,
      since core/tools/registry.py ORs them together at class-definition
      time — a tool is only truly chat-visible if BOTH are False.
    """

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()
        from core.tools.registry import DefaultToolRegistry
        cls.tools = DefaultToolRegistry.all()

    def _is_chat_excluded(self, name: str) -> bool:
        cls = self.tools[name]
        return bool(getattr(cls, "chat_excluded", False) or getattr(cls, "agent_only", False))

    def test_blackboard_tools_are_chat_excluded(self):
        for name in ("blackboard_write", "blackboard_read"):
            self.assertIn(name, self.tools)
            self.assertTrue(self._is_chat_excluded(name), f"{name} should be chat_excluded")

    def test_messaging_tools_are_chat_excluded(self):
        for name in ("send_agent_message", "read_agent_messages"):
            self.assertIn(name, self.tools)
            self.assertTrue(self._is_chat_excluded(name), f"{name} should be chat_excluded")

    def test_delegate_to_agent_is_no_longer_chat_excluded(self):
        self.assertIn("delegate_to_agent", self.tools)
        self.assertFalse(
            self._is_chat_excluded("delegate_to_agent"),
            "delegate_to_agent should now be visible in conversational mode",
        )

    def test_multi_agent_tools_remain_chat_excluded(self):
        """Regression guard: parallel_delegate/coordinate must NOT have been
        accidentally un-excluded alongside delegate_to_agent."""
        for name in ("parallel_delegate", "coordinate"):
            self.assertIn(name, self.tools)
            self.assertTrue(self._is_chat_excluded(name), f"{name} should remain chat_excluded")

    def test_trigger_daemon_remains_chat_excluded(self):
        """Set in Phase 2 — confirm Phase 1 changes didn't disturb it."""
        self.assertIn("trigger_daemon", self.tools)
        self.assertTrue(self._is_chat_excluded("trigger_daemon"))


class TestDescriptionRewritesPhase1(unittest.TestCase):
    """
    Phase 1 (2026-07): description rewrites adding "USE THIS WHEN / USE X
    INSTEAD" disambiguation to reduce tool-selection confusion for smaller
    models. delegate_to_agent and coordinate descriptions were deliberately
    left unchanged per review — this is asserted as a regression guard.
    """

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()
        from core.tools.registry import DefaultToolRegistry
        cls.tools = DefaultToolRegistry.all()

    def test_web_search_description_has_disambiguation(self):
        desc = self.tools["web_search"].description
        self.assertIn("USE THIS", desc)
        self.assertIn("USE http_get INSTEAD", desc)
        self.assertIn("USE rag_retrieve INSTEAD", desc)

    def test_http_get_description_has_disambiguation(self):
        desc = self.tools["http_get"].description
        self.assertIn("USE THIS WHEN", desc)
        self.assertIn("USE web_search INSTEAD", desc)

    def test_rag_retrieve_description_clarifies_not_web_search(self):
        desc = self.tools["rag_retrieve"].description
        self.assertIn("NOT a web search", desc)
        self.assertIn("USE web_search INSTEAD", desc)

    def test_delegate_to_agent_description_unchanged(self):
        """Regression guard: this description must NOT have been touched."""
        desc = self.tools["delegate_to_agent"].description
        self.assertNotIn("USE THIS", desc)
        self.assertIn("Delegate a specific subtask to a subagent", desc)

    def test_coordinate_description_unchanged(self):
        """Regression guard: this description must NOT have been touched."""
        desc = self.tools["coordinate"].description
        self.assertNotIn("USE THIS WHEN", desc)


class TestPEVProgressiveDisclosurePhase4(unittest.TestCase):
    """
    Phase 4 (2026-07): plan_execute mode now uses progressive disclosure
    (previously only conversational mode did), with a fixed set of
    orchestration tools pre-seeded active on turn 1 so the model can
    immediately plan/delegate without a describe_tool() round-trip. Every
    other tool (web_search, shell, git_*, etc.) still requires promotion.

    Tests worker.py's constants and replicates its exact promotion logic
    against a real UserToolRegistry, rather than invoking the full worker()
    function (a heavy, hard-to-unit-test monolith).
    """

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    def test_plan_execute_added_to_progressive_modes(self):
        from core.execution.worker import _PROGRESSIVE_MODES
        self.assertIn("plan_execute", _PROGRESSIVE_MODES)
        self.assertIn("conversational", _PROGRESSIVE_MODES, "must not have been removed")

    def test_pev_preseeded_tools_are_the_orchestration_set(self):
        from core.execution.worker import _PEV_PRESEEDED_TOOLS
        self.assertEqual(
            _PEV_PRESEEDED_TOOLS,
            {"coordinate", "delegate_to_agent", "parallel_delegate", "trigger_daemon"},
        )

    def test_pev_mode_starts_with_only_preseeded_tools_active(self):
        """Replicates worker.py's exact pre-seed loop against a real registry."""
        from core.tools.user_registry import UserToolRegistry
        from core.execution.worker import _PEV_PRESEEDED_TOOLS

        registry = UserToolRegistry(
            default_tools=True,
            enabled_tools=[],
            execution_mode="plan_execute",
            progressive=True,
        )
        for name in _PEV_PRESEEDED_TOOLS:
            registry.promote(name)

        active = set(registry.get_active_tool_classes().keys())
        expected = _PEV_PRESEEDED_TOOLS | {"describe_tool"}
        self.assertEqual(active, expected)

        # Tools NOT in the pre-seed set must still require promotion.
        for name in ("web_search", "shell", "http_get", "rag_retrieve"):
            self.assertNotIn(name, active, f"{name} should not be pre-seeded in PEV mode")

    def test_pev_mode_can_still_promote_other_tools_on_demand(self):
        from core.tools.user_registry import UserToolRegistry
        from core.execution.worker import _PEV_PRESEEDED_TOOLS

        registry = UserToolRegistry(
            default_tools=True, enabled_tools=[],
            execution_mode="plan_execute", progressive=True,
        )
        for name in _PEV_PRESEEDED_TOOLS:
            registry.promote(name)

        result = registry.promote("web_search")
        self.assertTrue(result)
        self.assertIn("web_search", registry.get_active_tool_classes())

    def test_conversational_mode_unaffected_by_pev_preseed(self):
        """Regression guard: conversational mode must still start with only
        describe_tool active -- the PEV pre-seed list must not leak there."""
        from core.tools.user_registry import UserToolRegistry

        registry = UserToolRegistry(
            default_tools=True, enabled_tools=[],
            execution_mode="conversational", progressive=True,
        )
        active = set(registry.get_active_tool_classes().keys())
        self.assertEqual(active, {"describe_tool"})

    def test_manifest_excludes_already_active_tools(self):
        """
        The tool manifest injected into the system prompt must not list a
        tool as "call describe_tool() to activate" when it's already active
        -- this would be contradictory. Exercises the same filtering logic
        worker.py applies before calling _build_manifest().
        """
        from core.execution.worker import _build_manifest
        from core.tools.registry import DefaultToolRegistry

        all_tools = DefaultToolRegistry.all()
        active_names = {"coordinate", "delegate_to_agent", "parallel_delegate", "trigger_daemon"}
        filtered = {name: cls for name, cls in all_tools.items() if name not in active_names}

        manifest = _build_manifest(filtered, "plan_execute")
        for name in active_names:
            self.assertNotIn(
                name, manifest,
                f"{name} is already active and must not appear in the manifest",
            )
        # A tool that still needs promotion should appear.
        self.assertIn("web_search", manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
