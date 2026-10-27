# tests/test_anthropic_vision_unit.py
"""
M-07 (Anthropic half): _convert_messages() translates EVA's canonical (OpenAI-style)
multimodal content into Anthropic's own image-block shape, and keeps every message role
alternating -- a requirement Anthropic's API enforces strictly -- even when M-07 injects a
follow-up image message right after a tool-result turn (both role="user").

Run with:
    pytest tests/test_anthropic_vision_unit.py -v
"""

from __future__ import annotations

import unittest

from providers.handlers.anthropic_handler import (
    _anthropic_content_blocks,
    _convert_messages,
    _merge_adjacent_same_role_turns,
)
from core.providers.multimodal import build_multimodal_content


class TestAnthropicContentBlocks(unittest.TestCase):

    def test_a_text_part_becomes_an_anthropic_text_block(self):
        blocks = _anthropic_content_blocks([{"type": "text", "text": "hello"}])
        self.assertEqual(blocks, [{"type": "text", "text": "hello"}])

    def test_an_image_url_part_becomes_an_anthropic_image_block(self):
        content = build_multimodal_content(None, [("image/png", "QUJD")])
        blocks = _anthropic_content_blocks(content)
        self.assertEqual(blocks, [{"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": "QUJD",
        }}])

    def test_text_and_image_together_preserve_order(self):
        content = build_multimodal_content("caption", [("image/png", "QUJD")])
        blocks = _anthropic_content_blocks(content)
        self.assertEqual([b["type"] for b in blocks], ["text", "image"])

    def test_a_non_data_uri_image_is_dropped_not_sent_malformed(self):
        blocks = _anthropic_content_blocks([
            {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
        ])
        self.assertEqual(blocks, [])

    def test_an_unrecognised_part_type_is_dropped(self):
        blocks = _anthropic_content_blocks([{"type": "audio_url", "audio_url": {"url": "x"}}])
        self.assertEqual(blocks, [])


class TestMergeAdjacentSameRoleTurns(unittest.TestCase):

    def test_already_alternating_messages_are_unchanged(self):
        msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        self.assertEqual(_merge_adjacent_same_role_turns(msgs), msgs)

    def test_two_adjacent_user_string_turns_are_merged_into_one(self):
        merged = _merge_adjacent_same_role_turns([
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["content"], [
            {"type": "text", "text": "first"}, {"type": "text", "text": "second"},
        ])

    def test_a_list_content_turn_merges_with_a_string_content_turn(self):
        merged = _merge_adjacent_same_role_turns([
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "r"}]},
            {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A"}}]},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["content"]), 2)
        self.assertEqual(merged[0]["content"][0]["type"], "tool_result")
        self.assertEqual(merged[0]["content"][1]["type"], "image")

    def test_an_empty_string_content_contributes_nothing_when_merged(self):
        merged = _merge_adjacent_same_role_turns([
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "real text"},
        ])
        self.assertEqual(merged[0]["content"], [{"type": "text", "text": "real text"}])

    def test_three_consecutive_same_role_turns_all_merge_into_one(self):
        merged = _merge_adjacent_same_role_turns([
            {"role": "user", "content": "a"}, {"role": "user", "content": "b"}, {"role": "user", "content": "c"},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["content"]), 3)

    def test_the_input_list_and_its_entries_are_not_mutated(self):
        original = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
        snapshot = [dict(m) for m in original]
        _merge_adjacent_same_role_turns(original)
        self.assertEqual(original, snapshot)

    def test_non_adjacent_same_role_turns_are_not_merged(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        self.assertEqual(_merge_adjacent_same_role_turns(msgs), msgs)


class TestConvertMessagesEndToEnd(unittest.TestCase):
    """The real entry point, exactly as AnthropicHandler._build_request calls it."""

    def test_a_plain_text_user_message_is_unaffected(self):
        out = _convert_messages([{"role": "user", "content": "hi"}])
        self.assertEqual(out, [{"role": "user", "content": "hi"}])

    def test_a_multimodal_user_message_is_translated(self):
        content = build_multimodal_content("what is this?", [("image/png", "QUJD")])
        out = _convert_messages([{"role": "user", "content": content}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "user")
        self.assertEqual(out[0]["content"][0], {"type": "text", "text": "what is this?"})
        self.assertEqual(out[0]["content"][1]["type"], "image")

    def test_a_tool_result_followed_by_an_image_message_becomes_one_user_turn(self):
        """The exact M-07 pattern build_tool_message() produces: a role="tool" message, then a
        role="user" image-only message right after it. Anthropic must see ONE user turn, not
        two adjacent ones (which its API rejects)."""
        image_content = build_multimodal_content(None, [("image/png", "QUJD")])
        out = _convert_messages([
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "browser_screenshot", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "name": "browser_screenshot", "content": '{"text": "clean"}'},
            {"role": "user", "content": image_content},
        ])
        roles = [m["role"] for m in out]
        self.assertEqual(roles, ["assistant", "user"])  # exactly one user turn, not two
        user_turn = out[1]
        types = [b["type"] for b in user_turn["content"]]
        self.assertIn("tool_result", types)
        self.assertIn("image", types)

    def test_a_real_user_question_right_after_an_attachment_context_message_merges_too(self):
        """api/conversation_management.py injects the attachment note as its own role="user"
        DB row; the user's real next message is a separate row too. If they land adjacent in
        the reloaded history (no assistant turn between them yet), Anthropic must still see
        one user turn."""
        out = _convert_messages([
            {"role": "user", "content": "[IMAGE ATTACHMENT]\nrefer to it"},
            {"role": "user", "content": "what does it show?"},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "user")

    def test_multiple_tool_calls_each_with_an_image_all_merge_into_one_user_turn(self):
        content1 = build_multimodal_content(None, [("image/png", "AAAA")])
        content2 = build_multimodal_content(None, [("image/png", "BBBB")])
        out = _convert_messages([
            {"role": "tool", "tool_call_id": "c1", "name": "t1", "content": "{}"},
            {"role": "user", "content": content1},
            {"role": "tool", "tool_call_id": "c2", "name": "t2", "content": "{}"},
            {"role": "user", "content": content2},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "user")
        types = [b["type"] for b in out[0]["content"]]
        self.assertEqual(types.count("tool_result"), 2)
        self.assertEqual(types.count("image"), 2)

    def test_a_tool_result_with_no_image_still_behaves_exactly_as_before(self):
        out = _convert_messages([
            {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": '{"content": "hi"}'},
        ])
        self.assertEqual(out, [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": '{"content": "hi"}'},
        ]}])

    def test_assistant_and_user_turns_still_alternate_normally(self):
        out = _convert_messages([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
        ])
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "user"])


if __name__ == "__main__":
    unittest.main()
