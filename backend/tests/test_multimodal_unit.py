# tests/test_multimodal_unit.py
"""
M-07: core/providers/multimodal.py -- the shared helpers used by both entry points (MCP tool
result images, e.g. Playwright screenshots, and chat-attached images) that decide whether a
model is vision-capable and build the canonical (OpenAI-style) multimodal content shape.

Run with:
    pytest tests/test_multimodal_unit.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core.providers.multimodal import (
    build_multimodal_content,
    data_uri_to_anthropic_source,
    image_url_part,
    is_multimodal_content,
    is_vision_capable,
    text_part,
    within_inline_limit,
)


def _cap(supports_vision):
    m = MagicMock()
    m.supports_vision = supports_vision
    return m


class TestIsVisionCapable(unittest.TestCase):

    def test_true_only_on_a_confirmed_true_record(self):
        with patch("core.providers.capability_registry.get_model", return_value=_cap(True)):
            self.assertTrue(is_vision_capable("openai", "gpt-4o"))

    def test_false_on_a_confirmed_false_record(self):
        with patch("core.providers.capability_registry.get_model", return_value=_cap(False)):
            self.assertFalse(is_vision_capable("ollama_cloud", "gpt-oss:120b"))

    def test_false_when_there_is_no_cached_record_at_all(self):
        # The exact live case: every Ollama Cloud model on the account currently returns this.
        with patch("core.providers.capability_registry.get_model", return_value=None):
            self.assertFalse(is_vision_capable("ollama_cloud", "gpt-oss:120b"))

    def test_false_when_the_lookup_raises(self):
        with patch("core.providers.capability_registry.get_model", side_effect=RuntimeError("down")):
            self.assertFalse(is_vision_capable("anthropic", "claude-x"))

    def test_false_with_no_provider_or_model(self):
        self.assertFalse(is_vision_capable(None, "m"))
        self.assertFalse(is_vision_capable("p", None))
        self.assertFalse(is_vision_capable("", ""))

    def test_the_global_kill_switch_overrides_a_confirmed_true_record(self):
        with patch("core.providers.multimodal.EVA_VISION_ENABLED", False), \
             patch("core.providers.capability_registry.get_model", return_value=_cap(True)):
            self.assertFalse(is_vision_capable("openai", "gpt-4o"))


class TestWithinInlineLimit(unittest.TestCase):

    def test_boundaries(self):
        from core.providers.multimodal import IMAGE_INLINE_MAX_BYTES
        self.assertTrue(within_inline_limit(0))
        self.assertTrue(within_inline_limit(IMAGE_INLINE_MAX_BYTES))
        self.assertFalse(within_inline_limit(IMAGE_INLINE_MAX_BYTES + 1))

    def test_negative_size_is_rejected(self):
        self.assertFalse(within_inline_limit(-1))


class TestContentPartBuilders(unittest.TestCase):

    def test_image_url_part_shape(self):
        part = image_url_part("image/png", "QUJD")
        self.assertEqual(part, {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}})

    def test_image_url_part_falls_back_on_a_missing_mime_type(self):
        part = image_url_part("", "QUJD")
        self.assertIn("application/octet-stream", part["image_url"]["url"])

    def test_text_part_shape(self):
        self.assertEqual(text_part("hello"), {"type": "text", "text": "hello"})

    def test_build_multimodal_content_orders_text_before_images(self):
        content = build_multimodal_content("caption", [("image/png", "AAAA"), ("image/jpeg", "BBBB")])
        self.assertEqual(len(content), 3)
        self.assertEqual(content[0], {"type": "text", "text": "caption"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertIn("AAAA", content[1]["image_url"]["url"])
        self.assertIn("BBBB", content[2]["image_url"]["url"])

    def test_build_multimodal_content_with_no_text_omits_the_text_part(self):
        content = build_multimodal_content(None, [("image/png", "AAAA")])
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "image_url")

    def test_build_multimodal_content_empty_input_is_an_empty_list(self):
        self.assertEqual(build_multimodal_content(None, []), [])
        self.assertEqual(build_multimodal_content("", []), [])


class TestIsMultimodalContent(unittest.TestCase):

    def test_a_plain_string_is_not_multimodal(self):
        self.assertFalse(is_multimodal_content("hello"))

    def test_none_is_not_multimodal(self):
        self.assertFalse(is_multimodal_content(None))

    def test_a_content_part_list_is_multimodal(self):
        self.assertTrue(is_multimodal_content([{"type": "text", "text": "hi"}]))
        self.assertTrue(is_multimodal_content(build_multimodal_content("x", [("image/png", "A")])))

    def test_an_empty_list_is_not_treated_as_multimodal(self):
        # Nothing to render as multimodal -- callers should fall back to plain content.
        self.assertFalse(is_multimodal_content([]))

    def test_a_list_of_non_part_dicts_is_not_multimodal(self):
        self.assertFalse(is_multimodal_content([{"role": "user", "content": "x"}]))

    def test_a_list_of_non_dicts_is_not_multimodal(self):
        self.assertFalse(is_multimodal_content(["not", "a", "part"]))


class TestDataUriToAnthropicSource(unittest.TestCase):

    def test_parses_a_normal_data_uri(self):
        src = data_uri_to_anthropic_source("data:image/png;base64,QUJD")
        self.assertEqual(src, {"type": "base64", "media_type": "image/png", "data": "QUJD"})

    def test_round_trips_image_url_part(self):
        part = image_url_part("image/jpeg", "ZmFrZQ==")
        src = data_uri_to_anthropic_source(part["image_url"]["url"])
        self.assertEqual(src["media_type"], "image/jpeg")
        self.assertEqual(src["data"], "ZmFrZQ==")

    def test_a_real_http_url_is_not_a_data_uri(self):
        self.assertIsNone(data_uri_to_anthropic_source("https://example.com/x.png"))

    def test_garbage_input_returns_none_not_an_exception(self):
        for bad in (None, "", "data:image/png", 12345, {}):
            self.assertIsNone(data_uri_to_anthropic_source(bad))

    def test_missing_media_type_falls_back(self):
        src = data_uri_to_anthropic_source("data:;base64,QUJD")
        self.assertEqual(src["media_type"], "application/octet-stream")


if __name__ == "__main__":
    unittest.main()
