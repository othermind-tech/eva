# tests/test_context_watchdog_unit.py
"""
Unit tests for V6.11: the context-budget watchdog.

Covers:
  - core.tools.dispatcher._cap_tool_output() — Layer 1, the dynamic
    per-tool-call output cap that generalizes core/shell/executor.py's
    static SHELL_MAX_OUTPUT_BYTES truncation to every tool.
  - core.agent_engine.AgentEngine._remaining_context_budget() and
    _compress_history_if_needed() — Layer 2, the proactive in-place
    ReAct-history compaction.

Instance methods are exercised via a lightweight SimpleNamespace stand-in
for `self` (they only touch self.provider/self.model/self._COMPRESS_KEEP_RECENT)
rather than constructing a full AgentEngine, which needs a live handler.

Mocked throughout — no live server, no real API calls.

Run with:
    cd <project_root>
    python tests/test_context_watchdog_unit.py
    (or: pytest tests/test_context_watchdog_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCapToolOutput(unittest.TestCase):
    """Layer 1 — core.tools.dispatcher._cap_tool_output()"""

    def test_under_budget_string_returned_unchanged(self):
        from core.tools.dispatcher import _cap_tool_output
        result = _cap_tool_output("short result", max_tokens=1000, model_name="gpt-oss:20b")
        self.assertEqual(result, "short result")

    def test_over_budget_string_truncated_with_marker(self):
        from core.tools.dispatcher import _cap_tool_output
        big = "word " * 5000  # comfortably over any small token budget
        result = _cap_tool_output(big, max_tokens=20, model_name="gpt-oss:20b")
        self.assertIsInstance(result, str)
        self.assertLess(len(result), len(big))
        self.assertIn("truncated by EVA", result)
        self.assertIn("20 tokens", result)

    def test_over_budget_dict_serialized_and_truncated(self):
        from core.tools.dispatcher import _cap_tool_output
        big_dict = {"results": [{"text": "word " * 200} for _ in range(50)]}
        result = _cap_tool_output(big_dict, max_tokens=20, model_name="gpt-oss:20b")
        # Non-string input over budget becomes a truncated string -- structure
        # is not preserved, matching shell's existing truncation precedent.
        self.assertIsInstance(result, str)
        self.assertIn("truncated by EVA", result)

    def test_under_budget_dict_returned_unchanged(self):
        from core.tools.dispatcher import _cap_tool_output
        small_dict = {"status": "ok"}
        result = _cap_tool_output(small_dict, max_tokens=1000, model_name="gpt-oss:20b")
        self.assertIs(result, small_dict)

    def test_none_raw_output_passthrough(self):
        from core.tools.dispatcher import _cap_tool_output
        self.assertIsNone(_cap_tool_output(None, max_tokens=100, model_name="gpt-oss:20b"))

    def test_zero_or_negative_max_tokens_passthrough(self):
        from core.tools.dispatcher import _cap_tool_output
        self.assertEqual(_cap_tool_output("anything", max_tokens=0, model_name="gpt-oss:20b"), "anything")
        self.assertEqual(_cap_tool_output("anything", max_tokens=-5, model_name="gpt-oss:20b"), "anything")


class TestRemainingContextBudget(unittest.TestCase):
    """Layer 1's budget input — AgentEngine._remaining_context_budget()"""

    def _fake_self(self, provider="ollama_cloud", model="gpt-oss:20b"):
        from core.agent_engine import AgentEngine
        ns = SimpleNamespace(provider=provider, model=model)
        ns._remaining_context_budget = AgentEngine._remaining_context_budget.__get__(ns)
        return ns

    def test_falls_back_to_default_when_capability_unknown(self):
        """
        Confirmed empirically (repro against the reference incident) that
        ollama_cloud has no cached capability data on disk at all in this
        deployment -- get_model() returns None for gpt-oss:20b, the exact
        model involved. Falling back to CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS
        instead of no-op'ing is what makes the watchdog actually apply to
        that provider, rather than silently disabling itself for it.
        """
        from config.settings import CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS
        fake_self = self._fake_self()
        with patch("core.agent_engine.get_model_capability", return_value=None):
            result = fake_self._remaining_context_budget([{"role": "user", "content": "hi"}])
        self.assertIsNotNone(result)
        self.assertLess(result, CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS)
        self.assertGreater(result, 0)

    def test_computes_remaining_budget_from_capability(self):
        from core.providers.capability_registry import ModelCapability
        fake_self = self._fake_self()
        cap = ModelCapability(provider="ollama_cloud", model="gpt-oss:20b", max_context_tokens=1000)
        messages = [{"role": "user", "content": "short message"}]
        with patch("core.agent_engine.get_model_capability", return_value=cap):
            result = fake_self._remaining_context_budget(messages)
        self.assertIsNotNone(result)
        self.assertLess(result, 1000)  # some tokens already consumed
        self.assertGreater(result, 0)

    def test_never_returns_negative(self):
        from core.providers.capability_registry import ModelCapability
        fake_self = self._fake_self()
        cap = ModelCapability(provider="ollama_cloud", model="gpt-oss:20b", max_context_tokens=5)
        messages = [{"role": "user", "content": "word " * 500}]  # far exceeds the tiny budget
        with patch("core.agent_engine.get_model_capability", return_value=cap):
            result = fake_self._remaining_context_budget(messages)
        self.assertEqual(result, 0)


class TestCompressHistoryIfNeeded(unittest.TestCase):
    """Layer 2 — AgentEngine._compress_history_if_needed()"""

    def _fake_self(self, provider="ollama_cloud", model="gpt-oss:20b"):
        from core.agent_engine import AgentEngine
        ns = SimpleNamespace(
            provider=provider, model=model,
            _COMPRESS_KEEP_RECENT=AgentEngine._COMPRESS_KEEP_RECENT,
        )
        ns._compress_history_if_needed = AgentEngine._compress_history_if_needed.__get__(ns)
        return ns

    def _long_history(self, n_pairs=10):
        messages = [
            {"role": "system", "content": "You are EVA."},
            {"role": "user", "content": "Do the task."},
        ]
        for i in range(n_pairs):
            messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": f"c{i}", "type": "function",
                                 "function": {"name": f"tool_{i}", "arguments": "{}"}}],
            })
            messages.append({"role": "tool", "tool_call_id": f"c{i}",
                              "content": "word " * 300})
        return messages

    def test_falls_back_to_default_and_still_compresses_when_capability_unknown(self):
        """
        Same reasoning as TestRemainingContextBudget's equivalent test:
        ollama_cloud has no cached capability data in this deployment, so
        "capability unknown" must not mean "watchdog disabled" -- it must
        fall back to CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS and still trigger
        when that fallback budget is exceeded.
        """
        fake_self = self._fake_self()
        messages = self._long_history(n_pairs=40)  # sized to comfortably clear the fallback's trigger ratio
        original_len = len(messages)
        with patch("core.agent_engine.get_model_capability", return_value=None):
            fake_self._compress_history_if_needed(messages)
        self.assertLess(len(messages), original_len)

    def test_noop_when_capability_unknown_and_under_fallback_trigger(self):
        fake_self = self._fake_self()
        messages = self._long_history(n_pairs=1)  # small -- under the fallback's trigger ratio too
        original = list(messages)
        with patch("core.agent_engine.get_model_capability", return_value=None):
            fake_self._compress_history_if_needed(messages)
        self.assertEqual(messages, original)

    def test_noop_when_comfortably_under_trigger_ratio(self):
        from core.providers.capability_registry import ModelCapability
        fake_self = self._fake_self()
        messages = self._long_history(n_pairs=1)  # small history
        original = list(messages)
        cap = ModelCapability(provider="ollama_cloud", model="gpt-oss:20b", max_context_tokens=1_000_000)
        with patch("core.agent_engine.get_model_capability", return_value=cap):
            fake_self._compress_history_if_needed(messages)
        self.assertEqual(messages, original)

    def test_compresses_in_place_when_over_trigger_ratio(self):
        from core.providers.capability_registry import ModelCapability
        fake_self = self._fake_self()
        messages = self._long_history(n_pairs=10)
        original_len = len(messages)
        # Small max_context_tokens so the large synthetic history trips the
        # 0.85 default trigger ratio comfortably.
        cap = ModelCapability(provider="ollama_cloud", model="gpt-oss:20b", max_context_tokens=500)
        with patch("core.agent_engine.get_model_capability", return_value=cap):
            fake_self._compress_history_if_needed(messages)
        # Compressed in place: fewer messages than before.
        self.assertLess(len(messages), original_len)
        # Leading system + user messages preserved verbatim.
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "You are EVA.")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Do the task.")
        # A condensation note was inserted.
        joined = " ".join(str(m.get("content", "")) for m in messages)
        self.assertIn("context-budget watchdog", joined)
        # Most recent exchange preserved verbatim at the tail.
        self.assertEqual(messages[-1], self._long_history(n_pairs=10)[-1])

    def test_noop_when_not_enough_history_to_compress(self):
        from core.providers.capability_registry import ModelCapability
        fake_self = self._fake_self()
        # Only system + user + one small exchange -- at or under keep_recent+2.
        messages = self._long_history(n_pairs=1)
        original = list(messages)
        cap = ModelCapability(provider="ollama_cloud", model="gpt-oss:20b", max_context_tokens=1)
        with patch("core.agent_engine.get_model_capability", return_value=cap):
            fake_self._compress_history_if_needed(messages)
        self.assertEqual(messages, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
