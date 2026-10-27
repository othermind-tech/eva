# tests/test_param_support_unit.py
"""
Unit tests for core/providers/param_support.py — mocked/pure, no live server.

Covers the V6.4 provider-agnostic max_tokens/temperature support lookup:
verifies each of the 6 provider rules independently, the fail-safe fallback
behaviour for unknown providers/exceptions, and the belt-and-suspenders
Anthropic-restriction check layered onto the LiteLLM passthrough path.

Run with:
    cd <project_root>
    python tests/test_param_support_unit.py
    (or: pytest tests/test_param_support_unit.py -v)
"""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.providers.param_support import get_param_support, ParamSupport


class TestAnthropicParamSupport(unittest.TestCase):
    def test_older_model_supports_temperature(self):
        r = get_param_support("anthropic", "claude-3-5-sonnet-20241022")
        self.assertTrue(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_tokens")

    def test_opus_4_1_supports_temperature(self):
        """Only 4.7+ is restricted -- earlier Opus 4.x point releases are fine."""
        r = get_param_support("anthropic", "claude-opus-4-1")
        self.assertTrue(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_tokens")

    def test_opus_4_7_temperature_restricted(self):
        r = get_param_support("anthropic", "claude-opus-4-7")
        self.assertFalse(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_tokens")

    def test_opus_4_8_temperature_restricted(self):
        r = get_param_support("anthropic", "claude-opus-4-8")
        self.assertFalse(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_tokens")

    def test_token_limit_param_is_always_max_tokens(self):
        """Anthropic's native API has no max_completion_tokens equivalent --
        must never be reported for this provider, restricted or not."""
        for model in ("claude-3-5-sonnet-20241022", "claude-opus-4-8", "claude-opus-4-1"):
            r = get_param_support("anthropic", model)
            self.assertEqual(r.token_limit_param, "max_tokens")

    def test_provider_name_is_case_insensitive(self):
        r = get_param_support("Anthropic", "claude-opus-4-8")
        self.assertFalse(r.supports_temperature)


class TestOpenAIParamSupport(unittest.TestCase):
    def test_standard_model_supports_temperature_and_max_tokens(self):
        r = get_param_support("openai", "gpt-4o")
        self.assertTrue(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_tokens")

    def test_o1_reasoning_model_uses_max_completion_tokens(self):
        r = get_param_support("openai", "o1")
        self.assertFalse(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_completion_tokens")

    def test_o3_reasoning_model_uses_max_completion_tokens(self):
        r = get_param_support("openai", "o3-mini")
        self.assertFalse(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_completion_tokens")

    def test_gpt5_reasoning_model_uses_max_completion_tokens(self):
        r = get_param_support("openai", "gpt-5-turbo")
        self.assertFalse(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_completion_tokens")

    def test_gpt4_is_not_matched_as_reasoning_model(self):
        """Regression guard against an overly broad prefix match."""
        r = get_param_support("openai", "gpt-4-turbo")
        self.assertTrue(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_tokens")


class TestGroqParamSupport(unittest.TestCase):
    def test_reasoning_capable_gpt_oss_model_supports_both(self):
        r = get_param_support("groq", "openai/gpt-oss-20b")
        self.assertTrue(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_completion_tokens")

    def test_standard_groq_model_supports_both(self):
        r = get_param_support("groq", "llama-3.3-70b-versatile")
        self.assertTrue(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_completion_tokens")


class TestOllamaParamSupport(unittest.TestCase):
    def test_local_ollama_always_supports_both(self):
        r = get_param_support("ollama", "llama3.2")
        self.assertTrue(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_tokens")

    def test_ollama_cloud_supports_both(self):
        r = get_param_support("ollama_cloud", "gpt-oss:20b")
        self.assertTrue(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_tokens")


class TestLiteLLMParamSupport(unittest.TestCase):
    def test_uses_litellm_supported_params_list(self):
        with patch("litellm.get_supported_openai_params", return_value=["temperature", "max_tokens", "top_p"]):
            r = get_param_support("litellm", "some-custom-model")
        self.assertTrue(r.supports_temperature)
        self.assertEqual(r.token_limit_param, "max_tokens")

    def test_prefers_max_completion_tokens_when_both_listed(self):
        with patch("litellm.get_supported_openai_params",
                    return_value=["temperature", "max_tokens", "max_completion_tokens"]):
            r = get_param_support("litellm", "some-model")
        self.assertEqual(r.token_limit_param, "max_completion_tokens")

    def test_no_temperature_in_supported_list(self):
        with patch("litellm.get_supported_openai_params", return_value=["max_tokens"]):
            r = get_param_support("litellm", "some-model")
        self.assertFalse(r.supports_temperature)

    def test_none_returned_by_litellm_falls_back_conservatively(self):
        """LiteLLM's own docstring: None means 'unmapped provider'."""
        with patch("litellm.get_supported_openai_params", return_value=None):
            r = get_param_support("litellm", "totally-unknown-model")
        self.assertFalse(r.supports_temperature)
        self.assertIsNone(r.token_limit_param)

    def test_litellm_lookup_exception_falls_back_conservatively(self):
        with patch("litellm.get_supported_openai_params", side_effect=RuntimeError("boom")):
            r = get_param_support("litellm", "some-model")
        self.assertFalse(r.supports_temperature)
        self.assertIsNone(r.token_limit_param)

    def test_anthropic_restriction_applied_even_via_litellm_passthrough(self):
        """
        The core regression this belt-and-suspenders check exists for:
        LiteLLM's own metadata does NOT encode the Opus 4.7+ restriction,
        so it must be applied independently when the model string is
        routed to Anthropic underneath.
        """
        with patch("litellm.get_supported_openai_params",
                    return_value=["temperature", "max_tokens", "max_completion_tokens"]):
            r = get_param_support("litellm", "anthropic/claude-opus-4-8")
        self.assertFalse(
            r.supports_temperature,
            "Opus 4.8 temperature restriction must apply even when LiteLLM's own "
            "metadata claims temperature is supported",
        )

    def test_anthropic_restriction_not_applied_to_non_anthropic_litellm_models(self):
        with patch("litellm.get_supported_openai_params", return_value=["temperature", "max_tokens"]):
            r = get_param_support("litellm", "cohere/command-r")
        self.assertTrue(r.supports_temperature)


class TestFailSafeFallback(unittest.TestCase):
    def test_unknown_provider_returns_conservative_fallback(self):
        r = get_param_support("some_future_provider_not_yet_supported", "some-model")
        self.assertFalse(r.supports_temperature)
        self.assertIsNone(r.token_limit_param)

    def test_empty_provider_string_does_not_raise(self):
        r = get_param_support("", "some-model")
        self.assertFalse(r.supports_temperature)
        self.assertIsNone(r.token_limit_param)

    def test_get_param_support_never_raises_on_provider_function_exception(self):
        """If a per-provider function ever raises, get_param_support must
        still return the conservative fallback, not propagate the exception."""
        with patch.dict(
            "core.providers.param_support._PROVIDER_HANDLERS",
            {"anthropic": lambda model: (_ for _ in ()).throw(RuntimeError("boom"))},
        ):
            r = get_param_support("anthropic", "claude-opus-4-8")
        self.assertFalse(r.supports_temperature)
        self.assertIsNone(r.token_limit_param)

    def test_returns_paramsupport_dataclass_instance(self):
        r = get_param_support("openai", "gpt-4o")
        self.assertIsInstance(r, ParamSupport)


if __name__ == "__main__":
    unittest.main(verbosity=2)
