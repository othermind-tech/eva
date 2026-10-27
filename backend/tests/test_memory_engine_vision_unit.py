# tests/test_memory_engine_vision_unit.py
"""
M-07 (chat-attachment half): a chat-attached image reaches a confirmed vision-capable model as
real image content, instead of only ever the "[IMAGE ATTACHMENT] ... refer to it" placeholder
note api/conversation_management.py injects -- which is ALL it ever became before, despite
that endpoint already gating the attach itself on supports_vision and its own comment claiming
"actual base64 is forwarded at inference time" (it never was).

Covers core/memory/engine.py's MemoryEngine._image_attachments_for_vision() (the new DB/blob
read, gated on a confirmed vision-capable model) and the updated
MemoryEngine._append_message_with_tools() (turns the placeholder message into real multimodal
content when this conversation has one).

Run with:
    pytest tests/test_memory_engine_vision_unit.py -v
"""

from __future__ import annotations

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.memory.engine import MemoryEngine

_FAKE_BYTES = b"not a real png, just some bytes"
_FAKE_B64 = base64.b64encode(_FAKE_BYTES).decode()


def _link(message_id="msg1", attachment_id="att1", attachment_type="image"):
    return SimpleNamespace(message_id=message_id, attachment_id=attachment_id, attachment_type=attachment_type)


def _attachment(mime_type="image/png", blob_path="a.png", project_id=None):
    return SimpleNamespace(mime_type=mime_type, blob_path=blob_path, project_id=project_id)


class TestImageAttachmentsForVision(unittest.IsolatedAsyncioTestCase):

    def _engine(self):
        return MemoryEngine(db=AsyncMock())

    async def test_not_vision_capable_does_no_work_at_all(self):
        engine = self._engine()
        with patch("core.providers.multimodal.is_vision_capable", return_value=False), \
             patch("core.db.repository.ConversationAttachmentRepo.list_for_conversation") as list_mock:
            out = await engine._image_attachments_for_vision(
                conversation_id="c1", user_id="u1", provider="p", model_name="m")
        self.assertEqual(out, {})
        list_mock.assert_not_called()

    async def test_vision_capable_with_a_real_image_attachment_returns_it(self):
        engine = self._engine()
        with patch("core.providers.multimodal.is_vision_capable", return_value=True), \
             patch("core.db.repository.ConversationAttachmentRepo.list_for_conversation",
                   new=AsyncMock(return_value=[_link()])), \
             patch("core.db.repository.AttachmentRepo.get", new=AsyncMock(return_value=_attachment())), \
             patch("utils.blob_store.read_file_blob", return_value=_FAKE_BYTES):
            out = await engine._image_attachments_for_vision(
                conversation_id="c1", user_id="u1", provider="p", model_name="m")
        self.assertEqual(out, {"msg1": ("image/png", _FAKE_B64)})

    async def test_a_non_image_link_is_skipped(self):
        engine = self._engine()
        with patch("core.providers.multimodal.is_vision_capable", return_value=True), \
             patch("core.db.repository.ConversationAttachmentRepo.list_for_conversation",
                   new=AsyncMock(return_value=[_link(attachment_type="file")])), \
             patch("core.db.repository.AttachmentRepo.get", new=AsyncMock()) as get_mock:
            out = await engine._image_attachments_for_vision(
                conversation_id="c1", user_id="u1", provider="p", model_name="m")
        self.assertEqual(out, {})
        get_mock.assert_not_called()

    async def test_a_link_with_no_message_id_is_skipped(self):
        engine = self._engine()
        with patch("core.providers.multimodal.is_vision_capable", return_value=True), \
             patch("core.db.repository.ConversationAttachmentRepo.list_for_conversation",
                   new=AsyncMock(return_value=[_link(message_id=None)])):
            out = await engine._image_attachments_for_vision(
                conversation_id="c1", user_id="u1", provider="p", model_name="m")
        self.assertEqual(out, {})

    async def test_a_missing_attachment_row_is_skipped(self):
        engine = self._engine()
        with patch("core.providers.multimodal.is_vision_capable", return_value=True), \
             patch("core.db.repository.ConversationAttachmentRepo.list_for_conversation",
                   new=AsyncMock(return_value=[_link()])), \
             patch("core.db.repository.AttachmentRepo.get", new=AsyncMock(return_value=None)):
            out = await engine._image_attachments_for_vision(
                conversation_id="c1", user_id="u1", provider="p", model_name="m")
        self.assertEqual(out, {})

    async def test_a_blob_read_failure_is_skipped_not_raised(self):
        engine = self._engine()
        with patch("core.providers.multimodal.is_vision_capable", return_value=True), \
             patch("core.db.repository.ConversationAttachmentRepo.list_for_conversation",
                   new=AsyncMock(return_value=[_link()])), \
             patch("core.db.repository.AttachmentRepo.get", new=AsyncMock(return_value=_attachment())), \
             patch("utils.blob_store.read_file_blob", side_effect=OSError("gone")):
            out = await engine._image_attachments_for_vision(
                conversation_id="c1", user_id="u1", provider="p", model_name="m")
        self.assertEqual(out, {})

    async def test_a_blob_that_no_longer_exists_on_disk_is_skipped(self):
        engine = self._engine()
        with patch("core.providers.multimodal.is_vision_capable", return_value=True), \
             patch("core.db.repository.ConversationAttachmentRepo.list_for_conversation",
                   new=AsyncMock(return_value=[_link()])), \
             patch("core.db.repository.AttachmentRepo.get", new=AsyncMock(return_value=_attachment())), \
             patch("utils.blob_store.read_file_blob", return_value=None):
            out = await engine._image_attachments_for_vision(
                conversation_id="c1", user_id="u1", provider="p", model_name="m")
        self.assertEqual(out, {})

    async def test_an_oversized_image_is_skipped(self):
        engine = self._engine()
        with patch("core.providers.multimodal.is_vision_capable", return_value=True), \
             patch("core.providers.multimodal.within_inline_limit", return_value=False), \
             patch("core.db.repository.ConversationAttachmentRepo.list_for_conversation",
                   new=AsyncMock(return_value=[_link()])), \
             patch("core.db.repository.AttachmentRepo.get", new=AsyncMock(return_value=_attachment())), \
             patch("utils.blob_store.read_file_blob", return_value=_FAKE_BYTES):
            out = await engine._image_attachments_for_vision(
                conversation_id="c1", user_id="u1", provider="p", model_name="m")
        self.assertEqual(out, {})

    async def test_two_image_attachments_both_come_back(self):
        engine = self._engine()
        links = [_link(message_id="msg1", attachment_id="att1"), _link(message_id="msg2", attachment_id="att2")]
        with patch("core.providers.multimodal.is_vision_capable", return_value=True), \
             patch("core.db.repository.ConversationAttachmentRepo.list_for_conversation",
                   new=AsyncMock(return_value=links)), \
             patch("core.db.repository.AttachmentRepo.get", new=AsyncMock(return_value=_attachment())), \
             patch("utils.blob_store.read_file_blob", return_value=_FAKE_BYTES):
            out = await engine._image_attachments_for_vision(
                conversation_id="c1", user_id="u1", provider="p", model_name="m")
        self.assertEqual(set(out.keys()), {"msg1", "msg2"})


class TestAppendMessageWithToolsImageSubstitution(unittest.TestCase):

    def _engine(self):
        return MemoryEngine(db=MagicMock())

    def _user_msg(self, id="msg1", content="[IMAGE ATTACHMENT]\nrefer to it"):
        return SimpleNamespace(id=id, role="user", content=content)

    def test_no_image_map_leaves_the_message_as_plain_text(self):
        engine, context = self._engine(), []
        engine._append_message_with_tools(context, self._user_msg(), {})
        self.assertEqual(context, [{"role": "user", "content": "[IMAGE ATTACHMENT]\nrefer to it"}])

    def test_a_matching_image_id_produces_multimodal_content(self):
        engine, context = self._engine(), []
        engine._append_message_with_tools(context, self._user_msg(), {}, {"msg1": ("image/png", _FAKE_B64)})
        self.assertEqual(context[0]["role"], "user")
        content = context[0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "[IMAGE ATTACHMENT]\nrefer to it"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertIn(_FAKE_B64, content[1]["image_url"]["url"])

    def test_a_non_matching_image_map_leaves_the_message_as_plain_text(self):
        engine, context = self._engine(), []
        engine._append_message_with_tools(context, self._user_msg(id="msg1"), {}, {"some-other-msg": ("image/png", "X")})
        self.assertEqual(context, [{"role": "user", "content": "[IMAGE ATTACHMENT]\nrefer to it"}])

    def test_a_normal_user_message_unrelated_to_any_attachment_is_unaffected(self):
        engine, context = self._engine(), []
        engine._append_message_with_tools(context, self._user_msg(id="msg2", content="hello"), {}, {"msg1": ("image/png", "X")})
        self.assertEqual(context, [{"role": "user", "content": "hello"}])

    def test_assistant_and_tool_message_handling_is_unaffected_by_the_new_parameter(self):
        engine, context = self._engine(), []
        assistant_msg = SimpleNamespace(id="a1", role="assistant", content="hi there")
        engine._append_message_with_tools(context, assistant_msg, {}, {"msg1": ("image/png", "X")})
        self.assertEqual(context, [{"role": "assistant", "content": "hi there"}])


if __name__ == "__main__":
    unittest.main()
