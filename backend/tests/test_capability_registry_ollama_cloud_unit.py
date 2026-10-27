# tests/test_capability_registry_ollama_cloud_unit.py
"""
A-17: the context-budget watchdog fell back to a flat 8,192-token guess for
every Ollama Cloud model, because core/providers/capability_registry.py's
LiteLLM merge never included "ollama"/"ollama_cloud" in its provider maps --
even though LiteLLM's own public data already carries the real number (e.g.
gpt-oss:120b: 131,072, confirmed empirically against the live LiteLLM JSON).

LiteLLM tags both local and cloud Ollama under one litellm_provider ==
"ollama", distinguished only by a "-cloud" suffix on the model name. Local
Ollama is deliberately excluded from this fix: its real usable context
depends on the user's own num_ctx configuration, which this static data
can't know, so surfacing a number for it would risk a confidently WRONG
value -- the one thing this fix must not do.

Covers:
  1. core/providers/capability_registry.py::_resolve_ollama_cloud_model_name()
     -- the pure name-resolution function (cloud vs. local vs. malformed).
  2. core/providers/capability_registry.py::_merge_litellm() -- the actual
     merge behaviour: ollama_cloud entries registered with real data, local
     ollama entries never registered, every other provider unaffected.
"""

from __future__ import annotations

import unittest


class TestResolveOllamaCloudModelName(unittest.TestCase):

    def _resolve(self, name):
        from core.providers.capability_registry import _resolve_ollama_cloud_model_name
        return _resolve_ollama_cloud_model_name(name)

    def test_a_cloud_entry_resolves_to_the_bare_model_name(self):
        self.assertEqual(self._resolve("ollama/gpt-oss:120b-cloud"), "gpt-oss:120b")

    def test_a_local_entry_with_no_cloud_suffix_resolves_to_none(self):
        self.assertIsNone(self._resolve("ollama/llama3.1"))

    def test_a_non_ollama_prefixed_name_resolves_to_none(self):
        self.assertIsNone(self._resolve("groq/openai/gpt-oss-120b-cloud"))

    def test_the_bare_ollama_prefix_with_only_the_suffix_resolves_to_none(self):
        # "ollama/-cloud" -> bare name would be "" -- must not register an
        # empty model name.
        self.assertIsNone(self._resolve("ollama/-cloud"))

    def test_a_name_that_is_just_the_suffix_with_no_prefix_resolves_to_none(self):
        self.assertIsNone(self._resolve("-cloud"))


class TestMergeLitellmOllamaCloud(unittest.TestCase):
    """A-17: exercises the real _merge_litellm() merge path with synthetic
    LiteLLM-shaped data (no network), matching the existing pattern in
    tests/test_reasoning_levels_unit.py's TestCapabilityRegistrySupportsReasoningFix."""

    def setUp(self):
        import core.providers.capability_registry as cr
        self.cr = cr
        cr._registry.clear()
        cr._provider_caps.clear()

    def test_a_cloud_model_is_registered_under_ollama_cloud_with_the_real_context_window(self):
        sample = {
            "ollama/gpt-oss:120b-cloud": {
                "litellm_provider": "ollama", "mode": "chat",
                "max_input_tokens": 131072, "max_output_tokens": 131072,
                "input_cost_per_token": 0.0, "output_cost_per_token": 0.0,
                "supports_function_calling": True,
            },
        }
        self.cr._merge_litellm(sample)
        cap = self.cr.get_model("ollama_cloud", "gpt-oss:120b")
        self.assertIsNotNone(cap)
        self.assertEqual(cap.max_context_tokens, 131072)
        self.assertEqual(cap.provider, "ollama_cloud")
        self.assertEqual(cap.model, "gpt-oss:120b")

    def test_a_local_only_model_is_never_registered_under_any_provider(self):
        sample = {
            "ollama/llama3.1": {
                "litellm_provider": "ollama", "mode": "chat",
                "max_input_tokens": 131072,
            },
        }
        self.cr._merge_litellm(sample)
        self.assertIsNone(self.cr.get_model("ollama_cloud", "llama3.1"))
        self.assertIsNone(self.cr.get_model("ollama", "llama3.1"))

    def test_local_and_cloud_entries_for_the_same_base_model_are_kept_separate(self):
        # The local entry ("ollama/llama3.1", no suffix) must never register
        # anywhere -- only the "-cloud" entry does, under provider
        # "ollama_cloud" and the bare model name with the suffix stripped.
        sample = {
            "ollama/llama3.1": {
                "litellm_provider": "ollama", "mode": "chat", "max_input_tokens": 8192,
            },
            "ollama/llama3.1-cloud": {
                "litellm_provider": "ollama", "mode": "chat", "max_input_tokens": 131072,
            },
        }
        self.cr._merge_litellm(sample)
        cloud_cap = self.cr.get_model("ollama_cloud", "llama3.1")
        self.assertIsNotNone(cloud_cap)
        self.assertEqual(cloud_cap.max_context_tokens, 131072)
        self.assertIsNone(self.cr.get_model("ollama", "llama3.1"))
        self.assertIsNone(self.cr.get_model("ollama", "llama3.1-cloud"))
        self.assertIsNone(self.cr.get_model("ollama_cloud", "llama3.1-cloud"))

    def test_silent_dataset_means_reasoning_is_offered_not_switched_off(self):
        """Regression (A-17 merge): LiteLLM's "-cloud" entries carry no
        supports_reasoning key; that must not read as 'unsupported'."""
        sample = {
            "ollama/gpt-oss:20b-cloud": {
                "litellm_provider": "ollama", "mode": "chat", "max_input_tokens": 131072,
                "supports_function_calling": True,
            },
        }
        self.cr._merge_litellm(sample)
        self.assertTrue(self.cr.get_model("ollama_cloud", "gpt-oss:20b").supports_reasoning)

    def test_an_explicit_false_from_litellm_is_still_honoured(self):
        sample = {
            "ollama/some-model-cloud": {
                "litellm_provider": "ollama", "mode": "chat", "max_input_tokens": 8192,
                "supports_reasoning": False,
            },
        }
        self.cr._merge_litellm(sample)
        self.assertFalse(self.cr.get_model("ollama_cloud", "some-model").supports_reasoning)

    def test_an_explicit_true_from_litellm_is_honoured(self):
        sample = {
            "ollama/deepseek-v3.1:671b-cloud": {
                "litellm_provider": "ollama", "mode": "chat", "max_input_tokens": 131072,
                "supports_reasoning": True,
            },
        }
        self.cr._merge_litellm(sample)
        self.assertTrue(self.cr.get_model("ollama_cloud", "deepseek-v3.1:671b").supports_reasoning)

    def test_silence_for_other_providers_stays_unsupported(self):
        sample = {"gpt-4": {"litellm_provider": "openai", "mode": "chat", "max_input_tokens": 8192}}
        self.cr._merge_litellm(sample)
        self.assertFalse(self.cr.get_model("openai", "gpt-4").supports_reasoning)

    def test_other_providers_are_completely_unaffected(self):
        sample = {
            "gpt-4o": {"litellm_provider": "openai", "mode": "chat", "max_input_tokens": 128000},
            "ollama/gpt-oss:120b-cloud": {
                "litellm_provider": "ollama", "mode": "chat", "max_input_tokens": 131072,
            },
        }
        self.cr._merge_litellm(sample)
        openai_cap = self.cr.get_model("openai", "gpt-4o")
        self.assertIsNotNone(openai_cap)
        self.assertEqual(openai_cap.max_context_tokens, 128000)

    def test_the_litellm_gateway_cache_keeps_the_raw_routing_key_unchanged(self):
        # Part 2 of _merge_litellm (the "litellm" gateway superset cache) is
        # untouched by the ollama_cloud special case -- it should still store
        # the raw LiteLLM key, exactly like every other provider's entries do.
        sample = {
            "ollama/gpt-oss:120b-cloud": {
                "litellm_provider": "ollama", "mode": "chat", "max_input_tokens": 131072,
            },
        }
        self.cr._merge_litellm(sample)
        gw_cap = self.cr.get_model("litellm", "ollama/gpt-oss:120b-cloud")
        self.assertIsNotNone(gw_cap)
        self.assertEqual(gw_cap.max_context_tokens, 131072)

    def test_context_watchdog_no_longer_needs_the_fallback_for_gpt_oss_120b(self):
        """End-to-end for A-17's actual symptom: AgentEngine._remaining_context_budget()
        reads capability_registry.get_model(provider, model).max_context_tokens --
        confirm that now resolves to the real number, not the 8,192 fallback."""
        from config.settings import CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS
        sample = {
            "ollama/gpt-oss:120b-cloud": {
                "litellm_provider": "ollama", "mode": "chat",
                "max_input_tokens": 131072, "max_output_tokens": 131072,
            },
        }
        self.cr._merge_litellm(sample)
        cap = self.cr.get_model("ollama_cloud", "gpt-oss:120b")
        max_tokens = (cap.max_context_tokens if cap else None) or CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS
        self.assertEqual(max_tokens, 131072)
        self.assertNotEqual(max_tokens, CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS)


if __name__ == "__main__":
    unittest.main()
