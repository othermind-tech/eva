# tests/test_reasoning_levels_unit.py
"""
Unit tests for V6.7: reasoning goes from a plain bool to a provider-aware
level ("minimal"/"low"/"medium"/"high"/"xhigh", or None/"off" for
disabled).

Covers:
  - core/providers/reasoning_support.py: the unified-vocabulary module --
    snap-to-nearest-level, Anthropic's EVA-owned shape detection (adaptive
    vs legacy budget_tokens) and temperature gating, Ollama's level
    mapping, the provider dispatch function.
  - core/providers/capability_registry.py: the supports_reasoning binding
    fix (was always False for every registered model before this).
  - providers/handlers/openai_handler.py: the try-once/learn mechanism
    for max_tokens vs max_completion_tokens, replacing a hand-maintained
    model-name list with dynamic self-correction.
  - api/schemas/requests.py: SessionRequest.reasoning's field validator
    (level strings, "off"/None, and the legacy-bool transition shim).
  - api/session.py: the save-time capability check -- binary gate,
    level-snapping, and the always-on-can't-disable case.

History: the Anthropic shape/temperature functions originally borrowed
litellm's private AnthropicConfig internals directly, verified against the
real installed package rather than mocked. That broke outright when a
later litellm upgrade removed/reshaped those private methods (litellm
1.80.7: AnthropicConfig._is_adaptive_thinking_model and
._apply_sampling_param no longer exist) -- Anthropic is a native EVA
provider (providers/handlers/anthropic_handler.py uses the `anthropic` SDK
directly, litellm is never in that request path), so depending on
litellm's *private*, unpinned internals for this one classification was
the wrong tradeoff. Reasoning_support.py now owns this classification
directly (see its own module comments) -- these tests check that
EVA-owned logic against the same real model names as before, no litellm
involved. The classification is still based on the original empirical verification.

Run with:
    cd <project_root>
    python tests/test_reasoning_levels_unit.py
    (or: pytest tests/test_reasoning_levels_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# core/providers/reasoning_support.py
# ---------------------------------------------------------------------------

class TestSnapToNearestLevel(unittest.TestCase):
    def test_exact_match_returned_unchanged(self):
        from core.providers.reasoning_support import snap_to_nearest_level
        self.assertEqual(snap_to_nearest_level("medium", ("low", "medium", "high")), "medium")

    def test_snaps_xhigh_to_high_when_unavailable(self):
        from core.providers.reasoning_support import snap_to_nearest_level
        self.assertEqual(snap_to_nearest_level("xhigh", ("low", "medium", "high")), "high")

    def test_snaps_minimal_to_low_when_unavailable(self):
        from core.providers.reasoning_support import snap_to_nearest_level
        self.assertEqual(snap_to_nearest_level("minimal", ("low", "medium", "high")), "low")

    def test_empty_available_returns_none(self):
        from core.providers.reasoning_support import snap_to_nearest_level
        self.assertIsNone(snap_to_nearest_level("medium", ()))

    def test_unrecognised_requested_falls_back_to_middle(self):
        from core.providers.reasoning_support import snap_to_nearest_level
        result = snap_to_nearest_level("not-a-real-level", ("low", "medium", "high"))
        self.assertEqual(result, "medium")


class TestAnthropicReasoningSupportInHouse(unittest.TestCase):
    """
    Verifies EVA's own (no-litellm) Anthropic shape/temperature
    classification. Previously tested the same behaviour borrowed from
    litellm's private internals -- broke when those internals changed
    shape across a litellm upgrade (see module docstring). Same model
    names, same expected behaviour, now backed by EVA-owned logic that
    doesn't depend on litellm's private, unpinned API surface.
    """

    def test_legacy_model_gets_full_vocabulary_and_can_disable(self):
        from core.providers.reasoning_support import anthropic_reasoning_support, REASONING_LEVELS
        support = anthropic_reasoning_support("claude-opus-4-1-20250805")
        self.assertEqual(support.levels, REASONING_LEVELS)
        self.assertTrue(support.can_disable)

    def test_adaptive_model_gets_three_levels(self):
        from core.providers.reasoning_support import anthropic_reasoning_support
        support = anthropic_reasoning_support("claude-opus-4-8")
        self.assertEqual(support.levels, ("low", "medium", "high"))

    def test_always_on_model_cannot_disable(self):
        from core.providers.reasoning_support import anthropic_reasoning_support
        support = anthropic_reasoning_support("claude-fable-5")
        self.assertFalse(support.can_disable)

    def test_adaptive_model_that_is_not_always_on_can_disable(self):
        from core.providers.reasoning_support import anthropic_reasoning_support
        support = anthropic_reasoning_support("claude-opus-4-8")
        self.assertTrue(support.can_disable)

    def test_thinking_param_off_returns_none(self):
        from core.providers.reasoning_support import anthropic_thinking_param
        self.assertIsNone(anthropic_thinking_param(None, "claude-opus-4-8"))
        self.assertIsNone(anthropic_thinking_param("off", "claude-opus-4-8"))

    def test_thinking_param_legacy_model_returns_budget_tokens_shape(self):
        from core.providers.reasoning_support import anthropic_thinking_param
        result = anthropic_thinking_param("medium", "claude-opus-4-1-20250805")
        self.assertEqual(result["type"], "enabled")
        self.assertIsInstance(result["budget_tokens"], int)
        self.assertGreater(result["budget_tokens"], 0)

    def test_thinking_param_adaptive_model_returns_effort_shape(self):
        from core.providers.reasoning_support import anthropic_thinking_param
        result = anthropic_thinking_param("high", "claude-opus-4-8")
        self.assertEqual(result, {"type": "adaptive", "effort": "high"})

    def test_thinking_param_adaptive_model_snaps_out_of_range_level(self):
        from core.providers.reasoning_support import anthropic_thinking_param
        result = anthropic_thinking_param("xhigh", "claude-opus-4-8")
        self.assertEqual(result, {"type": "adaptive", "effort": "high"})

    def test_temperature_allowed_on_unrestricted_model(self):
        from core.providers.reasoning_support import anthropic_supports_temperature
        self.assertTrue(anthropic_supports_temperature("claude-opus-4-1-20250805", 0.7))

    def test_temperature_rejected_on_restricted_model_nondefault_value(self):
        from core.providers.reasoning_support import anthropic_supports_temperature
        self.assertFalse(anthropic_supports_temperature("claude-opus-4-8", 0.7))

    def test_temperature_one_exception_allowed_even_on_restricted_model(self):
        """The nuance EVA's old hardcoded list would have missed entirely --
        litellm's own logic special-cases exactly this value."""
        from core.providers.reasoning_support import anthropic_supports_temperature
        self.assertTrue(anthropic_supports_temperature("claude-opus-4-8", 1))


class TestOllamaThinkValue(unittest.TestCase):
    def test_maps_xhigh_to_max(self):
        from core.providers.reasoning_support import ollama_think_value
        self.assertEqual(ollama_think_value("xhigh"), "max")

    def test_maps_minimal_to_low(self):
        from core.providers.reasoning_support import ollama_think_value
        self.assertEqual(ollama_think_value("minimal"), "low")

    def test_none_and_off_both_return_none(self):
        from core.providers.reasoning_support import ollama_think_value
        self.assertIsNone(ollama_think_value(None))
        self.assertIsNone(ollama_think_value("off"))


class TestGetReasoningSupportDispatch(unittest.TestCase):
    def test_anthropic_routes_to_anthropic_function(self):
        from core.providers.reasoning_support import get_reasoning_support
        support = get_reasoning_support("anthropic", "claude-opus-4-8")
        self.assertEqual(support.levels, ("low", "medium", "high"))

    def test_ollama_cloud_gets_full_unified_range(self):
        from core.providers.reasoning_support import get_reasoning_support, REASONING_LEVELS
        support = get_reasoning_support("ollama_cloud", "gpt-oss:20b")
        self.assertEqual(support.levels, REASONING_LEVELS)

    def test_unknown_provider_gets_optimistic_full_range(self):
        from core.providers.reasoning_support import get_reasoning_support, REASONING_LEVELS
        support = get_reasoning_support("openai", "o1")
        self.assertEqual(support.levels, REASONING_LEVELS)


# ---------------------------------------------------------------------------
# core/providers/capability_registry.py -- supports_reasoning binding fix
# ---------------------------------------------------------------------------

class TestCapabilityRegistrySupportsReasoningFix(unittest.TestCase):
    """
    Before this fix, supports_reasoning was never actually read from
    litellm's per-model data -- every registered model fell back to an
    always-empty provider default, resolving False even for o1/o3/etc.
    """

    def test_reasoning_capable_model_correctly_registers_true(self):
        import core.providers.capability_registry as cr
        sample = {
            "o1": {"litellm_provider": "openai", "mode": "chat",
                    "supports_reasoning": True, "supports_function_calling": True},
        }
        cr._registry.clear()
        cr._provider_caps.clear()
        cr._merge_litellm(sample)
        cap = cr.get_model("openai", "o1")
        self.assertIsNotNone(cap)
        self.assertTrue(cap.supports_reasoning)

    def test_non_reasoning_model_correctly_registers_false(self):
        import core.providers.capability_registry as cr
        sample = {
            "gpt-4o": {"litellm_provider": "openai", "mode": "chat",
                       "supports_reasoning": False, "supports_function_calling": True},
        }
        cr._registry.clear()
        cr._provider_caps.clear()
        cr._merge_litellm(sample)
        cap = cr.get_model("openai", "gpt-4o")
        self.assertIsNotNone(cap)
        self.assertFalse(cap.supports_reasoning)

    def test_litellm_gateway_cache_also_gets_correct_value(self):
        import core.providers.capability_registry as cr
        sample = {
            "claude-opus-4-1-20250805": {"litellm_provider": "anthropic", "mode": "chat",
                                          "supports_reasoning": True},
        }
        cr._registry.clear()
        cr._provider_caps.clear()
        cr._merge_litellm(sample)
        cap = cr.get_model("litellm", "claude-opus-4-1-20250805")
        self.assertIsNotNone(cap)
        self.assertTrue(cap.supports_reasoning)

    def test_existing_entry_promoted_to_true_on_merge(self):
        """Mirrors the existing vision/tool-call promotion pattern already
        used for merged entries -- reasoning should promote the same way."""
        import core.providers.capability_registry as cr
        from core.providers.capability_registry import ModelCapability
        cr._registry.clear()
        cr._provider_caps.clear()
        cr._register(ModelCapability(provider="openai", model="o1", supports_reasoning=False))
        sample = {"o1": {"litellm_provider": "openai", "mode": "chat", "supports_reasoning": True}}
        cr._merge_litellm(sample)
        cap = cr.get_model("openai", "o1")
        self.assertTrue(cap.supports_reasoning)


# ---------------------------------------------------------------------------
# providers/handlers/openai_handler.py -- try-once/learn param correction
# ---------------------------------------------------------------------------

class TestOpenAITokenParamRetry(unittest.TestCase):
    def test_detector_matches_real_shaped_rejection(self):
        from providers.handlers.openai_handler import _is_wrong_token_param_error

        class FakeRejection(Exception):
            def __init__(self, param, msg):
                super().__init__(msg)
                self.param = param

        exc = FakeRejection(
            "max_tokens",
            "Unsupported parameter: 'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead.",
        )
        self.assertTrue(_is_wrong_token_param_error(exc))

    def test_detector_rejects_unrelated_error(self):
        from providers.handlers.openai_handler import _is_wrong_token_param_error
        self.assertFalse(_is_wrong_token_param_error(ValueError("something unrelated")))

    def test_detector_rejects_error_with_no_param_attribute(self):
        from providers.handlers.openai_handler import _is_wrong_token_param_error
        self.assertFalse(_is_wrong_token_param_error(Exception("plain exception, no .param")))

    def test_retry_corrects_param_and_learns_for_future_calls(self):
        from providers.handlers.openai_handler import OpenAIHandler, _LEARNED_TOKEN_LIMIT_PARAM

        class FakeRejection(Exception):
            def __init__(self, param, msg):
                super().__init__(msg)
                self.param = param

        async def run():
            h = OpenAIHandler({"api_key": "sk-fake", "model": "future-reasoning-model-not-in-any-list"})
            _LEARNED_TOKEN_LIMIT_PARAM.pop(h.model, None)  # isolate from other test runs
            calls = {"n": 0}

            async def fake_create(**kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    assert "max_tokens" in kwargs
                    raise FakeRejection(
                        "max_tokens",
                        "Unsupported parameter: 'max_tokens' is not supported with this model. "
                        "Use 'max_completion_tokens' instead.",
                    )
                assert "max_completion_tokens" in kwargs
                return "OK"

            h.client.chat.completions.create = fake_create
            req = {"model": h.model, "max_tokens": 100}
            result = await h._create_with_token_param_retry(req)
            return result, calls["n"], dict(_LEARNED_TOKEN_LIMIT_PARAM)

        result, call_count, learned = asyncio.run(run())
        self.assertEqual(result, "OK")
        self.assertEqual(call_count, 2)
        self.assertEqual(learned.get("future-reasoning-model-not-in-any-list"), "max_completion_tokens")

    def test_unrelated_error_propagates_without_retry(self):
        from providers.handlers.openai_handler import OpenAIHandler

        async def run():
            h = OpenAIHandler({"api_key": "sk-fake", "model": "gpt-4o"})
            calls = {"n": 0}

            async def fake_create(**kwargs):
                calls["n"] += 1
                raise ValueError("totally unrelated failure")

            h.client.chat.completions.create = fake_create
            with self.assertRaises(ValueError):
                await h._create_with_token_param_retry({"model": h.model, "max_tokens": 100})
            return calls["n"]

        call_count = asyncio.run(run())
        self.assertEqual(call_count, 1)  # no pointless retry on an unrelated error


# ---------------------------------------------------------------------------
# api/schemas/requests.py -- SessionRequest.reasoning field validator
# ---------------------------------------------------------------------------

class TestSessionRequestReasoningValidator(unittest.TestCase):
    def test_valid_level_string_accepted(self):
        from api.schemas.requests import SessionRequest
        body = SessionRequest(provider="openai", model="gpt-4", reasoning="high")
        self.assertEqual(body.reasoning, "high")

    def test_off_string_normalised_to_none(self):
        from api.schemas.requests import SessionRequest
        body = SessionRequest(provider="openai", model="gpt-4", reasoning="off")
        self.assertIsNone(body.reasoning)

    def test_none_stays_none(self):
        from api.schemas.requests import SessionRequest
        body = SessionRequest(provider="openai", model="gpt-4", reasoning=None)
        self.assertIsNone(body.reasoning)

    def test_omitted_defaults_to_none(self):
        from api.schemas.requests import SessionRequest
        body = SessionRequest(provider="openai", model="gpt-4")
        self.assertIsNone(body.reasoning)

    def test_legacy_bool_true_becomes_medium(self):
        from api.schemas.requests import SessionRequest
        body = SessionRequest(provider="openai", model="gpt-4", reasoning=True)
        self.assertEqual(body.reasoning, "medium")

    def test_legacy_bool_false_becomes_none(self):
        from api.schemas.requests import SessionRequest
        body = SessionRequest(provider="openai", model="gpt-4", reasoning=False)
        self.assertIsNone(body.reasoning)

    def test_invalid_level_rejected(self):
        from api.schemas.requests import SessionRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            SessionRequest(provider="openai", model="gpt-4", reasoning="super-duper-high")


# ---------------------------------------------------------------------------
# api/session.py -- save-time capability check (binary gate + level snap +
# always-on handling)
# ---------------------------------------------------------------------------

def _mock_db_context():
    mock_db = AsyncMock()
    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session_local


class TestSessionReasoningCapabilityCheck(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_disabled_when_model_does_not_support_it_at_all(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest
        from core.providers.capability_registry import ModelCapability

        body = SessionRequest(provider="openai", model="gpt-4o", reasoning="medium")
        no_reasoning_cap = ModelCapability(provider="openai", model="gpt-4o", supports_reasoning=False)

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=no_reasoning_cap), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertIsNone(result["reasoning"])
        self.assertTrue(any("does not support reasoning" in w for w in result["warnings"]))

    async def test_reasoning_level_snapped_when_model_has_smaller_set(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest
        from core.providers.capability_registry import ModelCapability

        body = SessionRequest(provider="anthropic", model="claude-opus-4-8", reasoning="xhigh")
        cap = ModelCapability(provider="anthropic", model="claude-opus-4-8", supports_reasoning=True)

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=cap), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(result["reasoning"], "high")
        self.assertTrue(any("closest supported level" in w for w in result["warnings"]))

    async def test_reasoning_level_within_range_passes_through_unchanged(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest
        from core.providers.capability_registry import ModelCapability

        body = SessionRequest(provider="anthropic", model="claude-opus-4-8", reasoning="medium")
        cap = ModelCapability(provider="anthropic", model="claude-opus-4-8", supports_reasoning=True)

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=cap), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(result["reasoning"], "medium")
        self.assertEqual(result["warnings"], [])

    async def test_always_on_model_forces_medium_when_off_requested(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest
        from core.providers.capability_registry import ModelCapability

        body = SessionRequest(provider="anthropic", model="claude-fable-5", reasoning=None)
        cap = ModelCapability(provider="anthropic", model="claude-fable-5", supports_reasoning=True)

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=cap), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(result["reasoning"], "medium")
        self.assertTrue(any("cannot be set to off" in w for w in result["warnings"]))

    async def test_unknown_model_passes_reasoning_through_unchanged(self):
        from api.session import set_session
        from api.schemas.requests import SessionRequest

        body = SessionRequest(provider="openai", model="some-brand-new-model", reasoning="high")

        with patch("api.session.is_provider_key_configured", return_value=True), \
             patch("api.session.get_model", return_value=None), \
             patch("api.session.AsyncSessionLocal", _mock_db_context()), \
             patch("api.session.DBRepo.upsert_user_preferences", new=AsyncMock()), \
             patch("api.session.set_user_session"):
            result = await set_session(body, auth={"user_id": "u1"})

        self.assertEqual(result["reasoning"], "high")
        self.assertEqual(result["warnings"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
