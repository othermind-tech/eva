"""
V6.59 Phase 5 -- run-time usability gate (core/providers/usability.py).

Phase 4b validates a provider choice when it is saved. This is the check made at
the moment of USE, because a saved choice goes stale: the key is removed, the
model is not a chat model, a legacy sub-agent row has a provider but no model.
Pinned here: what counts as usable, how a run's primary + fallback chain is
reduced to the usable candidates (with promotion), and that each run path
(/chat, the worker, daemons, sub-agent delegation) applies it.
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.providers import usability as u
from providers import credentials


def _keys(*providers):
    for p in providers:
        credentials.set_api_key(p, f"{p}-key")


class ProviderCheckTests(unittest.TestCase):
    def test_matrix(self):
        _keys("groq")
        self.assertIsNone(u.check_provider("groq"))
        self.assertIsNone(u.check_provider("ollama"))
        self.assertEqual(u.check_provider("nope").reason, u.REASON_UNKNOWN_PROVIDER)
        self.assertEqual(u.check_provider("azure").reason, u.REASON_NOT_CHAT_READY)
        self.assertEqual(u.check_provider("openai").reason, u.REASON_NOT_CONFIGURED)

    def test_explicit_key_counts_as_configured(self):
        self.assertIsNone(u.check_provider("openai", explicit_key="sk-x"))

    def test_status_codes(self):
        self.assertEqual(u.check_provider("openai").status_code, 424)
        self.assertEqual(u.check_provider("azure").status_code, 422)

    def test_key_removed_after_save_is_caught(self):
        _keys("groq")
        self.assertIsNone(u.check_provider("groq"))
        credentials.delete_api_key("groq")
        self.assertEqual(u.check_provider("groq").reason, u.REASON_NOT_CONFIGURED)


class ModelCheckTests(unittest.TestCase):
    def test_known_chat_tool_model_is_usable(self):
        tools, usable, reason = u.model_usability("deepseek", "deepseek-chat")
        self.assertTrue(usable)
        self.assertTrue(tools)
        self.assertIsNone(reason)

    def test_unknown_model_is_allowed(self):
        self.assertEqual(u.model_usability("deepseek", "model-released-yesterday"), (None, True, None))
        self.assertIsNone(u.check_model("deepseek", "model-released-yesterday"))

    def test_non_chat_model_blocked(self):
        info = {"mode": "embedding", "supports_function_calling": False}
        with patch.object(u, "model_facts", return_value=info):
            p = u.check_model("mistral", "mistral-embed")
        self.assertEqual(p.reason, u.REASON_NOT_CHAT_MODEL)
        self.assertEqual(p.status_code, 422)

    def test_explicitly_no_tool_calling_blocked(self):
        with patch.object(u, "model_facts", return_value={"mode": "chat", "supports_function_calling": False}):
            p = u.check_model("mistral", "old-model")
        self.assertEqual(p.reason, u.REASON_NO_TOOL_CALLING)
        self.assertIn("tool calling", p.message)

    def test_silent_dataset_is_not_a_block(self):
        # supports_function_calling absent -> unknown, not False
        with patch.object(u, "model_facts", return_value={"mode": "chat"}):
            self.assertIsNone(u.check_model("mistral", "x"))

    def test_local_provider_models_never_blocked(self):
        with patch.object(u, "model_facts", return_value={"mode": "chat", "supports_function_calling": False}):
            self.assertIsNone(u.check_model("ollama", "my-local-model"))

    def test_setting_turns_model_half_off(self):
        with patch("config.settings.PROVIDER_GATE_MODEL_CHECK_ENABLED", False), \
             patch.object(u, "model_facts", return_value={"mode": "embedding"}):
            self.assertIsNone(u.check_model("mistral", "mistral-embed"))

    def test_provider_half_unaffected_by_the_model_switch(self):
        with patch("config.settings.PROVIDER_GATE_MODEL_CHECK_ENABLED", False):
            self.assertEqual(u.check_target("openai", "gpt-4o").reason, u.REASON_NOT_CONFIGURED)

    def test_missing_model(self):
        _keys("groq")
        self.assertEqual(u.check_target("groq", "").reason, u.REASON_MODEL_MISSING)


class PlanRunTests(unittest.TestCase):
    def test_all_usable_keeps_order(self):
        _keys("groq", "ollama_cloud")
        plan = u.plan_run("ollama_cloud", "gpt-oss:20b", [{"provider": "groq", "model": "llama-3.3-70b-versatile"}])
        self.assertTrue(plan.ok)
        self.assertFalse(plan.promoted)
        self.assertEqual([c["provider"] for c in plan.candidates], ["ollama_cloud", "groq"])
        self.assertEqual(plan.skipped, [])

    def test_primary_unusable_fallback_promoted(self):
        _keys("groq")                                  # anthropic key was removed
        plan = u.plan_run("anthropic", "claude-x", [{"provider": "groq", "model": "llama-3.3-70b-versatile"}])
        self.assertTrue(plan.ok)
        self.assertTrue(plan.promoted)
        self.assertEqual(plan.primary["provider"], "groq")
        self.assertEqual(plan.fallbacks, [])
        self.assertEqual(plan.primary_problem.reason, u.REASON_NOT_CONFIGURED)

    def test_unusable_fallback_pruned_primary_untouched(self):
        _keys("groq")
        plan = u.plan_run("groq", "llama-3.3-70b-versatile",
                          [{"provider": "openai", "model": "gpt-4o"}, {"provider": "bedrock", "model": "x"}])
        self.assertTrue(plan.ok)
        self.assertFalse(plan.promoted)
        self.assertEqual(plan.fallbacks, [])
        self.assertEqual(len(plan.skipped), 2)

    def test_nothing_usable_fails_with_actionable_message(self):
        plan = u.plan_run("openai", "gpt-4o", [{"provider": "anthropic", "model": "c"}])
        self.assertFalse(plan.ok)
        msg = plan.failure_message()
        self.assertIn("OpenAI", msg)
        self.assertIn("1 fallback entry was also unusable", msg)
        self.assertEqual(msg.count("Profile → API Keys"), 1)        # said once, not repeated
        self.assertIn("pick another provider", msg)
        self.assertEqual(plan.primary_problem.status_code, 424)
        self.assertEqual(plan.primary_problem.model, "gpt-4o")      # the target that failed, not ""

    def test_entries_without_model_are_ignored(self):
        _keys("groq")
        plan = u.plan_run("groq", "m", [{"provider": "openai"}, {"model": "x"}])
        self.assertEqual(len(plan.candidates), 1)
        self.assertEqual(plan.skipped, [])

    def test_explicit_entry_key_makes_it_usable(self):
        plan = u.plan_run("groq", "m", [{"provider": "openai", "model": "gpt-4o", "api_key": "sk-explicit"}])
        self.assertEqual([c["provider"] for c in plan.candidates], ["openai"])
        self.assertTrue(plan.promoted)


class ChildTargetTests(unittest.TestCase):
    def test_no_override_uses_session(self):
        _keys("groq")
        plan = u.resolve_child_target(None, None, "groq", "llama-3.3-70b-versatile")
        self.assertEqual(plan.primary["provider"], "groq")

    def test_model_only_override_applies_to_session_provider(self):
        _keys("groq")
        plan = u.resolve_child_target(None, "other-model", "groq", "llama-3.3-70b-versatile")
        self.assertEqual(plan.primary, {"provider": "groq", "model": "other-model"})

    def test_provider_override_is_a_pair_never_mixed_with_parent_model(self):
        _keys("groq", "ollama_cloud")
        plan = u.resolve_child_target("ollama_cloud", "gpt-oss:20b", "groq", "llama-3.3-70b-versatile")
        self.assertEqual(plan.primary, {"provider": "ollama_cloud", "model": "gpt-oss:20b"})

    def test_legacy_provider_without_model_is_unusable_not_mismatched(self):
        _keys("groq", "ollama_cloud")
        plan = u.resolve_child_target("ollama_cloud", None, "groq", "llama-3.3-70b-versatile")
        self.assertFalse(plan.ok)
        self.assertEqual(plan.primary_problem.reason, u.REASON_MODEL_MISSING)

    def test_override_key_removed_falls_back_to_parent_chain(self):
        _keys("groq")
        plan = u.resolve_child_target("anthropic", "claude-x", "groq", "m",
                                      [{"provider": "groq", "model": "llama-3.3-70b-versatile"}])
        self.assertTrue(plan.promoted)
        self.assertEqual(plan.primary["provider"], "groq")


class ChatEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def _chat(self, session):
        from api.chat import chat_endpoint
        from api.schemas.requests import ChatRequest
        with patch("api.chat.get_user_session", return_value=session):
            return await chat_endpoint(ChatRequest(message="hi"), auth={"user_id": "u", "session_id": "s"}, db=AsyncMock())

    async def test_stale_primary_with_no_fallback_is_rejected_synchronously(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            await self._chat({"provider": "openai", "model": "gpt-4o", "fallback_chain": []})
        self.assertEqual(cm.exception.status_code, 424)
        d = cm.exception.detail
        self.assertEqual((d["error_code"], d["reason"], d["provider"]), ("provider_not_usable", u.REASON_NOT_CONFIGURED, "openai"))
        self.assertIn("API Keys", d["message"])

    async def test_non_chat_ready_primary_is_422(self):
        from fastapi import HTTPException
        credentials.set_api_key("azure", "k")
        with self.assertRaises(HTTPException) as cm:
            await self._chat({"provider": "azure", "model": "gpt-4o", "fallback_chain": []})
        self.assertEqual(cm.exception.status_code, 422)


class DelegationToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_delegate_unusable_child_fails_only_that_agent(self):
        from tools.default import parallel_delegate_tool as pd
        cfg = MagicMock(provider="anthropic", model="claude-x", max_steps=3, tools=[], system_prompt="")
        view = MagicMock(execution_id="e1", shadow=None, blackboard=None, cancel_token=None)
        task = MagicMock(agent_name="researcher", task="t", context=None)
        out = await pd._run_one(task, view, cfg, "groq", "m", "", False, [], False)
        self.assertEqual(out["status"], "failed")
        self.assertIn("Anthropic", out["error"])
        self.assertEqual(out["duration_ms"], 0)

    async def test_parallel_delegate_provider_without_model_fails_clearly(self):
        from tools.default import parallel_delegate_tool as pd
        _keys("groq", "ollama_cloud")
        cfg = MagicMock(provider="ollama_cloud", model=None, max_steps=3, tools=[], system_prompt="")
        view = MagicMock(execution_id="e1", shadow=None, blackboard=None, cancel_token=None)
        task = MagicMock(agent_name="researcher", task="t", context=None)
        out = await pd._run_one(task, view, cfg, "groq", "llama-3.3-70b-versatile", "", False, [], False)
        self.assertEqual(out["status"], "failed")
        self.assertIn("No model is set", out["error"])


class SingleDelegateToolGateTests(unittest.TestCase):
    def test_delegate_tool_gates_the_child_target_and_uses_only_usable_fallbacks(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "tools/default/delegate_tool.py").read_text(encoding="utf-8")
        self.assertIn("resolve_child_target(", src)
        self.assertIn("fallback_chain=_child_fallbacks", src)
        self.assertNotIn('fallback_chain=session.get("fallback_chain") or []', src)


class DaemonRunnerGateTests(unittest.TestCase):
    def test_runner_source_uses_the_gate_and_pairs_provider_with_model(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core/daemons/runner.py").read_text(encoding="utf-8")
        self.assertIn("plan_run(provider, model, _fallbacks)", src)
        self.assertIn("fallback_chain=_fallbacks", src)
        # an override is a pair: the model must not fall back to the session's model
        self.assertIn("model    = daemon.model\n", src)


class SessionForPlanTests(unittest.TestCase):
    def test_nothing_skipped_returns_the_same_object(self):
        _keys("groq")
        session = {"provider": "groq", "model": "m", "fallback_chain": [], "api_key": None}
        plan = u.plan_run("groq", "m", [])
        self.assertIs(u.session_for_plan(session, plan), session)

    def test_promotion_returns_a_copy_and_never_mutates_the_stored_session(self):
        _keys("groq")
        stored = {"provider": "anthropic", "model": "claude-x", "api_key": "OLD-PRIMARY-KEY",
                  "fallback_chain": [{"provider": "groq", "model": "g", "api_key": "fb-key"},
                                     {"provider": "openai", "model": "o"}], "tools": ["shell"]}
        snapshot = {k: (list(v) if isinstance(v, list) else v) for k, v in stored.items()}
        plan = u.plan_run(stored["provider"], stored["model"], stored["fallback_chain"])
        run_session = u.session_for_plan(stored, plan)
        self.assertEqual((run_session["provider"], run_session["model"]), ("groq", "g"))
        self.assertEqual(run_session["fallback_chain"], [])          # unusable openai pruned
        self.assertEqual(run_session["api_key"], "fb-key")           # not the old primary's key
        self.assertEqual(run_session["tools"], ["shell"])            # everything else carried over
        self.assertEqual(stored, snapshot)                           # stored session untouched

    def test_pruned_fallbacks_without_promotion_keep_primary_and_key(self):
        _keys("groq")
        stored = {"provider": "groq", "model": "m", "api_key": "primary-key",
                  "fallback_chain": [{"provider": "openai", "model": "o"}]}
        plan = u.plan_run("groq", "m", stored["fallback_chain"])
        run_session = u.session_for_plan(stored, plan)
        self.assertEqual(run_session["api_key"], "primary-key")
        self.assertEqual(run_session["fallback_chain"], [])
        self.assertEqual(len(stored["fallback_chain"]), 1)


class WorkerGateTests(unittest.TestCase):
    def test_worker_applies_gate_before_building_tools_and_publishes_promotion(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core/execution/worker.py").read_text(encoding="utf-8")
        gate = src.index("plan_run(session[\"provider\"]")
        self.assertLess(gate, src.index("user_tools = UserToolRegistry("))
        self.assertIn("session = session_for_plan(session, _plan)", src)
        self.assertIn("provider.fallback_promoted", src)


if __name__ == "__main__":
    unittest.main()
