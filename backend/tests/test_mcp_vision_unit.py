# tests/test_mcp_vision_unit.py
"""
M-07 (MCP half): an MCP tool result image (e.g. a Playwright screenshot) reaches a confirmed
vision-capable model as a real image, instead of always being stripped to
"[image omitted: ...]" regardless of what the model can actually do.

Covers core/mcp/runtime.py's new branch in MCPRuntime.execute(), and
utils/message_builder.py::build_tool_message() turning a result that carries
core.mcp.runtime.IMAGE_RESULT_KEY into a follow-up image message.

Run with:
    pytest tests/test_mcp_vision_unit.py -v
"""

from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.mcp.runtime import IMAGE_RESULT_KEY, MCPRuntime, _content_block_to_image
from utils.message_builder import build_tool_message

_FAKE_PNG_B64 = base64.b64encode(b"not a real png, just some bytes").decode()


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _image_block(data_b64=_FAKE_PNG_B64, mime="image/png"):
    return SimpleNamespace(type="image", text=None, data=data_b64, mime_type=mime)


class TestContentBlockToImage(unittest.TestCase):

    def test_a_text_block_is_not_an_image(self):
        self.assertIsNone(_content_block_to_image(_text_block("hi")))

    def test_an_image_block_within_the_limit_is_returned(self):
        mime, data = _content_block_to_image(_image_block())
        self.assertEqual(mime, "image/png")
        self.assertEqual(data, _FAKE_PNG_B64)

    def test_an_image_block_over_the_limit_returns_none(self):
        with patch("core.mcp.runtime.within_inline_limit", return_value=False):
            self.assertIsNone(_content_block_to_image(_image_block()))

    def test_an_image_block_with_no_data_returns_none(self):
        self.assertIsNone(_content_block_to_image(_image_block(data_b64=None)))

    def test_missing_mime_type_falls_back(self):
        block = SimpleNamespace(type="image", text=None, data=_FAKE_PNG_B64, mime_type=None)
        mime, _ = _content_block_to_image(block)
        self.assertEqual(mime, "application/octet-stream")


class TestMCPRuntimeExecuteVisionBranch(unittest.IsolatedAsyncioTestCase):

    async def _run(self, content, vision_capable, session=None):
        db = AsyncMock()

        config_result = MagicMock()
        config_result.scalars.return_value.first.return_value = MagicMock(id="cfg1")
        tool_meta_result = MagicMock()
        tool_meta_result.scalars.return_value.first.return_value = MagicMock()
        db.execute = AsyncMock(side_effect=[config_result, tool_meta_result])

        raw_result = SimpleNamespace(content=content)
        with patch("core.mcp.runtime.mcp_session_pool") as pool, \
             patch("core.mcp.runtime.is_vision_capable", return_value=vision_capable), \
             patch("config.settings.get_user_session", return_value=session or {"provider": "p", "model": "m"}):
            pool.call_tool = AsyncMock(return_value=raw_result)
            return await MCPRuntime.execute(
                db=db, user_id="u1", tool_name="playwright__browser_screenshot", arguments={},
            )

    async def test_not_vision_capable_keeps_the_placeholder_list(self):
        result = await self._run([_text_block("ok"), _image_block()], vision_capable=False)
        self.assertIsInstance(result.result, list)
        self.assertIn("ok", result.result)
        self.assertTrue(any("image omitted" in s for s in result.result))
        self.assertNotIn(IMAGE_RESULT_KEY, result.result if isinstance(result.result, dict) else {})

    async def test_vision_capable_with_an_image_produces_the_marker_shape(self):
        result = await self._run([_text_block("console clean"), _image_block()], vision_capable=True)
        self.assertIsInstance(result.result, dict)
        self.assertEqual(result.result["text"], "console clean")
        self.assertEqual(len(result.result[IMAGE_RESULT_KEY]), 1)
        self.assertEqual(result.result[IMAGE_RESULT_KEY][0]["mime_type"], "image/png")
        self.assertEqual(result.result[IMAGE_RESULT_KEY][0]["data_b64"], _FAKE_PNG_B64)

    async def test_vision_capable_but_no_image_in_the_result_is_unaffected(self):
        result = await self._run([_text_block("a"), _text_block("b")], vision_capable=True)
        self.assertEqual(result.result, ["a", "b"])

    async def test_vision_capable_but_the_image_is_too_large_falls_back_to_placeholder(self):
        with patch("core.mcp.runtime.within_inline_limit", return_value=False):
            result = await self._run([_image_block()], vision_capable=True)
        self.assertIsInstance(result.result, list)
        self.assertTrue(any("image omitted" in s for s in result.result))

    async def test_a_broken_session_lookup_does_not_break_the_call(self):
        db = AsyncMock()
        config_result = MagicMock()
        config_result.scalars.return_value.first.return_value = MagicMock(id="cfg1")
        tool_meta_result = MagicMock()
        tool_meta_result.scalars.return_value.first.return_value = MagicMock()
        db.execute = AsyncMock(side_effect=[config_result, tool_meta_result])
        raw_result = SimpleNamespace(content=[_text_block("ok")])
        with patch("core.mcp.runtime.mcp_session_pool") as pool, \
             patch("config.settings.get_user_session", side_effect=RuntimeError("no session")):
            pool.call_tool = AsyncMock(return_value=raw_result)
            result = await MCPRuntime.execute(db=db, user_id="u1", tool_name="playwright__x", arguments={})
        self.assertEqual(result.result, ["ok"])


class TestBuildToolMessageWithImages(unittest.TestCase):

    def test_no_images_returns_a_single_message_unchanged(self):
        msgs = build_tool_message(tool_call_id="c1", tool_name="read_file", result={"content": "hi"})
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "tool")
        self.assertEqual(json.loads(msgs[0]["content"]), {"content": "hi"})

    def test_an_image_result_produces_a_tool_message_plus_a_followup_user_message(self):
        result = {"text": "console clean", IMAGE_RESULT_KEY: [{"mime_type": "image/png", "data_b64": _FAKE_PNG_B64}]}
        msgs = build_tool_message(tool_call_id="c1", tool_name="playwright__browser_screenshot", result=result)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "tool")
        self.assertNotIn(IMAGE_RESULT_KEY, msgs[0]["content"])
        self.assertEqual(json.loads(msgs[0]["content"]), {"text": "console clean"})
        self.assertEqual(msgs[1]["role"], "user")
        self.assertEqual(msgs[1]["content"][0]["type"], "image_url")
        self.assertIn(_FAKE_PNG_B64, msgs[1]["content"][0]["image_url"]["url"])

    def test_the_original_result_dict_is_never_mutated(self):
        """The exact bug this must not reintroduce: `result` is the SAME object
        core/agent_engine.py already yielded via ToolResultChunk and is about to persist to
        the DB via on_step_complete() -- popping the image key out of it in place would
        silently strip the image from both."""
        result = {"text": "x", IMAGE_RESULT_KEY: [{"mime_type": "image/png", "data_b64": "AAAA"}]}
        original = dict(result)
        build_tool_message(tool_call_id="c1", tool_name="t", result=result)
        self.assertEqual(result, original)
        self.assertIn(IMAGE_RESULT_KEY, result)

    def test_multiple_images_each_become_their_own_content_part(self):
        result = {"text": "", IMAGE_RESULT_KEY: [
            {"mime_type": "image/png", "data_b64": "AAAA"},
            {"mime_type": "image/jpeg", "data_b64": "BBBB"},
        ]}
        msgs = build_tool_message(tool_call_id="c1", tool_name="t", result=result)
        self.assertEqual(len(msgs[1]["content"]), 2)

    def test_an_empty_images_list_behaves_like_no_images(self):
        result = {"text": "x", IMAGE_RESULT_KEY: []}
        msgs = build_tool_message(tool_call_id="c1", tool_name="t", result=result)
        self.assertEqual(len(msgs), 1)

    def test_a_non_dict_result_is_unaffected(self):
        msgs = build_tool_message(tool_call_id="c1", tool_name="t", result="plain string result")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(json.loads(msgs[0]["content"]), "plain string result")


if __name__ == "__main__":
    unittest.main()
