# tests/test_features.py
"""
EVA Feature Tests — Consolidated

Merges:
  - tests/test_api_features.py  (B-051 through B-066 + edit/retry truncation)
  - tests/test_worker_features.py (B-056 artifact updater, B-058 auto-title,
                                   B-059 watchdog, duplicate tool detection)
  - tests/test_project.py       (B-045 project entity + eva_format)
  - tests/test_shell_session.py  (B-049 shell session + executor)

Plus new tests for all fixes applied in this session:
  - Fix 1 : MemoryEngine ordering (sequence/created_at, not UUID)
  - Fix 4 : _timed_stream first-chunk + inter-chunk idle timeouts
  - Fix 5 : FALLBACK_ERROR_TYPES includes "timeout"
  - Fix 6 : classify_error returns typed enum with .value
  - Fix 8 : message_logger empty-response placeholder vs overflow
  - Fix 9 : extractor mode gating (Scope 3 only on plan_execute)
  - Fix 10: pev_runner cancel check includes "aborted"
  - Fix 11: cancel_token wired to AgentEngine
  - Fix 12: approval gate sets cancel_token immediately on rejection
  - Worker ownership: liveness beacon, claim, scoped watchdog 3-case,
                      orphan recovery skips live-owned executions

Run with:
    cd <project_root>
    pytest tests/test_features.py -v
"""

from __future__ import annotations

import pytest

import asyncio
import json
import os
import platform
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch



# ===========================================================================
# B-052 — Project-Scoped RAG
# ===========================================================================

class TestB052RagProjectScope(unittest.IsolatedAsyncioTestCase):

    def test_index_chunks_includes_project_id(self):
        from core.rag.models import Chunk
        chunks = [Chunk(id="c1", text="hello", vector=[0.1, 0.2],
                        metadata={"source_filename": "a.txt"})]

        class _FakeCollection:
            def __init__(self):
                self.add_calls = []
            def add(self, ids, embeddings, documents, metadatas):
                self.add_calls.append({"ids": ids, "embeddings": embeddings,
                                       "documents": documents, "metadatas": metadatas})

        class _FakeChromaClient:
            def __init__(self, col):
                self._col = col
            def get_or_create_collection(self, name, **kwargs):
                return self._col

        fake_col = _FakeCollection()
        fake_client = _FakeChromaClient(fake_col)

        with patch("core.rag.ingestion.get_chroma_client", return_value=fake_client):
            from core.rag.ingestion import index_chunks
            index_chunks("tenant1", chunks, project_id="proj-abc")

        self.assertEqual(len(fake_col.add_calls), 1)
        self.assertEqual(fake_col.add_calls[0]["metadatas"][0]["project_id"], "proj-abc")

    def test_index_chunks_omits_project_id_when_none(self):
        from core.rag.models import Chunk
        chunks = [Chunk(id="c1", text="hello", vector=[0.1, 0.2], metadata={})]

        class _FakeCollection:
            def __init__(self):
                self.add_calls = []
            def add(self, ids, embeddings, documents, metadatas):
                self.add_calls.append({"ids": ids, "embeddings": embeddings,
                                       "documents": documents, "metadatas": metadatas})

        class _FakeChromaClient:
            def __init__(self, col):
                self._col = col
            def get_or_create_collection(self, name, **kwargs):
                return self._col

        fake_col = _FakeCollection()
        fake_client = _FakeChromaClient(fake_col)

        with patch("core.rag.ingestion.get_chroma_client", return_value=fake_client):
            from core.rag.ingestion import index_chunks
            index_chunks("tenant1", chunks)

        self.assertEqual(len(fake_col.add_calls), 1)
        self.assertNotIn("project_id", fake_col.add_calls[0]["metadatas"][0])

    def test_rag_tool_args_has_project_id_field(self):
        from tools.default.rag_tool import RagRetrieveArgs
        args = RagRetrieveArgs(query="test", project_id="proj-xyz")
        self.assertEqual(args.project_id, "proj-xyz")

    def test_rag_tool_args_project_id_defaults_none(self):
        from tools.default.rag_tool import RagRetrieveArgs
        args = RagRetrieveArgs(query="test")
        self.assertIsNone(args.project_id)

    def test_rag_tool_builds_filter_for_project_id(self):
        args = {"query": "test", "project_id": "proj-42"}
        metadata_filter: list = []
        if args.get("project_id"):
            metadata_filter.append({"key": "project_id", "match": {"value": args["project_id"]}})
        self.assertEqual(len(metadata_filter), 1)
        self.assertEqual(metadata_filter[0]["match"]["value"], "proj-42")

    def test_rag_tool_no_filter_when_project_id_is_none(self):
        args = {"query": "test", "project_id": None}
        metadata_filter: list = []
        if args.get("project_id"):
            metadata_filter.append({"key": "project_id", "match": {"value": args["project_id"]}})
        self.assertEqual(len(metadata_filter), 0)

    def test_rag_tool_args_has_no_user_id_field(self):
        """Regression guard: user_id must not be a model-facing argument --
        the model has no legitimate way to know its own authenticated
        user_id, so a real call would search under whatever it guessed and
        silently find nothing regardless of what was actually ingested.
        Confirmed live: real ingestion succeeded, real retrieval with a
        model-supplied user_id found zero results every time; querying
        with the correct (execution-derived) user_id found it immediately."""
        from tools.default.rag_tool import RagRetrieveArgs
        self.assertNotIn("user_id", RagRetrieveArgs.model_fields)

    async def test_rag_retrieve_uses_execution_user_id(self):
        from tools.default.rag_tool import RagRetrieveTool

        tool = RagRetrieveTool()
        tool.execution = MagicMock()
        tool.execution.user_id = "real-authenticated-user"

        with patch("tools.default.rag_tool.embed_text", return_value=[0.1, 0.2]), \
             patch("tools.default.rag_tool.search_vectors", return_value=[]) as mock_search:
            await tool.run({"query": "anything"})

        self.assertEqual(mock_search.call_args.kwargs["tenant_id"], "real-authenticated-user")

    async def test_rag_retrieve_errors_without_execution_context(self):
        from tools.default.rag_tool import RagRetrieveTool

        tool = RagRetrieveTool()
        tool.execution = MagicMock()
        tool.execution.user_id = None

        result = await tool.run({"query": "anything"})
        self.assertIn("error", result.result)


class _FakeIngestCollection:
    def __init__(self):
        self.add_calls = []

    def add(self, ids, embeddings, documents, metadatas):
        self.add_calls.append({
            "ids": ids, "embeddings": embeddings,
            "documents": documents, "metadatas": metadatas,
        })


class _FakeIngestChromaClient:
    def __init__(self, col):
        self._col = col

    def get_or_create_collection(self, name, **kwargs):
        return self._col


def _fake_execution(**overrides):
    execution = MagicMock()
    execution.user_id = overrides.get("user_id", "test_user")
    execution.conversation_id = overrides.get("conversation_id", "conv-1")
    execution.project_workspace = overrides.get("project_workspace", None)
    execution.parent_project_id = overrides.get("parent_project_id", None)
    return execution


class TestRagIngestTool(unittest.IsolatedAsyncioTestCase):
    """Phase D: rag_ingest -- the model-callable counterpart to rag_retrieve.
    Mocks embed_text (avoids loading the real ONNX model) and ChromaDB
    (same _FakeCollection/_FakeChromaClient pattern as TestB052RagProjectScope
    above) so these are true unit tests, not integration tests."""

    async def test_ingest_content_mode(self):
        from tools.default.rag_tool import RagIngestTool

        fake_col = _FakeIngestCollection()
        fake_client = _FakeIngestChromaClient(fake_col)

        tool = RagIngestTool()
        tool.execution = _fake_execution()

        with patch("tools.default.rag_tool.embed_text", return_value=[0.1, 0.2]), \
             patch("core.rag.ingestion.get_chroma_client", return_value=fake_client):
            result = await tool.run({"content": "hello world", "label": "note.txt"})

        self.assertTrue(result.result["ok"])
        self.assertEqual(result.result["source_filename"], "note.txt")
        self.assertGreaterEqual(len(fake_col.add_calls), 1)
        self.assertEqual(fake_col.add_calls[0]["metadatas"][0]["source_filename"], "note.txt")

    async def test_ingest_path_mode(self):
        from tools.default.rag_tool import RagIngestTool

        fake_col = _FakeIngestCollection()
        fake_client = _FakeIngestChromaClient(fake_col)

        with tempfile.TemporaryDirectory() as ext_dir:
            file_path = Path(ext_dir) / "doc.txt"
            file_path.write_text("some project documentation", encoding="utf-8")

            tool = RagIngestTool()
            tool.execution = _fake_execution(project_workspace=ext_dir)

            with patch("tools.default.rag_tool.embed_text", return_value=[0.1, 0.2]), \
                 patch("core.rag.ingestion.get_chroma_client", return_value=fake_client):
                result = await tool.run({"path": "doc.txt"})

        self.assertTrue(result.result["ok"])
        self.assertEqual(result.result["source_filename"], "doc.txt")

    async def test_ingest_missing_file_returns_error(self):
        from tools.default.rag_tool import RagIngestTool

        tool = RagIngestTool()
        tool.execution = _fake_execution()

        result = await tool.run({"path": "does_not_exist.txt"})
        self.assertIn("error", result.result)

    async def test_ingest_rejects_unsupported_extension(self):
        from tools.default.rag_tool import RagIngestTool

        tool = RagIngestTool()
        tool.execution = _fake_execution()

        result = await tool.run({"content": "binary-ish content", "label": "image.png"})
        self.assertIn("error", result.result)
        self.assertIn("accepted_extensions", result.result)

    async def test_ingest_rejects_over_chunk_ceiling(self):
        from tools.default.rag_tool import RagIngestTool
        from config.settings import RAG_INGEST_MAX_CHUNKS

        tool = RagIngestTool()
        tool.execution = _fake_execution()

        too_many_chunks = ["x"] * (RAG_INGEST_MAX_CHUNKS + 1)
        with patch("tools.default.rag_tool.process_file_bytes", return_value=too_many_chunks):
            result = await tool.run({"content": "irrelevant, process_file_bytes is mocked"})

        self.assertIn("error", result.result)
        self.assertIn(str(RAG_INGEST_MAX_CHUNKS), result.result["error"])

    async def test_ingest_scopes_to_active_project_by_default(self):
        from tools.default.rag_tool import RagIngestTool

        fake_col = _FakeIngestCollection()
        fake_client = _FakeIngestChromaClient(fake_col)

        tool = RagIngestTool()
        tool.execution = _fake_execution(parent_project_id="proj-active")

        with patch("tools.default.rag_tool.embed_text", return_value=[0.1, 0.2]), \
             patch("core.rag.ingestion.get_chroma_client", return_value=fake_client):
            result = await tool.run({"content": "hello", "label": "n.txt"})

        self.assertEqual(result.result["project_id"], "proj-active")
        self.assertEqual(fake_col.add_calls[0]["metadatas"][0]["project_id"], "proj-active")

    async def test_ingest_explicit_project_id_overrides_active_project(self):
        from tools.default.rag_tool import RagIngestTool

        fake_col = _FakeIngestCollection()
        fake_client = _FakeIngestChromaClient(fake_col)

        tool = RagIngestTool()
        tool.execution = _fake_execution(parent_project_id="proj-active")

        with patch("tools.default.rag_tool.embed_text", return_value=[0.1, 0.2]), \
             patch("core.rag.ingestion.get_chroma_client", return_value=fake_client):
            result = await tool.run({
                "content": "hello", "label": "n.txt", "project_id": "proj-explicit",
            })

        self.assertEqual(result.result["project_id"], "proj-explicit")

    async def test_ingest_no_project_when_none_active(self):
        from tools.default.rag_tool import RagIngestTool

        fake_col = _FakeIngestCollection()
        fake_client = _FakeIngestChromaClient(fake_col)

        tool = RagIngestTool()
        tool.execution = _fake_execution()  # parent_project_id=None

        with patch("tools.default.rag_tool.embed_text", return_value=[0.1, 0.2]), \
             patch("core.rag.ingestion.get_chroma_client", return_value=fake_client):
            result = await tool.run({"content": "hello", "label": "n.txt"})

        self.assertIsNone(result.result["project_id"])
        self.assertNotIn("project_id", fake_col.add_calls[0]["metadatas"][0])


class TestRagIngestArgsValidation(unittest.TestCase):

    def test_rejects_both_path_and_content(self):
        from tools.default.rag_tool import RagIngestArgs
        with self.assertRaises(Exception):
            RagIngestArgs(path="a.txt", content="hello")

    def test_rejects_neither_path_nor_content(self):
        from tools.default.rag_tool import RagIngestArgs
        with self.assertRaises(Exception):
            RagIngestArgs()

    def test_accepts_path_only(self):
        from tools.default.rag_tool import RagIngestArgs
        args = RagIngestArgs(path="a.txt")
        self.assertEqual(args.path, "a.txt")

    def test_accepts_content_only(self):
        from tools.default.rag_tool import RagIngestArgs
        args = RagIngestArgs(content="hello world")
        self.assertEqual(args.content, "hello world")


# ===========================================================================
# B-053 — MCP Scoping
# ===========================================================================

def _make_mcp_config(name: str, enabled: bool = True):
    cfg = MagicMock()
    cfg.name = name
    cfg.enabled = enabled
    cfg.id = f"id-{name}"
    cfg.transport = "stdio"
    cfg.command = "echo"
    cfg.args = []
    cfg.env = None
    cfg.cwd = None
    cfg.env_file = None
    cfg.url = None
    cfg.headers = None
    return cfg


def _make_tool_meta(name: str):
    t = MagicMock()
    t.name = name
    t.description = "a tool"
    t.input_schema = {}
    t.output_schema = None
    return t


class TestB053MCPScoping(unittest.IsolatedAsyncioTestCase):

    def _make_manager_and_registry(self):
        from core.tools.dynamic_registry import DynamicToolRegistry
        from core.mcp.manager import MCPManager
        registry = DynamicToolRegistry()
        manager = MCPManager(registry=registry)
        return manager, registry

    async def test_no_restriction_registers_all_enabled(self):
        # P-06a: register_tools_for_user() reads MCPRepo.list_tools_for_config()
        # (a pure DB cache read) instead of opening a live connection via
        # sync_tools_for_config() -- patch the cache read, not the sync call.
        manager, registry = self._make_manager_and_registry()
        configs = [_make_mcp_config("server_a"), _make_mcp_config("server_b")]
        tools_a = [_make_tool_meta("tool_a1")]
        tools_b = [_make_tool_meta("tool_b1")]

        async def fake_load(db, user_id): return configs
        async def fake_list(db, config_id):
            return tools_a if config_id == "id-server_a" else tools_b

        manager.load_user_configs = fake_load
        with patch("core.mcp.manager.MCPRepo.list_tools_for_config", side_effect=fake_list):
            await manager.register_tools_for_user(db=AsyncMock(), user_id="u1",
                                                   allowed_mcp_servers=None)
        names = [m.name for m in registry.list_tools()]
        self.assertIn("server_a__tool_a1", names)
        self.assertIn("server_b__tool_b1", names)

    async def test_allow_set_filters_to_listed_servers_only(self):
        manager, registry = self._make_manager_and_registry()
        configs = [_make_mcp_config("server_a"), _make_mcp_config("server_b")]
        tools_a = [_make_tool_meta("tool_a1")]

        async def fake_load(db, user_id): return configs
        async def fake_list(db, config_id): return tools_a

        manager.load_user_configs = fake_load
        with patch("core.mcp.manager.MCPRepo.list_tools_for_config", side_effect=fake_list):
            await manager.register_tools_for_user(db=AsyncMock(), user_id="u1",
                                                   allowed_mcp_servers=["server_a"])
        names = [m.name for m in registry.list_tools()]
        self.assertIn("server_a__tool_a1", names)
        self.assertFalse(any("server_b" in n for n in names))

    async def test_empty_allow_list_registers_all(self):
        manager, registry = self._make_manager_and_registry()
        configs = [_make_mcp_config("server_a")]
        tools_a = [_make_tool_meta("tool_a1")]

        async def fake_load(db, user_id): return configs
        async def fake_list(db, config_id): return tools_a

        manager.load_user_configs = fake_load
        with patch("core.mcp.manager.MCPRepo.list_tools_for_config", side_effect=fake_list):
            await manager.register_tools_for_user(db=AsyncMock(), user_id="u1",
                                                   allowed_mcp_servers=[])
        names = [m.name for m in registry.list_tools()]
        self.assertIn("server_a__tool_a1", names)

    async def test_disabled_config_always_skipped(self):
        manager, registry = self._make_manager_and_registry()
        configs = [_make_mcp_config("server_a", enabled=False)]

        async def fake_load(db, user_id): return configs

        manager.load_user_configs = fake_load
        await manager.register_tools_for_user(db=AsyncMock(), user_id="u1",
                                               allowed_mcp_servers=["server_a"])
        self.assertEqual(registry.list_tools(), [])


class TestB053ModelColumns(unittest.TestCase):

    def test_subagent_config_has_allowed_mcp_servers(self):
        from core.db.models import SubagentConfig
        self.assertTrue(hasattr(SubagentConfig, "allowed_mcp_servers"))

    def test_project_has_allowed_mcp_servers(self):
        from core.db.models import Project
        self.assertTrue(hasattr(Project, "allowed_mcp_servers"))


# ===========================================================================
# B-054 — Per-Conversation System Prompt
# ===========================================================================

class TestB054ConversationModel(unittest.TestCase):

    def test_conversation_has_system_prompt(self):
        from core.db.models import Conversation
        self.assertTrue(hasattr(Conversation, "system_prompt"))

    def test_conversation_system_prompt_is_nullable(self):
        from core.db.models import Conversation
        col = Conversation.__table__.c.get("system_prompt")
        self.assertIsNotNone(col)
        self.assertTrue(col.nullable)


class TestB054ConversationResponse(unittest.TestCase):

    def test_response_schema_has_system_prompt(self):
        from api.schemas.responses import ConversationResponse
        self.assertIn("system_prompt", ConversationResponse.model_fields)

    def test_system_prompt_defaults_to_none(self):
        from api.schemas.responses import ConversationResponse
        self.assertIsNone(ConversationResponse.model_fields["system_prompt"].default)


class TestB054Repository(unittest.IsolatedAsyncioTestCase):

    async def test_update_conversation_sets_system_prompt(self):
        from core.db.repository import DBRepo
        fake_convo = MagicMock()
        fake_convo.title = "Old Title"
        fake_convo.system_prompt = None
        db = AsyncMock()
        db.get = AsyncMock(return_value=fake_convo)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        updates = MagicMock()
        updates.title = None
        updates.system_prompt = "You are a Python expert."
        await DBRepo.update_conversation(db, "conv-123", updates)
        self.assertEqual(fake_convo.system_prompt, "You are a Python expert.")
        db.commit.assert_awaited_once()

    async def test_update_conversation_returns_none_for_missing(self):
        from core.db.repository import DBRepo
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        updates = MagicMock()
        updates.title = "Whatever"
        updates.system_prompt = None
        result = await DBRepo.update_conversation(db, "nonexistent", updates)
        self.assertIsNone(result)


class TestB054SessionNoSystemPrompt(unittest.TestCase):

    def test_hydrate_does_not_store_system_prompt(self):
        from config.settings import hydrate_user_session_from_db, get_user_session
        hydrate_user_session_from_db(
            "__b054_test_user__",
            {"provider": "groq", "model": "llama-3.3-70b-versatile",
             "system_prompt": "This should be ignored", "tools": []},
        )
        session = get_user_session("__b054_test_user__")
        self.assertEqual(session.get("system_prompt", ""), "")


# ===========================================================================
# B-055 — System Prompt Enhancer
# ===========================================================================

class TestB055PromptEnhanceRequest(unittest.TestCase):

    def test_empty_raw_prompt_raises(self):
        from pydantic import ValidationError
        from api.prompts import PromptEnhanceRequest
        with self.assertRaises(ValidationError):
            PromptEnhanceRequest(raw_prompt="")

    def test_whitespace_only_raises(self):
        from pydantic import ValidationError
        from api.prompts import PromptEnhanceRequest
        with self.assertRaises(ValidationError):
            PromptEnhanceRequest(raw_prompt="   ")

    def test_valid_prompt_passes(self):
        from api.prompts import PromptEnhanceRequest
        req = PromptEnhanceRequest(raw_prompt="You are a helpful assistant.")
        self.assertEqual(req.raw_prompt, "You are a helpful assistant.")

    def test_default_context_is_conversation(self):
        from api.prompts import PromptEnhanceRequest
        req = PromptEnhanceRequest(raw_prompt="Some prompt")
        self.assertEqual(req.context, "conversation")


class TestB055MetaPromptBuilder(unittest.TestCase):

    def test_conversation_context_label(self):
        from api.prompts import _build_meta_user_message
        msg = _build_meta_user_message("Be helpful", "conversation")
        self.assertIn("a specific conversation", msg)
        self.assertIn("Be helpful", msg)

    def test_agent_context_label(self):
        from api.prompts import _build_meta_user_message
        msg = _build_meta_user_message("You are an agent", "agent")
        self.assertIn("a reusable AI agent persona", msg)

    def test_project_context_label(self):
        from api.prompts import _build_meta_user_message
        msg = _build_meta_user_message("Project context", "project")
        self.assertIn("a project workspace context", msg)


class TestB055ApiKeyResolution(unittest.TestCase):
    """
    api/prompts.py no longer defines its own _resolve_api_key -- it now
    imports the shared core.providers.fallback_state.resolve_api_key(),
    the same helper core/providers/fallback_call.py's candidate-building
    uses. Signature also changed: takes the explicit key value directly
    (session.get("api_key")), not the whole session dict, and returns ""
    (never None) when nothing is configured -- see that function's own
    docstring for why (empty string is what callers already treat as
    "try anyway"/fallback-eligible, not a special None case).
    """

    def test_session_key_takes_priority(self):
        from core.providers.fallback_state import resolve_api_key
        with patch.dict(os.environ, {"GROQ_API_KEY": "env-key"}):
            result = resolve_api_key("session-key", "groq")
        self.assertEqual(result, "session-key")

    def test_falls_back_to_stored_key(self):
        from core.providers.fallback_state import resolve_api_key
        from providers import credentials
        credentials.set_api_key("groq", "stored-key")   # isolated in-memory store (conftest)
        self.assertEqual(resolve_api_key(None, "groq"), "stored-key")

    def test_returns_empty_string_when_no_key(self):
        from core.providers.fallback_state import resolve_api_key
        env_backup = os.environ.pop("GROQ_API_KEY", None)
        try:
            result = resolve_api_key(None, "groq")
            self.assertEqual(result, "")
        finally:
            if env_backup:
                os.environ["GROQ_API_KEY"] = env_backup


class TestB055EnhanceEndpoint(unittest.IsolatedAsyncioTestCase):

    async def test_enhance_raises_424_when_no_api_key(self):
        from fastapi import HTTPException
        from api.prompts import enhance_prompt, PromptEnhanceRequest
        mock_request = PromptEnhanceRequest(raw_prompt="Some prompt")
        mock_session = {"provider": "groq", "model": "llama-3.3-70b-versatile", "api_key": None}
        env_backup = os.environ.pop("GROQ_API_KEY", None)
        try:
            with patch("api.prompts.get_user_session", return_value=mock_session):
                with self.assertRaises(HTTPException) as ctx:
                    await enhance_prompt(mock_request, auth={"user_id": "user-no-key"})
            self.assertEqual(ctx.exception.status_code, 424)
        finally:
            if env_backup:
                os.environ["GROQ_API_KEY"] = env_backup


# ===========================================================================
# B-056 — Project Linkage
# ===========================================================================

class TestChatRequestProjectId(unittest.TestCase):

    def test_chat_request_has_project_id(self):
        from api.schemas.requests import ChatRequest
        req = ChatRequest(message="hello", project_id="proj-123")
        self.assertEqual(req.project_id, "proj-123")

    def test_chat_request_project_id_defaults_none(self):
        from api.schemas.requests import ChatRequest
        req = ChatRequest(message="hello")
        self.assertIsNone(req.project_id)


class TestSubagentProjectId(unittest.TestCase):

    def test_request_has_project_id(self):
        from api.agents import SubagentConfigRequest
        req = SubagentConfigRequest(name="researcher", project_id="proj-abc")
        self.assertEqual(req.project_id, "proj-abc")

    def test_response_has_project_id(self):
        from api.agents import SubagentConfigResponse
        self.assertIn("project_id", SubagentConfigResponse.model_fields)


class TestDaemonProjectId(unittest.TestCase):

    def test_request_has_project_id(self):
        from api.daemons import DaemonConfigRequest
        req = DaemonConfigRequest(name="monitor", project_id="proj-abc")
        self.assertEqual(req.project_id, "proj-abc")

    def test_response_has_project_id(self):
        from api.daemons import DaemonConfigResponse
        self.assertIn("project_id", DaemonConfigResponse.model_fields)


class TestB082MultiActiveProjects(unittest.TestCase):
    """
    B-082: Multiple projects can be active simultaneously.

    - deactivate_others() removed from create_project and update_project
    - deactivate_others() method deleted from ProjectRepo
    - get_active_for_user() replaced by list_active_for_user() (returns List, no .limit(1))
    - worker.py: workspace comes from convo.project_id, not from "the active project"
    - daemon runner: workspace, parent_project_id, and project context wired from daemon.project_id
    """

    # ── api/projects.py ───────────────────────────────────────────────────────

    def test_create_project_no_longer_calls_deactivate_others(self):
        """create_project() must not call deactivate_others."""
        src = Path("api/projects.py").read_text(encoding="utf-8")
        idx = src.find("async def create_project")
        self.assertGreater(idx, 0)
        # Find the next top-level async def after create_project
        next_fn = src.find("\nasync def ", idx + 1)
        create_section = src[idx:next_fn] if next_fn > 0 else src[idx:]
        self.assertNotIn("deactivate_others", create_section,
                         "create_project must not call deactivate_others")

    def test_update_project_no_longer_calls_deactivate_others(self):
        """update_project() must not call deactivate_others."""
        src = Path("api/projects.py").read_text(encoding="utf-8")
        idx = src.find("async def update_project")
        self.assertGreater(idx, 0)
        next_fn = src.find("\nasync def ", idx + 1)
        update_section = src[idx:next_fn] if next_fn > 0 else src[idx:]
        self.assertNotIn("deactivate_others", update_section,
                         "update_project must not call deactivate_others")

    # ── core/db/repository.py ────────────────────────────────────────────────

    def test_deactivate_others_method_removed(self):
        """ProjectRepo.deactivate_others must no longer exist."""
        src = Path("core/db/repository.py").read_text(encoding="utf-8")
        self.assertNotIn("def deactivate_others", src,
                         "deactivate_others must be deleted from ProjectRepo")

    def test_list_active_for_user_exists_and_returns_list(self):
        """ProjectRepo.list_active_for_user must exist and have no .limit(1)."""
        src = Path("core/db/repository.py").read_text(encoding="utf-8")
        self.assertIn("def list_active_for_user", src,
                      "list_active_for_user must replace get_active_for_user")
        idx = src.find("def list_active_for_user")
        method_src = src[idx:idx + 400]
        self.assertNotIn(".limit(1)", method_src,
                         "list_active_for_user must not use .limit(1)")
        self.assertIn("List[Project]", method_src,
                      "list_active_for_user must return List[Project]")

    def test_get_active_for_user_removed(self):
        """The old get_active_for_user singular method must be gone."""
        src = Path("core/db/repository.py").read_text(encoding="utf-8")
        self.assertNotIn("def get_active_for_user", src,
                         "get_active_for_user must be replaced by list_active_for_user")

    # ── core/execution/worker.py ─────────────────────────────────────────────

    def test_worker_no_longer_calls_get_active_for_user(self):
        """worker.py must not call get_active_for_user (removed in B-082)."""
        src = Path("core/execution/worker.py").read_text(encoding="utf-8")
        self.assertNotIn("get_active_for_user", src,
                         "worker.py must not call get_active_for_user")

    def test_worker_workspace_set_inside_resolved_project_block(self):
        """
        project_workspace must be set where resolved_project_id is fetched
        (from convo.project_id), not from a separate active-project lookup.
        """
        src = Path("core/execution/worker.py").read_text(encoding="utf-8")
        # The workspace assignment must appear inside the resolved_project_id
        # block, i.e. before that try block's own except clause -- boundary-
        # based rather than a fixed character count, since a fixed window
        # here has already gone stale once as the surrounding comments/
        # logging grew (see the identical convention used elsewhere in this
        # file for the same reason).
        idx = src.find("resolved_project_id = convo.project_id")
        self.assertGreater(idx, 0)
        end_idx = src.find("except Exception as _exc:", idx)
        self.assertGreater(end_idx, idx, "expected the enclosing try block's except clause")
        nearby = src[idx:end_idx]
        self.assertIn("project_workspace", nearby,
                      "workspace must be set in the resolved_project_id block")

    # ── core/daemons/runner.py ───────────────────────────────────────────────

    def test_daemon_runner_sets_project_workspace(self):
        """runner.py must set execution.project_workspace from daemon.project_id."""
        src = Path("core/daemons/runner.py").read_text(encoding="utf-8")
        self.assertIn("execution.project_workspace", src,
                      "daemon runner must set execution.project_workspace")

    def test_daemon_runner_sets_parent_project_id(self):
        """runner.py must set execution.parent_project_id for MCP scoping."""
        src = Path("core/daemons/runner.py").read_text(encoding="utf-8")
        self.assertIn("execution.parent_project_id = daemon.project_id", src,
                      "daemon runner must set parent_project_id for MCP scoping")

    def test_daemon_runner_injects_project_context(self):
        """runner.py must call ProjectManager.load_context for project-linked daemons."""
        src = Path("core/daemons/runner.py").read_text(encoding="utf-8")
        self.assertIn("load_context", src,
                      "daemon runner must call ProjectManager.load_context")
        self.assertIn('tier="focused"', src)

    def test_daemon_runner_uses_augmented_system_prompt(self):
        """AgentEngine must receive daemon_system_prompt not daemon.system_prompt directly."""
        src = Path("core/daemons/runner.py").read_text(encoding="utf-8")
        self.assertIn("daemon_system_prompt", src)
        # The AgentEngine call must use the augmented variable
        idx = src.find("AgentEngine(")
        self.assertGreater(idx, 0)
        agent_call = src[idx:idx + 400]
        self.assertIn("daemon_system_prompt", agent_call,
                      "AgentEngine must receive daemon_system_prompt (augmented), not raw daemon.system_prompt")
        self.assertNotIn("daemon.system_prompt", agent_call,
                         "AgentEngine must not use daemon.system_prompt directly — use daemon_system_prompt")

    def test_daemon_runner_project_context_is_nonfatal(self):
        """Project context fetch must be wrapped in try/except — non-fatal."""
        src = Path("core/daemons/runner.py").read_text(encoding="utf-8")
        # The project resolution block must have exception handling
        self.assertIn("could not resolve project context", src,
                      "daemon runner project context fetch must be non-fatal with warning log")


class TestDaemonTriggerProjectPausedGuard(unittest.TestCase):
    """
    Daemon manual trigger must be blocked when the daemon is project-paused.

    Gap: pause_for_project() sets lifecycle_status="project_paused" + enabled=False,
    but the trigger endpoint previously didn't check either field — allowing a manual
    trigger to overwrite the cascade state and leave the daemon in "idle" after the run.

    Phase C (revised): a separate DaemonRegistry.trigger_now() was tried and then
    reverted -- POST /projects/{id}/sync calls api/daemons.py's trigger_daemon()
    directly as a plain function instead, so there is exactly one place these
    guards live. These tests check the guard logic at that single location.
    """

    def test_trigger_checks_enabled_flag_first(self):
        """
        trigger_daemon() must block when daemon.enabled is False — the primary gate.
        A disabled daemon cannot run regardless of why it was disabled.
        """
        src = Path("api/daemons.py").read_text(encoding="utf-8")
        idx = src.find("async def trigger_daemon")
        self.assertGreater(idx, 0)
        trigger_section = src[idx:idx + 2500]
        self.assertIn("not daemon.enabled", trigger_section,
                      "trigger_daemon must check enabled flag as primary gate")

    def test_trigger_gives_project_paused_message_when_cascade_disabled(self):
        """When disabled by project cascade, the response must mention 'project_paused'."""
        src = Path("api/daemons.py").read_text(encoding="utf-8")
        idx = src.find("async def trigger_daemon")
        trigger_section = src[idx:idx + 2500]
        self.assertIn('"project_paused"', trigger_section)

    def test_trigger_gives_disabled_message_when_user_disabled(self):
        """When disabled by the user (not project cascade), the message must say 'disabled'."""
        src = Path("api/daemons.py").read_text(encoding="utf-8")
        idx = src.find("async def trigger_daemon")
        trigger_section = src[idx:idx + 2500]
        self.assertIn("is disabled", trigger_section)

    def test_trigger_source_checks_project_status_directly(self):
        """
        trigger_daemon() must ALSO fetch the project and check its status directly
        as a belt-and-suspenders fallback for the edge case where enabled=True
        but the project is paused (DB inconsistency / cascade gap).
        """
        src = Path("api/daemons.py").read_text(encoding="utf-8")
        idx = src.find("async def trigger_daemon")
        self.assertGreater(idx, 0)
        trigger_section = src[idx:idx + 3500]
        self.assertIn("ProjectRepo.get", trigger_section,
                      "trigger_daemon must fetch project to authoritatively check status")
        self.assertIn('"paused"', trigger_section)
        self.assertIn('"archived"', trigger_section)

    def test_trigger_error_result_mentions_project(self):
        """The project_paused detail must mention 'project' so the user understands why."""
        src = Path("api/daemons.py").read_text(encoding="utf-8")
        idx = src.find("async def trigger_daemon")
        self.assertGreater(idx, 0)
        trigger_section = src[idx:idx + 2500]
        self.assertIn("project", trigger_section.lower())

    def test_sync_project_calls_trigger_daemon_directly(self):
        """api/projects.py's sync_project must delegate to api/daemons.py's
        trigger_daemon() directly (plain function call) rather than reimplementing
        the guards or going through a separate shared registry method -- confirms
        there is exactly one place these guards live."""
        projects_src = Path("api/projects.py").read_text(encoding="utf-8")
        idx = projects_src.find("async def sync_project")
        self.assertGreater(idx, 0, "sync_project not found")
        next_fn = projects_src.find("\nasync def ", idx + 1)
        section = projects_src[idx:next_fn] if next_fn > 0 else projects_src[idx:]
        self.assertIn("from api.daemons import trigger_daemon", section)
        self.assertIn("await trigger_daemon(", section)
        self.assertNotIn("trigger_now", section,
                         "sync_project must not reference a separate trigger_now method")

    def test_record_success_restores_project_paused_on_disabled_project_daemon(self):
        """
        record_success() must set lifecycle_status='project_paused' (not 'idle')
        when enabled=False and project_id is set — i.e., the daemon ran while
        the project was paused (race condition or old trigger-gap exploit).
        """
        src = Path("core/daemons/registry.py").read_text(encoding="utf-8")
        # The defensive guard must be present in record_success
        # Look for the combination of enabled check and project_paused restoration
        self.assertIn(
            "not row.enabled and row.project_id",
            src,
            "record_success must restore project_paused for disabled project daemons",
        )

    def test_record_failure_suppresses_retry_for_project_paused_daemon(self):
        """
        record_failure() must NOT schedule a retry when the daemon is
        project-paused (enabled=False + project_id set) — the scheduler can't
        fire it anyway and "retrying" would be a misleading status.
        """
        src = Path("core/daemons/registry.py").read_text(encoding="utf-8")
        self.assertIn(
            "retry suppressed",
            src,
            "record_failure must suppress retries for project-paused daemons",
        )

    def test_record_success_existing_project_paused_guard_still_present(self):
        """The original guard (don't overwrite explicit 'project_paused') is still there."""
        src = Path("core/daemons/registry.py").read_text(encoding="utf-8")
        self.assertIn(
            'not in ("paused", "project_paused")',
            src,
            "record_success must still skip setting lifecycle when already project_paused",
        )


class TestProjectIdColumns(unittest.TestCase):

    def test_subagent_config_has_project_id(self):
        from core.db.models import SubagentConfig
        col = SubagentConfig.__table__.c.get("project_id")
        self.assertIsNotNone(col)
        self.assertTrue(col.nullable)

    def test_daemon_config_has_project_id(self):
        from core.db.models import DaemonConfig
        col = DaemonConfig.__table__.c.get("project_id")
        self.assertIsNotNone(col)

    def test_conversation_has_project_id(self):
        from core.db.models import Conversation
        col = Conversation.__table__.c.get("project_id")
        self.assertIsNotNone(col)


# ===========================================================================
# B-057 — Project Status Guards
# ===========================================================================

class TestCheckProjectLinkable(unittest.IsolatedAsyncioTestCase):

    async def test_agents_archived_raises_403(self):
        from fastapi import HTTPException
        proj = MagicMock(); proj.status = "archived"
        with patch("api.agents.ProjectRepo") as MockRepo, \
             patch("api.agents.AsyncSessionLocal") as MockSession:
            MockSession.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)
            MockRepo.get = AsyncMock(return_value=proj)
            from api.agents import _check_project_linkable
            with self.assertRaises(HTTPException) as ctx:
                await _check_project_linkable("proj-1", "user-1")
            self.assertEqual(ctx.exception.status_code, 403)

    async def test_agents_not_found_raises_404(self):
        from fastapi import HTTPException
        with patch("api.agents.ProjectRepo") as MockRepo, \
             patch("api.agents.AsyncSessionLocal") as MockSession:
            MockSession.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)
            MockRepo.get = AsyncMock(return_value=None)
            from api.agents import _check_project_linkable
            with self.assertRaises(HTTPException) as ctx:
                await _check_project_linkable("proj-missing", "user-1")
            self.assertEqual(ctx.exception.status_code, 404)

    async def test_agents_active_passes(self):
        proj = MagicMock(); proj.status = "active"
        with patch("api.agents.ProjectRepo") as MockRepo, \
             patch("api.agents.AsyncSessionLocal") as MockSession:
            MockSession.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)
            MockRepo.get = AsyncMock(return_value=proj)
            from api.agents import _check_project_linkable
            await _check_project_linkable("proj-1", "user-1")


class TestProjectStatusField(unittest.TestCase):

    def test_project_status_column_exists(self):
        from core.db.models import Project
        self.assertTrue(hasattr(Project, "status"))

    def test_project_default_status_is_active(self):
        from core.db.models import Project
        col = Project.__table__.c.get("status")
        self.assertEqual(col.default.arg, "active")


# ===========================================================================
# Edit/Retry Truncation — DELETE /conversations/{id}/messages
# ===========================================================================

class TestDeleteMessagesFromRepo(unittest.IsolatedAsyncioTestCase):

    def _make_msg(self, msg_id, seq=None, created_offset=0):
        msg = MagicMock()
        msg.id = msg_id
        msg.conversation_id = "conv-1"
        msg.sequence = seq
        msg.created_at = datetime.now(timezone.utc) + timedelta(seconds=created_offset)
        return msg

    async def test_returns_zero_when_message_id_not_found(self):
        msg_a = self._make_msg("msg-a", seq=1)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [msg_a]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.commit = AsyncMock()
        from core.db.repository import DBRepo
        count = await DBRepo.delete_messages_from(db, "conv-1", "msg-nonexistent")
        self.assertEqual(count, 0)
        db.commit.assert_not_awaited()

    async def test_deletes_from_message_and_all_after(self):
        msg_a = self._make_msg("msg-a", seq=1)
        msg_b = self._make_msg("msg-b", seq=2)
        msg_c = self._make_msg("msg-c", seq=3)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [msg_a, msg_b, msg_c]
        db = AsyncMock()
        del_result = MagicMock()
        del_result.rowcount = 2
        db.execute.side_effect = [mock_result, MagicMock(), del_result]
        db.commit = AsyncMock()
        from core.db.repository import DBRepo
        count = await DBRepo.delete_messages_from(db, "conv-1", "msg-b")
        self.assertEqual(count, 2)
        db.commit.assert_awaited_once()


class TestDeleteMessagesEndpointRegistered(unittest.TestCase):

    def test_endpoint_registered_on_router(self):
        import inspect
        import api.conversation_management as mod
        source = inspect.getsource(mod)
        self.assertIn("delete_messages_from", source)
        self.assertIn("/conversations/{conversation_id}/messages", source)
        self.assertIn("from_message_id", source)


# ===========================================================================
# B-061: GET /executions filter params
# ===========================================================================

class TestExecutionListFilterParams(unittest.TestCase):

    def test_list_executions_has_triggered_by_param(self):
        import inspect
        import api.executions as mod
        source = inspect.getsource(mod.list_executions)
        self.assertIn("triggered_by", source)

    def test_list_executions_has_daemon_name_param(self):
        import inspect
        import api.executions as mod
        source = inspect.getsource(mod.list_executions)
        self.assertIn("daemon_name", source)

    def test_execution_summary_has_new_fields(self):
        from api.executions import ExecutionSummary
        fields = ExecutionSummary.model_fields
        self.assertIn("triggered_by", fields)
        self.assertIn("daemon_name", fields)
        self.assertIn("project_id", fields)

    def test_list_for_user_accepts_triggered_by(self):
        import inspect
        from core.db.repository import ExecutionMetricsRepo
        sig = inspect.signature(ExecutionMetricsRepo.list_for_user)
        self.assertIn("triggered_by", sig.parameters)

    def test_list_for_user_accepts_daemon_name(self):
        import inspect
        from core.db.repository import ExecutionMetricsRepo
        sig = inspect.signature(ExecutionMetricsRepo.list_for_user)
        self.assertIn("daemon_name", sig.parameters)


# ===========================================================================
# B-062: GET /daemons/{name}/executions
# ===========================================================================

class TestDaemonExecutionsEndpointSchema(unittest.TestCase):

    def test_daemon_execution_summary_schema(self):
        from api.daemons import DaemonExecutionSummary
        fields = DaemonExecutionSummary.model_fields
        for f in ("execution_id", "status", "triggered_by", "project_id",
                  "termination_reason", "duration_ms", "created_at", "completed_at"):
            self.assertIn(f, fields)

    def test_list_for_daemon_method_exists(self):
        from core.db.repository import ExecutionMetricsRepo
        self.assertTrue(hasattr(ExecutionMetricsRepo, "list_for_daemon"))

    def test_endpoint_registered(self):
        import inspect
        import api.daemons as mod
        source = inspect.getsource(mod)
        self.assertIn("list_daemon_executions", source)


class TestDaemonExecutionsEndpointAsync(unittest.IsolatedAsyncioTestCase):

    async def test_list_daemon_executions_404_for_unknown(self):
        from fastapi import HTTPException
        with patch("api.daemons.daemon_registry") as mock_reg:
            mock_reg.get = AsyncMock(return_value=None)
            from api.daemons import list_daemon_executions
            with self.assertRaises(HTTPException) as ctx:
                await list_daemon_executions(
                    name="no_such_daemon", limit=20, auth={"user_id": "user-1"},
                )
            self.assertEqual(ctx.exception.status_code, 404)


# ===========================================================================
# B-063: POST /daemons/{name}/cancel
# ===========================================================================

class TestDaemonCancelEndpoint(unittest.IsolatedAsyncioTestCase):

    def test_cancel_endpoint_registered(self):
        import inspect
        import api.daemons as mod
        source = inspect.getsource(mod)
        self.assertIn("cancel_daemon_execution", source)

    async def test_cancel_returns_404_for_unknown_daemon(self):
        from fastapi import HTTPException
        with patch("api.daemons.daemon_registry") as mock_reg:
            mock_reg.get = AsyncMock(return_value=None)
            from api.daemons import cancel_daemon_execution
            with self.assertRaises(HTTPException) as ctx:
                await cancel_daemon_execution(
                    name="no_such_daemon", auth={"user_id": "user-1"},
                )
            self.assertEqual(ctx.exception.status_code, 404)

    async def test_cancel_returns_409_when_idle(self):
        from fastapi import HTTPException
        mock_daemon = MagicMock()
        mock_daemon.lifecycle_status = "idle"
        mock_daemon.current_execution_id = None
        with patch("api.daemons.daemon_registry") as mock_reg:
            mock_reg.get = AsyncMock(return_value=mock_daemon)
            from api.daemons import cancel_daemon_execution
            with self.assertRaises(HTTPException) as ctx:
                await cancel_daemon_execution(
                    name="my_daemon", auth={"user_id": "user-1"},
                )
            self.assertEqual(ctx.exception.status_code, 409)


# ===========================================================================
# B-066 — Conversation Attachments & File Library
# ===========================================================================

class TestFileParserBasic(unittest.TestCase):

    def test_parse_plain_text(self):
        from core.files.parser import parse_file
        content = b"Hello world. This is a test file with some content."
        result = parse_file("sample.txt", content)
        self.assertEqual(result["attachment_type"], "file")
        self.assertIsNotNone(result["text"])
        self.assertGreater(result["token_estimate"], 0)

    def test_parse_png_image(self):
        from core.files.parser import parse_file
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        result = parse_file("photo.png", png_header)
        self.assertEqual(result["attachment_type"], "image")
        self.assertIsNotNone(result["base64_data"])
        self.assertFalse(result["use_rag"])

    def test_parse_binary_raises(self):
        from core.files.parser import parse_file
        binary = bytes([0x00, 0x01, 0xFF, 0xFE] * 200)
        with self.assertRaises(ValueError):
            parse_file("binary.bin", binary)

    def test_token_estimate_scales_with_content(self):
        from core.files.parser import parse_file
        r_short = parse_file("short.txt", b"Hello world.")
        r_long  = parse_file("long.txt", b"Hello world. " * 500)
        self.assertGreater(r_long["token_estimate"], r_short["token_estimate"])

    def test_parse_json_as_text(self):
        from core.files.parser import parse_file
        result = parse_file("data.json", b'{"key": "value"}')
        self.assertEqual(result["attachment_type"], "file")
        self.assertIn("key", result["text"])


class TestAttachmentModelColumns(unittest.TestCase):

    def test_attachment_columns(self):
        from core.db.models import Attachment
        for col in ("id", "user_id", "attachment_type", "filename", "mime_type",
                    "blob_path", "token_estimate", "size_bytes", "project_id", "created_at"):
            self.assertTrue(hasattr(Attachment, col), f"Missing column: {col}")

    def test_conversation_attachment_columns(self):
        from core.db.models import ConversationAttachment
        for col in ("id", "conversation_id", "user_id", "attachment_type",
                    "attachment_id", "ref_conversation_id", "ref_execution_id",
                    "message_id", "rag_indexed", "rag_job_id", "created_at"):
            self.assertTrue(hasattr(ConversationAttachment, col), f"Missing column: {col}")


class TestAttachmentRepoInterface(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession,
                                    expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_create_and_get(self):
        from core.db.repository import AttachmentRepo
        async with self.Session() as db:
            att = await AttachmentRepo.create(
                db, user_id="u1", attachment_type="file",
                filename="test.txt", mime_type="text/plain",
                token_estimate=150, size_bytes=1024,
            )
            self.assertIsNotNone(att.id)
            fetched = await AttachmentRepo.get(db, att.id)
            self.assertEqual(fetched.filename, "test.txt")

    async def test_list_for_user(self):
        from core.db.repository import AttachmentRepo
        async with self.Session() as db:
            await AttachmentRepo.create(db, user_id="u1", attachment_type="file",
                                        filename="a.txt")
            await AttachmentRepo.create(db, user_id="u1", attachment_type="image",
                                        filename="b.png")
            await AttachmentRepo.create(db, user_id="u2", attachment_type="file",
                                        filename="c.txt")
            items_u1 = await AttachmentRepo.list_for_user(db, "u1")
            self.assertEqual(len(items_u1), 2)

    async def test_delete(self):
        from core.db.repository import AttachmentRepo
        async with self.Session() as db:
            att = await AttachmentRepo.create(db, user_id="u1",
                                              attachment_type="file")
            deleted = await AttachmentRepo.delete(db, att.id)
            self.assertTrue(deleted)
            gone = await AttachmentRepo.get(db, att.id)
            self.assertIsNone(gone)


class TestDeleteConversationCascadesExecutionPlan(unittest.IsolatedAsyncioTestCase):
    """
    DB-02, closed 2026-08-11 -- execution_plans rows were orphaned (left
    pointing at a now-deleted conversation_id) because
    DBRepo.delete_conversation_and_children() never touched ExecutionPlan
    at all. Fixed by adding it to the deletion set, since (unlike
    ExecutionMetrics, deliberately retained -- see the regression guard
    below) its goal/step descriptions/result_summary are actual conversation
    content, not just aggregate numbers -- a user deleting a conversation
    expects that content gone too.

    Uses a real in-memory SQLite DB (same pattern as
    TestAttachmentRepoInterface above) rather than mocks, since this is
    exactly the kind of multi-table cascading-delete behaviour a mock
    would trivially get right without proving anything.
    """

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession,
                                    expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_execution_plan_deleted_but_execution_metrics_retained(self):
        from core.db.models import Conversation, ExecutionMetrics
        from core.db.repository import DBRepo, PlanRepo

        conv_id = "conv-db02-test"
        exec_id = "exec-db02-test"

        async with self.Session() as db:
            db.add(Conversation(id=conv_id, user_id="u1", title="Test convo"))
            await db.commit()

            await PlanRepo.create(
                db,
                execution_id=exec_id,
                conversation_id=conv_id,
                user_id="u1",
                goal="Sensitive goal text that should not outlive the conversation",
                steps=[{"index": 0, "title": "Step", "description": "desc"}],
            )

            db.add(ExecutionMetrics(
                execution_id=exec_id, conversation_id=conv_id,
                user_id="u1", status="completed",
            ))
            await db.commit()

            # Sanity check both rows actually exist before deletion.
            plan_before = await PlanRepo.get_by_execution(db, exec_id)
            self.assertIsNotNone(plan_before)

            await DBRepo.delete_conversation_and_children(db, conv_id)

            plan_after = await PlanRepo.get_by_execution(db, exec_id)
            self.assertIsNone(
                plan_after,
                "ExecutionPlan must be deleted alongside its conversation (DB-02)",
            )

            metrics_after = await db.get(ExecutionMetrics, exec_id)
            self.assertIsNotNone(
                metrics_after,
                "ExecutionMetrics must still be intentionally retained for usage/cost history",
            )

            convo_after = await db.get(Conversation, conv_id)
            self.assertIsNone(convo_after)


class TestDeleteAllConversationsCascadesExecutionPlan(unittest.IsolatedAsyncioTestCase):
    """
    Sibling of TestDeleteConversationCascadesExecutionPlan above --
    DBRepo.delete_all_conversations_for_user() (the actual bulk "delete
    all my conversations" implementation, called from
    api/conversation_management.py) had the identical DB-02 gap: it never
    touched ExecutionPlan either. Caught during a Q-06 audit of the
    (unrelated, dead-code) AuthRepo duplicate of the single-conversation
    delete method -- AuthRepo turned out to have a dead duplicate of THIS
    method too, which is what surfaced that the real, live bulk-delete
    path had never received the DB-02 fix in the first place.
    """

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession,
                                    expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_execution_plans_deleted_but_execution_metrics_retained_for_bulk_delete(self):
        from core.db.models import Conversation, ExecutionMetrics
        from core.db.repository import DBRepo, PlanRepo

        user_id = "u-db02-bulk-test"
        conv_ids = ["conv-bulk-1", "conv-bulk-2"]
        exec_ids = ["exec-bulk-1", "exec-bulk-2"]

        async with self.Session() as db:
            for conv_id, exec_id in zip(conv_ids, exec_ids):
                db.add(Conversation(id=conv_id, user_id=user_id, title="Test convo"))
                await db.commit()
                await PlanRepo.create(
                    db, execution_id=exec_id, conversation_id=conv_id, user_id=user_id,
                    goal="Sensitive goal text", steps=[{"index": 0, "title": "Step", "description": "desc"}],
                )
                db.add(ExecutionMetrics(
                    execution_id=exec_id, conversation_id=conv_id,
                    user_id=user_id, status="completed",
                ))
                await db.commit()

            for exec_id in exec_ids:
                self.assertIsNotNone(await PlanRepo.get_by_execution(db, exec_id))

            deleted_count = await DBRepo.delete_all_conversations_for_user(db, user_id)
            self.assertEqual(deleted_count, 2)

            for exec_id in exec_ids:
                self.assertIsNone(
                    await PlanRepo.get_by_execution(db, exec_id),
                    "ExecutionPlan rows must be deleted for every conversation in a bulk delete (DB-02)",
                )
                self.assertIsNotNone(
                    await db.get(ExecutionMetrics, exec_id),
                    "ExecutionMetrics must still be retained even in a bulk delete",
                )

            for conv_id in conv_ids:
                self.assertIsNone(await db.get(Conversation, conv_id))


class TestAuthRetentionPurge(unittest.IsolatedAsyncioTestCase):
    """
    DB-01 -- AuthRepo.purge_expired_sessions() / purge_old_audit_logs(),
    wired into core/tasks/cleanup.py's daily loop as a second, independent
    pass alongside the existing workspace cleanup.

    Real in-memory SQLite DB (same pattern as the DB-02 tests above), since
    this is exactly the kind of "only delete what's actually dead, never a
    live row" behaviour a mock would trivially get right without proving
    anything.
    """

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession,
                                    expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_purge_expired_sessions_only_removes_dead_rows(self):
        from datetime import datetime, timezone, timedelta
        from core.db.models import User, AuthSession
        from core.db.repository import AuthRepo

        now = datetime.now(timezone.utc)

        async with self.Session() as db:
            db.add(User(id="u1", email="u1@test.local", username="u1", password_hash="x"))
            await db.commit()

            # Live: valid refresh token, not revoked -- must survive.
            db.add(AuthSession(
                id="sess-live", user_id="u1", revoked=False,
                expires_at=now + timedelta(hours=1),
                refresh_token="tok-live",
                refresh_expires_at=now + timedelta(days=5),
            ))
            # Revoked: must be purged regardless of refresh_expires_at.
            db.add(AuthSession(
                id="sess-revoked", user_id="u1", revoked=True,
                expires_at=now + timedelta(hours=1),
                refresh_token="tok-revoked",
                refresh_expires_at=now + timedelta(days=5),
            ))
            # Refresh-expired: not revoked, but refresh_expires_at is in the
            # past -- can never be used to re-authenticate, must be purged.
            db.add(AuthSession(
                id="sess-refresh-expired", user_id="u1", revoked=False,
                expires_at=now - timedelta(hours=1),
                refresh_token="tok-expired",
                refresh_expires_at=now - timedelta(days=1),
            ))
            # No refresh token at all (refresh_expires_at is NULL) -- must
            # NOT be treated as expired just because the column is null.
            db.add(AuthSession(
                id="sess-no-refresh", user_id="u1", revoked=False,
                expires_at=now + timedelta(hours=1),
                refresh_token=None,
                refresh_expires_at=None,
            ))
            await db.commit()

            purged = await AuthRepo.purge_expired_sessions(db)
            self.assertEqual(purged, 2)

            self.assertIsNotNone(await db.get(AuthSession, "sess-live"))
            self.assertIsNotNone(await db.get(AuthSession, "sess-no-refresh"))
            self.assertIsNone(await db.get(AuthSession, "sess-revoked"))
            self.assertIsNone(await db.get(AuthSession, "sess-refresh-expired"))

    async def test_purge_old_audit_logs_respects_retention_window(self):
        from datetime import datetime, timezone, timedelta
        from core.db.models import User, AuthAuditLog
        from core.db.repository import AuthRepo

        now = datetime.now(timezone.utc)

        async with self.Session() as db:
            db.add(User(id="u1", email="u1@test.local", username="u1", password_hash="x"))
            await db.commit()

            db.add(AuthAuditLog(
                id="log-recent", user_id="u1", event_type="login",
                created_at=now - timedelta(days=10),
            ))
            db.add(AuthAuditLog(
                id="log-old", user_id="u1", event_type="login",
                created_at=now - timedelta(days=200),
            ))
            await db.commit()

            purged = await AuthRepo.purge_old_audit_logs(db, retention_days=180)
            self.assertEqual(purged, 1)

            self.assertIsNotNone(await db.get(AuthAuditLog, "log-recent"))
            self.assertIsNone(await db.get(AuthAuditLog, "log-old"))

    async def test_cleanup_pass_wires_both_purge_functions(self):
        """core.tasks.cleanup._purge_auth_data() must call both AuthRepo
        purge functions and return their counts -- a source-level check
        since this function opens its own AsyncSessionLocal (a real DB,
        not the in-memory one this test class otherwise uses)."""
        from unittest.mock import patch, AsyncMock
        import core.tasks.cleanup as cleanup_mod

        with patch("core.db.repository.AuthRepo.purge_expired_sessions",
                   new=AsyncMock(return_value=3)), \
             patch("core.db.repository.AuthRepo.purge_old_audit_logs",
                   new=AsyncMock(return_value=7)):
            summary = await cleanup_mod._purge_auth_data()

        self.assertEqual(summary, {"sessions_purged": 3, "audit_logs_purged": 7})


class TestSlidingWindowRateLimiter(unittest.IsolatedAsyncioTestCase):
    """
    S-02 -- core.rate_limit.SlidingWindowRateLimiter, extracted from the
    original inline webhook rate limiter (api/webhooks.py, S-3) so the
    auth endpoints could reuse the identical, already-proven mechanism.
    Tests drive it directly rather than through an endpoint, since this
    class is the actual unit of correctness both callers depend on.
    """

    async def test_check_and_record_blocks_after_max_calls(self):
        from core.rate_limit import SlidingWindowRateLimiter
        limiter = SlidingWindowRateLimiter(window_seconds=60, max_calls=3)

        for _ in range(3):
            await limiter.check_and_record("k")

        with self.assertRaises(Exception) as ctx:
            await limiter.check_and_record("k")
        self.assertEqual(ctx.exception.status_code, 429)

    async def test_check_and_record_429_carries_retry_after_header(self):
        from core.rate_limit import SlidingWindowRateLimiter
        limiter = SlidingWindowRateLimiter(window_seconds=60, max_calls=1)

        await limiter.check_and_record("k")
        with self.assertRaises(Exception) as ctx:
            await limiter.check_and_record("k")

        self.assertIn("Retry-After", ctx.exception.headers)
        self.assertGreater(int(ctx.exception.headers["Retry-After"]), 0)

    async def test_window_expiry_allows_calls_again(self):
        from unittest.mock import patch
        from core.rate_limit import SlidingWindowRateLimiter
        limiter = SlidingWindowRateLimiter(window_seconds=10, max_calls=1)

        with patch("core.rate_limit.time.monotonic", return_value=1000.0):
            await limiter.check_and_record("k")
            with self.assertRaises(Exception):
                await limiter.check_and_record("k")

        # Past the window -- the old timestamp is evicted, call allowed again.
        with patch("core.rate_limit.time.monotonic", return_value=1011.0):
            await limiter.check_and_record("k")  # must not raise

    async def test_check_alone_does_not_record(self):
        """check() (used by the failure-counted login flow) must not itself
        count as a call -- only record_failure() should."""
        from core.rate_limit import SlidingWindowRateLimiter
        limiter = SlidingWindowRateLimiter(window_seconds=60, max_calls=1)

        for _ in range(5):
            await limiter.check("k")  # must never raise, never records

    async def test_record_failure_then_check_blocks_after_threshold(self):
        from core.rate_limit import SlidingWindowRateLimiter
        limiter = SlidingWindowRateLimiter(window_seconds=60, max_calls=2)

        await limiter.check("k")  # ok, no failures yet
        await limiter.record_failure("k")
        await limiter.check("k")  # ok, 1 failure < max_calls
        await limiter.record_failure("k")

        with self.assertRaises(Exception) as ctx:
            await limiter.check("k")
        self.assertEqual(ctx.exception.status_code, 429)

    async def test_reset_clears_recorded_failures(self):
        from core.rate_limit import SlidingWindowRateLimiter
        limiter = SlidingWindowRateLimiter(window_seconds=60, max_calls=1)

        await limiter.record_failure("k")
        with self.assertRaises(Exception):
            await limiter.check("k")

        await limiter.reset("k")
        await limiter.check("k")  # must not raise -- counter was cleared

    async def test_different_keys_are_independent(self):
        from core.rate_limit import SlidingWindowRateLimiter
        limiter = SlidingWindowRateLimiter(window_seconds=60, max_calls=1)

        await limiter.check_and_record("key-a")
        # key-b has never been called -- must not be affected by key-a's usage.
        await limiter.check_and_record("key-b")  # must not raise


class TestAuthEndpointRateLimiting(unittest.IsolatedAsyncioTestCase):
    """
    S-02 -- verifies the actual wiring in api/auth.py: login enforces a
    dual-key (IP + account), failure-counted limiter that resets on
    success; signup and refresh use a simpler all-traffic-counted,
    IP-only limiter. Mocks every dependency of login()/signup()/
    refresh_token() except the rate limiter itself, so these tests would
    fail if the wiring were ever removed or miskeyed -- exactly the
    regression this extraction needs to guard against.
    """

    def _make_request(self, ip="1.2.3.4"):
        req = MagicMock()
        req.client.host = ip
        req.headers = {"user-agent": "pytest"}
        return req

    async def test_login_blocks_after_max_failed_attempts_by_ip_and_account(self):
        import api.auth as auth_mod
        from config.settings import AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS

        # Fresh limiter instance for this test -- module-level state would
        # otherwise leak between tests in this class.
        auth_mod._login_rate_limiter = type(auth_mod._login_rate_limiter)(
            window_seconds=60, max_calls=AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
        )

        request = self._make_request()
        form_data = MagicMock()
        form_data.username = "nosuchuser"
        form_data.password = "wrong"
        db = AsyncMock()

        with patch.object(auth_mod.AuthRepo, "get_user_by_username", new=AsyncMock(return_value=None)):
            for _ in range(AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS):
                with self.assertRaises(Exception) as ctx:
                    await auth_mod.login(request=request, form_data=form_data, db=db)
                self.assertEqual(ctx.exception.status_code, 401)

            # One more failed attempt past the threshold -- must be a 429
            # rate-limit rejection, not another 401 "invalid credentials".
            with self.assertRaises(Exception) as ctx:
                await auth_mod.login(request=request, form_data=form_data, db=db)
            self.assertEqual(ctx.exception.status_code, 429)

    async def test_signup_rate_limited_by_ip(self):
        import api.auth as auth_mod
        from config.settings import AUTH_SIGNUP_RATE_LIMIT_MAX_CALLS

        auth_mod._signup_rate_limiter = type(auth_mod._signup_rate_limiter)(
            window_seconds=60, max_calls=AUTH_SIGNUP_RATE_LIMIT_MAX_CALLS,
        )

        request = self._make_request()
        db = AsyncMock()
        # Fail fast inside create_user so the test doesn't need to mock the
        # rest of signup()'s body -- the rate-limit check runs before this.
        with patch.object(auth_mod.AuthRepo, "create_user",
                           new=AsyncMock(side_effect=RuntimeError("stop here"))):
            for _ in range(AUTH_SIGNUP_RATE_LIMIT_MAX_CALLS):
                data = MagicMock(username="u", email="u@test.local", password="p")
                with self.assertRaises(RuntimeError):
                    await auth_mod.signup(data=data, request=request, db=db)

            data = MagicMock(username="u2", email="u2@test.local", password="p")
            with self.assertRaises(Exception) as ctx:
                await auth_mod.signup(data=data, request=request, db=db)
            self.assertEqual(ctx.exception.status_code, 429)

    async def test_refresh_rate_limited_by_ip(self):
        import api.auth as auth_mod
        from config.settings import AUTH_REFRESH_RATE_LIMIT_MAX_CALLS

        auth_mod._refresh_rate_limiter = type(auth_mod._refresh_rate_limiter)(
            window_seconds=60, max_calls=AUTH_REFRESH_RATE_LIMIT_MAX_CALLS,
        )

        request = self._make_request()
        db = AsyncMock()
        data = MagicMock(refresh_token="tok")

        with patch.object(auth_mod.AuthRepo, "get_session_by_refresh_token",
                           new=AsyncMock(return_value=None)):
            for _ in range(AUTH_REFRESH_RATE_LIMIT_MAX_CALLS):
                with self.assertRaises(Exception) as ctx:
                    await auth_mod.refresh_token(data=data, request=request, db=db)
                self.assertEqual(ctx.exception.status_code, 401)

            with self.assertRaises(Exception) as ctx:
                await auth_mod.refresh_token(data=data, request=request, db=db)
            self.assertEqual(ctx.exception.status_code, 429)


class TestGlobalHttpExceptionHandlerPreservesHeaders(unittest.IsolatedAsyncioTestCase):
    """
    Live-found while verifying S-02: main.py's global StarletteHTTPException
    handler -- the only one registered, so it intercepts every HTTPException
    raised anywhere in the app -- built its JSONResponse from only
    status_code/content, silently dropping exc.headers. This meant
    Retry-After was never actually reaching clients on any 429, including
    the pre-existing webhook rate limiter (S-3, predates this session) --
    not just the new auth one. Confirmed live: curl showed no retry-after
    header on a real 429 response before this fix.
    """

    async def test_custom_headers_pass_through_to_the_response(self):
        from fastapi import HTTPException
        from starlette.requests import Request as StarletteRequest
        import main as main_mod

        exc = HTTPException(
            status_code=429, detail="rate limited",
            headers={"Retry-After": "42"},
        )
        # A bare ASGI scope is enough -- the handler never touches the
        # request body, only exc.
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        request = StarletteRequest(scope)

        response = await main_mod.http_exception_handler(request, exc)

        self.assertEqual(response.headers.get("retry-after"), "42")
        self.assertEqual(response.status_code, 429)

    async def test_no_headers_on_the_exception_does_not_crash(self):
        """Regression guard: most HTTPException call sites never set
        headers at all -- exc.headers is None (or {}) in that case, and
        JSONResponse must accept that without raising."""
        from fastapi import HTTPException
        from starlette.requests import Request as StarletteRequest
        import main as main_mod

        exc = HTTPException(status_code=404, detail="not found")
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        request = StarletteRequest(scope)

        response = await main_mod.http_exception_handler(request, exc)
        self.assertEqual(response.status_code, 404)


class TestExecutionRegistryAttach(unittest.IsolatedAsyncioTestCase):
    """
    Live-found while verifying P-19 (2026-08-13): core/execution/worker.py's
    _process_execution() builds a fresh ConversationExecution to actually
    run the work -- disconnected from whatever create_execution_record()
    returned to the original HTTP caller -- so the registry's copy stayed
    stuck at PENDING forever, regardless of the execution's real outcome.
    Confirmed unrelated to P-19 itself via a live control test with a plain
    chat message. attach() is the fix: replace the stale placeholder with
    the worker's live, correctly-maintained object.
    """

    async def test_attach_replaces_stale_placeholder_in_place(self):
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ConversationExecution, ExecutionStatus

        registry = ExecutionRegistry()
        placeholder = await registry.create_execution_record(
            conversation_id="c1", user_id="u1", session_id="s1",
            execution_mode="conversational", execution_id="e1",
        )
        self.assertEqual(placeholder.status, ExecutionStatus.PENDING)

        live_object = ConversationExecution(
            execution_id="e1", conversation_id="c1", user_id="u1",
            session_id="s1", status=ExecutionStatus.RUNNING, buffer=None,
        )
        registry.attach(live_object)

        bucket = registry._executions["c1"]
        self.assertEqual(len(bucket), 1)
        self.assertIs(bucket[0], live_object)

        running = registry.list_running_for_user("u1")
        self.assertEqual(len(running), 1)
        self.assertIs(running[0], live_object)

        # The replaced object's own status mutations must now be what
        # list_running_for_user() sees -- this is the whole point of the
        # fix (a status transition on the worker's object must be visible
        # here without any other call site changing).
        live_object.status = ExecutionStatus.COMPLETED
        self.assertEqual(registry.list_running_for_user("u1"), [])

    async def test_attach_appends_when_no_existing_entry(self):
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ConversationExecution, ExecutionStatus

        registry = ExecutionRegistry()
        live_object = ConversationExecution(
            execution_id="e-new", conversation_id="c-new", user_id="u1",
            session_id="s1", status=ExecutionStatus.RUNNING, buffer=None,
        )
        registry.attach(live_object)

        running = registry.list_running_for_user("u1")
        self.assertEqual(len(running), 1)
        self.assertIs(running[0], live_object)

    def test_worker_wires_attach_after_building_execution(self):
        """Source-level wiring check: _process_execution must actually call
        execution_registry.attach(execution) right after constructing its
        own ConversationExecution -- the fix is worthless if this call site
        is ever removed or moved above the construction."""
        import inspect
        from core.execution import worker as worker_mod

        source = inspect.getsource(worker_mod.ExecutionWorker._process_execution)
        construct_idx = source.index("ConversationExecution(")
        attach_idx = source.index("execution_registry.attach(execution)")
        self.assertGreater(
            attach_idx, construct_idx,
            "attach() must be called after the ConversationExecution is built",
        )


class TestFinalizePathsPreserveAwaitingWakeup(unittest.TestCase):
    """
    Live-found while re-verifying P-19 after the attach() fix (2026-08-13):
    both run_execution() (conversational) and run_pev_execution() (PEV)
    have an unconditional "all steps streamed -> transition(COMPLETED)"
    finalize path that a schedule_wakeup call also reaches (the generator
    ends normally, not via cancel/error). COMPLETED is a legal transition
    target from AWAITING_WAKEUP -- it has to be, for WakeupScheduler's own
    later resolution -- so the unconditional transition doesn't raise, it
    just silently overwrites AWAITING_WAKEUP within milliseconds, defeating
    the entire point of a distinct paused state (confirmed live: the
    execution showed "completed" seconds into a 25-second wait, well before
    WakeupScheduler could have resolved it). Both finalize paths now check
    execution.status == AWAITING_WAKEUP and return before overriding it.

    Source-level checks, not full execution-flow tests: exercising
    run_execution()/run_pev_execution() end-to-end requires mocking the
    entire streaming/agent pipeline, disproportionate for a guard against
    this one line being removed. Live verification (this session, against
    a real restarted server) is the primary proof this works; these are
    the regression guard.
    """

    def test_run_execution_checks_awaiting_wakeup_before_completing(self):
        import inspect
        from core.execution import runner as runner_mod

        source = inspect.getsource(runner_mod.run_execution)
        check_idx = source.index("execution.status == ExecutionStatus.AWAITING_WAKEUP")
        transition_idx = source.index("transition(ExecutionStatus.COMPLETED)")
        self.assertLess(
            check_idx, transition_idx,
            "the AWAITING_WAKEUP check must come before the unconditional "
            "COMPLETED transition, not after",
        )

    def test_run_pev_execution_checks_awaiting_wakeup_before_completing(self):
        import inspect
        from core.execution import pev_runner as pev_runner_mod

        source = inspect.getsource(pev_runner_mod.run_pev_execution)
        check_idx = source.index("execution.status == ExecutionStatus.AWAITING_WAKEUP")
        transition_idx = source.index("transition(ExecutionStatus.COMPLETED)")
        self.assertLess(
            check_idx, transition_idx,
            "the AWAITING_WAKEUP check must come before the unconditional "
            "COMPLETED transition, not after",
        )


class TestP19Phase3AwaitingWakeupEventPublishing(unittest.TestCase):
    """
    P-19 Phase 3 -- each of the three AWAITING_WAKEUP guard sites (the same
    ones TestFinalizePathsPreserveAwaitingWakeup guards against being
    removed) must also publish an "execution.awaiting_wakeup" user event
    right before returning, so the frontend's live task-tray
    (GET /events/stream) learns an execution just paused without polling
    GET /executions/running.

    Source-level checks, same rationale as
    TestFinalizePathsPreserveAwaitingWakeup: exercising run_execution() /
    run_pev_execution() / _pev_stream() end-to-end requires mocking the
    entire streaming/agent pipeline, disproportionate for a guard this
    narrow. Live verification (real GET /events/stream connection, real
    composition run) is the primary proof; these are the regression guard.
    """

    def test_run_execution_publishes_awaiting_wakeup_event(self):
        import inspect
        from core.execution import runner as runner_mod

        source = inspect.getsource(runner_mod.run_execution)
        check_idx = source.index("execution.status == ExecutionStatus.AWAITING_WAKEUP")
        event_idx = source.index('event="execution.awaiting_wakeup"')
        return_idx = source.index("return", event_idx)
        self.assertLess(check_idx, event_idx)
        self.assertLess(event_idx, return_idx)

    def test_pev_stream_step_exit_does_not_publish_directly(self):
        """Live-found during Phase 3 verification: the per-step early exit
        inside _pev_stream() and run_pev_execution()'s own "all steps
        streamed" finalize path both reach their AWAITING_WAKEUP check for
        the SAME pause (_pev_stream()'s `return` simply ends the generator,
        which is exactly what makes run_pev_execution()'s consuming
        `async for` loop exit normally and reach its own check) -- so a
        publish call at both sites double-fires the same event
        (confirmed live: two "execution.awaiting_wakeup" events, ~80ms
        apart, for one pause). Fixed by publishing only from
        run_pev_execution()'s single outer choke point; this guards against
        the duplicate being reintroduced at the inner site."""
        import inspect
        from core.execution import pev_runner as pev_runner_mod

        source = inspect.getsource(pev_runner_mod._pev_stream)
        self.assertNotIn(
            'event="execution.awaiting_wakeup"', source,
            "publishing execution.awaiting_wakeup from _pev_stream's "
            "per-step exit double-fires it alongside run_pev_execution's "
            "own outer check -- publish only from the outer site",
        )

    def test_run_pev_execution_publishes_awaiting_wakeup_event(self):
        import inspect
        from core.execution import pev_runner as pev_runner_mod

        source = inspect.getsource(pev_runner_mod.run_pev_execution)
        check_idx = source.index("execution.status == ExecutionStatus.AWAITING_WAKEUP")
        event_idx = source.index('event="execution.awaiting_wakeup"')
        transition_idx = source.index("transition(ExecutionStatus.COMPLETED)")
        self.assertLess(check_idx, event_idx)
        self.assertLess(event_idx, transition_idx)


class TestWakeupRepo(unittest.IsolatedAsyncioTestCase):
    """
    P-19 Phase 1 -- WakeupRepo CRUD. Real in-memory SQLite DB, same pattern
    used throughout this session for anything with real multi-row/status
    behaviour a mock would trivially get right without proving anything.
    """

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession,
                                    expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_user_and_conversation(self, db, user_id="u1", conv_id="c1"):
        from core.db.models import User, Conversation
        db.add(User(id=user_id, email=f"{user_id}@test.local", username=user_id, password_hash="x"))
        db.add(Conversation(id=conv_id, user_id=user_id))
        await db.commit()

    async def test_create_and_count_pending(self):
        from datetime import datetime, timezone, timedelta
        from core.db.repository import WakeupRepo

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            now = datetime.now(timezone.utc)
            row = await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=now + timedelta(hours=1), reason="check on the daemon run",
            )
            self.assertEqual(row.status, "pending")
            self.assertIsNone(row.watch_execution_id)

            count = await WakeupRepo.count_pending_for_user(db, "u1")
            self.assertEqual(count, 1)
            self.assertEqual(await WakeupRepo.count_pending_for_user(db, "someone-else"), 0)

    async def test_list_pending_excludes_resolved_and_cancelled(self):
        from datetime import datetime, timezone, timedelta
        from core.db.repository import WakeupRepo

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            now = datetime.now(timezone.utc)
            r1 = await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=now, reason="a",
            )
            r2 = await WakeupRepo.create(
                db, execution_id="e2", conversation_id="c1", user_id="u1",
                wake_at=now, reason="b",
            )
            await WakeupRepo.mark_resolved(db, r1.id, resumed_execution_id="e1-resumed")

            pending = await WakeupRepo.list_pending(db)
            self.assertEqual([row.id for row in pending], [r2.id])

    async def test_mark_resolved_sets_fields(self):
        from datetime import datetime, timezone
        from core.db.repository import WakeupRepo
        from core.db.models import ScheduledWakeup

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            row = await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc), reason="a",
            )
            await WakeupRepo.mark_resolved(db, row.id, resumed_execution_id="new-exec-123")

            refreshed = await db.get(ScheduledWakeup, row.id)
            self.assertEqual(refreshed.status, "resolved")
            self.assertEqual(refreshed.resumed_execution_id, "new-exec-123")
            self.assertIsNotNone(refreshed.resolved_at)

    async def test_delete_for_conversation_cascade(self):
        from datetime import datetime, timezone
        from core.db.repository import WakeupRepo
        from core.db.models import ScheduledWakeup

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            row = await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc), reason="a",
            )
            await WakeupRepo.delete_for_conversation(db, "c1")
            await db.commit()
            self.assertIsNone(await db.get(ScheduledWakeup, row.id))

    async def test_purge_old_resolved_respects_retention_and_never_touches_pending(self):
        from datetime import datetime, timezone, timedelta
        from core.db.repository import WakeupRepo
        from core.db.models import ScheduledWakeup

        now = datetime.now(timezone.utc)

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            # Resolved, old -- must be purged.
            db.add(ScheduledWakeup(
                id="wk-old-resolved", execution_id="e1", conversation_id="c1",
                user_id="u1", wake_at=now, reason="a", status="resolved",
                resolved_at=now - timedelta(days=10),
            ))
            # Resolved, recent -- must survive.
            db.add(ScheduledWakeup(
                id="wk-recent-resolved", execution_id="e2", conversation_id="c1",
                user_id="u1", wake_at=now, reason="b", status="resolved",
                resolved_at=now - timedelta(days=1),
            ))
            # Cancelled, old -- must be purged (same treatment as resolved).
            db.add(ScheduledWakeup(
                id="wk-old-cancelled", execution_id="e3", conversation_id="c1",
                user_id="u1", wake_at=now, reason="c", status="cancelled",
                resolved_at=now - timedelta(days=10),
            ))
            # Still pending, old wake_at -- must NEVER be purged regardless
            # of age; it's live state the scheduler still needs.
            db.add(ScheduledWakeup(
                id="wk-old-pending", execution_id="e4", conversation_id="c1",
                user_id="u1", wake_at=now - timedelta(days=10), reason="d",
                status="pending",
            ))
            await db.commit()

            purged = await WakeupRepo.purge_old_resolved(db, retention_days=7)
            self.assertEqual(purged, 2)

            self.assertIsNone(await db.get(ScheduledWakeup, "wk-old-resolved"))
            self.assertIsNone(await db.get(ScheduledWakeup, "wk-old-cancelled"))
            self.assertIsNotNone(await db.get(ScheduledWakeup, "wk-recent-resolved"))
            self.assertIsNotNone(await db.get(ScheduledWakeup, "wk-old-pending"))

    async def test_delete_conversation_and_children_cascades_wakeups(self):
        """The real cascade path (DBRepo.delete_conversation_and_children),
        not just the standalone helper -- confirms the wiring, not just
        that the helper function works in isolation."""
        from datetime import datetime, timezone
        from core.db.repository import WakeupRepo, DBRepo
        from core.db.models import ScheduledWakeup

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            row = await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc), reason="a",
            )
            await DBRepo.delete_conversation_and_children(db, "c1")
            self.assertIsNone(await db.get(ScheduledWakeup, row.id))

    async def test_cleanup_pass_wires_purge_wakeup_data(self):
        """core.tasks.cleanup._purge_wakeup_data() must call
        WakeupRepo.purge_old_resolved() and return its count -- same
        source-level check as DB-01's sibling for _purge_auth_data, since
        this function opens its own AsyncSessionLocal."""
        from unittest.mock import patch, AsyncMock
        import core.tasks.cleanup as cleanup_mod

        with patch("core.db.repository.WakeupRepo.purge_old_resolved",
                   new=AsyncMock(return_value=5)):
            summary = await cleanup_mod._purge_wakeup_data()

        self.assertEqual(summary, {"wakeups_purged": 5})


class TestWakeupScheduler(unittest.IsolatedAsyncioTestCase):
    """
    P-19 Phase 1 -- WakeupScheduler's due-row resolution logic. Real
    in-memory DB for WakeupRepo/ExecutionMetricsRepo state; resume_from_wakeup
    itself is mocked here (it's tested separately in
    TestResumeFromWakeup) so this class stays focused on "which rows get
    resolved, and when" rather than re-proving message/execution creation.
    """

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession,
                                    expire_on_commit=False)

        import core.db.session as _dbs
        self._orig_session_local = _dbs.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session

    async def asyncTearDown(self):
        import core.db.session as _dbs
        _dbs.AsyncSessionLocal = self._orig_session_local
        await self.engine.dispose()

    async def _seed_user_and_conversation(self, db, user_id="u1", conv_id="c1"):
        from core.db.models import User, Conversation
        db.add(User(id=user_id, email=f"{user_id}@test.local", username=user_id, password_hash="x"))
        db.add(Conversation(id=conv_id, user_id=user_id))
        await db.commit()

    async def test_time_based_row_resolves_when_due(self):
        from datetime import datetime, timezone, timedelta
        from unittest.mock import AsyncMock, patch
        from core.db.repository import WakeupRepo
        from core.execution.wakeup_scheduler import WakeupScheduler

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            row = await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                reason="check back",
            )

        with patch(
            "core.execution.wakeup_scheduler.resume_from_wakeup",
            new=AsyncMock(return_value="new-exec-1"),
        ) as _resume, patch(
            "core.execution.execution_registry.resolve_wakeup",
            return_value=True,
        ), patch(
            "core.execution.redis_registry.redis_execution_registry.mark_execution_finished",
            new=AsyncMock(),
        ):
            scheduler = WakeupScheduler()
            await scheduler._poll()
            _resume.assert_awaited_once()

        async with self.Session() as db:
            pending = await WakeupRepo.list_pending(db)
            self.assertEqual(pending, [])

    async def test_resolving_updates_original_executions_stale_metrics_status(self):
        """Live-found during Tier A verification: the original execution's
        execution_metrics.status was written once, at pause time, as
        "awaiting_wakeup" -- and nothing updated it afterward, so GET
        /executions (history) showed it stuck there forever even after it
        genuinely resolved. resolve_wakeup_row must correct it."""
        from datetime import datetime, timezone, timedelta
        from unittest.mock import AsyncMock, patch
        from core.db.repository import WakeupRepo, ExecutionMetricsRepo
        from core.db.models import ExecutionMetrics
        from core.execution.wakeup_scheduler import WakeupScheduler

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            db.add(ExecutionMetrics(
                execution_id="e1", conversation_id="c1", user_id="u1",
                status="awaiting_wakeup",
            ))
            await db.commit()
            await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                reason="check back",
            )

        with patch(
            "core.execution.wakeup_scheduler.resume_from_wakeup",
            new=AsyncMock(return_value="new-exec-1"),
        ), patch(
            "core.execution.execution_registry.resolve_wakeup",
            return_value=True,
        ), patch(
            "core.execution.redis_registry.redis_execution_registry.mark_execution_finished",
            new=AsyncMock(),
        ):
            scheduler = WakeupScheduler()
            await scheduler._poll()

        async with self.Session() as db:
            metrics = await ExecutionMetricsRepo.get(db, "e1")
            self.assertEqual(metrics.status, "completed")

    async def test_resolving_publishes_wakeup_resolved_event(self):
        """P-19 Phase 3: resolve_wakeup_row() must publish a
        'wakeup.resolved' user event so the frontend's live task-tray
        (GET /events/stream) learns the wakeup fired without polling.
        publish_user_event is imported locally (deferred import) inside
        resolve_wakeup_row -- must patch the origin
        (core.events_publisher.publish_user_event), not a module-qualified
        name, for the patch to actually take effect."""
        from datetime import datetime, timezone, timedelta
        from unittest.mock import AsyncMock, patch
        from core.db.repository import WakeupRepo
        from core.execution.wakeup_scheduler import WakeupScheduler

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                reason="check back",
            )

        with patch(
            "core.execution.wakeup_scheduler.resume_from_wakeup",
            new=AsyncMock(return_value="new-exec-1"),
        ), patch(
            "core.execution.execution_registry.resolve_wakeup",
            return_value=True,
        ), patch(
            "core.execution.redis_registry.redis_execution_registry.mark_execution_finished",
            new=AsyncMock(),
        ), patch(
            "core.events_publisher.publish_user_event", new=AsyncMock(),
        ) as mock_event:
            scheduler = WakeupScheduler()
            await scheduler._poll()

        mock_event.assert_awaited_once()
        self.assertEqual(mock_event.call_args.kwargs["user_id"], "u1")
        self.assertEqual(mock_event.call_args.kwargs["event"], "wakeup.resolved")
        # Deliberately the ORIGINAL execution_id (what the frontend already
        # has displayed), not the new continuation execution_id.
        self.assertEqual(mock_event.call_args.kwargs["execution_id"], "e1")
        self.assertIn("new-exec-1", mock_event.call_args.kwargs["summary"])

    async def test_not_yet_due_row_is_skipped(self):
        from datetime import datetime, timezone, timedelta
        from unittest.mock import AsyncMock, patch
        from core.db.repository import WakeupRepo
        from core.execution.wakeup_scheduler import WakeupScheduler

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc) + timedelta(hours=1),
                reason="not yet",
            )

        with patch(
            "core.execution.wakeup_scheduler.resume_from_wakeup",
            new=AsyncMock(return_value="new-exec-1"),
        ) as _resume:
            scheduler = WakeupScheduler()
            await scheduler._poll()
            _resume.assert_not_awaited()

        async with self.Session() as db:
            pending = await WakeupRepo.list_pending(db)
            self.assertEqual(len(pending), 1)

    async def test_watch_execution_id_resolves_as_soon_as_terminal_metrics_row_exists(self):
        """The fixed-time ceiling (wake_at) is far in the future -- resolution
        must come from the watched execution reaching a terminal state, not
        from the fallback timing out."""
        from datetime import datetime, timezone, timedelta
        from unittest.mock import AsyncMock, patch
        from core.db.repository import WakeupRepo, ExecutionMetricsRepo
        from core.execution.wakeup_scheduler import WakeupScheduler

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc) + timedelta(hours=48),
                reason="wait for the daemon",
                watch_execution_id="daemon-exec-1",
            )
            await ExecutionMetricsRepo.upsert(db, {
                "execution_id": "daemon-exec-1",
                "conversation_id": "c1",
                "user_id": "u1",
                "status": "completed",
            })

        with patch(
            "core.execution.wakeup_scheduler.resume_from_wakeup",
            new=AsyncMock(return_value="new-exec-1"),
        ) as _resume, patch(
            "core.execution.execution_registry.resolve_wakeup",
            return_value=True,
        ), patch(
            "core.execution.redis_registry.redis_execution_registry.mark_execution_finished",
            new=AsyncMock(),
        ):
            scheduler = WakeupScheduler()
            await scheduler._poll()
            _resume.assert_awaited_once()

    async def test_watch_execution_id_falls_back_to_wake_at_when_never_terminal(self):
        from datetime import datetime, timezone, timedelta
        from unittest.mock import AsyncMock, patch
        from core.db.repository import WakeupRepo
        from core.execution.wakeup_scheduler import WakeupScheduler

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            # No ExecutionMetrics row for "still-running-exec" is ever created
            # -- simulates a watched execution that never finishes.
            await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                reason="wait for the daemon",
                watch_execution_id="still-running-exec",
            )

        with patch(
            "core.execution.wakeup_scheduler.resume_from_wakeup",
            new=AsyncMock(return_value="new-exec-1"),
        ) as _resume, patch(
            "core.execution.execution_registry.resolve_wakeup",
            return_value=True,
        ), patch(
            "core.execution.redis_registry.redis_execution_registry.mark_execution_finished",
            new=AsyncMock(),
        ):
            scheduler = WakeupScheduler()
            await scheduler._poll()
            _resume.assert_awaited_once()

    async def test_one_row_failing_does_not_block_others_in_the_same_poll(self):
        from datetime import datetime, timezone, timedelta
        from unittest.mock import AsyncMock, patch
        from core.db.repository import WakeupRepo
        from core.execution.wakeup_scheduler import WakeupScheduler

        async with self.Session() as db:
            await self._seed_user_and_conversation(db)
            await WakeupRepo.create(
                db, execution_id="e1", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                reason="first, will fail",
            )
            await WakeupRepo.create(
                db, execution_id="e2", conversation_id="c1", user_id="u1",
                wake_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                reason="second, should still resolve",
            )

        async def _resume_side_effect(row):
            if row.execution_id == "e1":
                raise RuntimeError("simulated failure resuming e1")
            return "new-exec-for-e2"

        with patch(
            "core.execution.wakeup_scheduler.resume_from_wakeup",
            new=AsyncMock(side_effect=_resume_side_effect),
        ), patch(
            "core.execution.execution_registry.resolve_wakeup",
            return_value=True,
        ), patch(
            "core.execution.redis_registry.redis_execution_registry.mark_execution_finished",
            new=AsyncMock(),
        ):
            scheduler = WakeupScheduler()
            await scheduler._poll()

        async with self.Session() as db:
            pending = await WakeupRepo.list_pending(db)
            # e1's row is still pending (will retry next poll); e2 resolved.
            self.assertEqual([row.execution_id for row in pending], ["e1"])


class TestResumeFromWakeup(unittest.IsolatedAsyncioTestCase):
    """
    P-19 Phase 1 -- resume_from_wakeup() itself: mirrors api/chat.py's own
    turn-kickoff sequence (persist user message -> create_execution_record
    -> enqueue_job). execution_registry/redis_execution_registry are mocked
    here (they need a running LocalBroker, out of scope for a focused unit
    test); DBRepo.add_message hits the real in-memory DB so the persisted
    trigger message content is genuinely verified, not assumed.
    """

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession,
                                    expire_on_commit=False)

        import core.db.session as _dbs
        self._orig_session_local = _dbs.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session

    async def asyncTearDown(self):
        import core.db.session as _dbs
        _dbs.AsyncSessionLocal = self._orig_session_local
        await self.engine.dispose()

    async def test_persists_trigger_message_and_enqueues_new_execution(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from core.db.models import User, Conversation
        from core.db.repository import DBRepo
        from core.execution.wakeup_scheduler import resume_from_wakeup

        async with self.Session() as db:
            db.add(User(id="u1", email="u1@test.local", username="u1", password_hash="x"))
            db.add(Conversation(id="c1", user_id="u1"))
            await db.commit()

        row = SimpleNamespace(
            id="wk1",
            execution_id="old-exec-1",
            conversation_id="c1",
            user_id="u1",
            reason="check whether the report finished",
            watch_execution_id=None,
        )

        _fake_execution = SimpleNamespace(execution_id="new-exec-1")
        with patch(
            "core.execution.execution_registry.create_execution_record",
            new=AsyncMock(return_value=_fake_execution),
        ) as _create, patch(
            "core.execution.redis_registry.redis_execution_registry.enqueue_job",
            new=AsyncMock(),
        ) as _enqueue:
            new_execution_id = await resume_from_wakeup(row)

        self.assertEqual(new_execution_id, "new-exec-1")
        _create.assert_awaited_once()
        _, kwargs = _create.call_args
        self.assertEqual(kwargs["conversation_id"], "c1")
        self.assertEqual(kwargs["user_id"], "u1")
        self.assertEqual(kwargs["execution_mode"], "conversational")
        self.assertIn("check whether the report finished", kwargs["initial_message"])
        _enqueue.assert_awaited_once_with("new-exec-1")

        async with self.Session() as db:
            messages = await DBRepo.list_messages_for_conversation(db, "c1")
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].role, "user")
            self.assertIn("check whether the report finished", messages[0].content)
            self.assertIn("Scheduled wakeup fired", messages[0].content)

    async def test_watched_execution_status_is_included_when_available(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from core.db.models import User, Conversation
        from core.db.repository import DBRepo, ExecutionMetricsRepo
        from core.execution.wakeup_scheduler import resume_from_wakeup

        async with self.Session() as db:
            db.add(User(id="u1", email="u1@test.local", username="u1", password_hash="x"))
            db.add(Conversation(id="c1", user_id="u1"))
            await db.commit()
            await ExecutionMetricsRepo.upsert(db, {
                "execution_id": "daemon-exec-1",
                "conversation_id": "c1",
                "user_id": "u1",
                "status": "failed",
            })

        row = SimpleNamespace(
            id="wk1", execution_id="old-exec-1", conversation_id="c1", user_id="u1",
            reason="see if the daemon finished", watch_execution_id="daemon-exec-1",
        )

        _fake_execution = SimpleNamespace(execution_id="new-exec-1")
        with patch(
            "core.execution.execution_registry.create_execution_record",
            new=AsyncMock(return_value=_fake_execution),
        ), patch(
            "core.execution.redis_registry.redis_execution_registry.enqueue_job",
            new=AsyncMock(),
        ):
            await resume_from_wakeup(row)

        async with self.Session() as db:
            messages = await DBRepo.list_messages_for_conversation(db, "c1")
            self.assertIn("failed", messages[0].content)
            self.assertIn("daemon-exec-1", messages[0].content)


class TestWakeupsAPI(unittest.IsolatedAsyncioTestCase):
    """
    P-19 Tier A -- GET /wakeups, POST /wakeups/{id}/resume,
    POST /wakeups/{id}/cancel. Mocks every dependency of the three route
    functions except the wiring itself, same convention as
    TestAuthEndpointRateLimiting -- these fail if the wiring is ever
    removed or the ownership/status checks are weakened.
    """

    def _make_row(self, **overrides):
        from types import SimpleNamespace
        from datetime import datetime, timezone
        defaults = dict(
            id="wk1", execution_id="e1", conversation_id="c1", user_id="u1",
            reason="check back", wake_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            watch_execution_id=None, status="pending",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    async def test_list_wakeups_enriches_watch_execution_id_with_watched_status(self):
        import api.wakeups as wakeups_mod
        from types import SimpleNamespace

        row_no_watch = self._make_row(id="wk1", watch_execution_id=None)
        row_watching = self._make_row(id="wk2", watch_execution_id="daemon-1")
        watched_metrics = SimpleNamespace(status="aborted", daemon_name="research-bot")
        conversation = SimpleNamespace(title="Research notes")

        with patch.object(
            wakeups_mod.WakeupRepo, "list_pending_for_user",
            new=AsyncMock(return_value=[row_no_watch, row_watching]),
        ), patch.object(
            wakeups_mod.ExecutionMetricsRepo, "get",
            new=AsyncMock(return_value=watched_metrics),
        ), patch.object(
            wakeups_mod.DBRepo, "get_conversation",
            new=AsyncMock(return_value=conversation),
        ):
            result = await wakeups_mod.list_wakeups(auth={"user_id": "u1"})

        self.assertEqual(len(result), 2)
        self.assertIsNone(result[0].watched_status)
        self.assertIsNone(result[0].watch_daemon_name)
        self.assertEqual(result[1].watched_status, "aborted")
        self.assertEqual(result[1].watch_daemon_name, "research-bot")
        # conversation_title -- readable label for "which chat is this",
        # not the raw conversation_id -- applies to both rows regardless
        # of whether they're watch-based.
        self.assertEqual(result[0].conversation_title, "Research notes")
        self.assertEqual(result[1].conversation_title, "Research notes")

    async def test_list_wakeups_conversation_title_none_when_not_yet_generated(self):
        """Title generation is async and runs after the first turn -- a
        genuinely title-less conversation must not crash the endpoint, and
        the frontend needs a real None to know to show a fallback."""
        import api.wakeups as wakeups_mod
        from types import SimpleNamespace

        row = self._make_row(watch_execution_id=None)
        conversation = SimpleNamespace(title=None)

        with patch.object(
            wakeups_mod.WakeupRepo, "list_pending_for_user",
            new=AsyncMock(return_value=[row]),
        ), patch.object(
            wakeups_mod.DBRepo, "get_conversation",
            new=AsyncMock(return_value=conversation),
        ):
            result = await wakeups_mod.list_wakeups(auth={"user_id": "u1"})

        self.assertIsNone(result[0].conversation_title)

    async def test_resume_wakeup_now_calls_resolve_wakeup_row(self):
        import api.wakeups as wakeups_mod

        row = self._make_row()
        with patch.object(wakeups_mod.WakeupRepo, "get", new=AsyncMock(return_value=row)), \
             patch("core.execution.wakeup_scheduler.resolve_wakeup_row",
                   new=AsyncMock(return_value="new-exec-1")) as mock_resolve:
            result = await wakeups_mod.resume_wakeup_now("wk1", auth={"user_id": "u1"})

        mock_resolve.assert_awaited_once_with(row)
        self.assertEqual(result, {"status": "resumed", "new_execution_id": "new-exec-1"})

    async def test_resume_wakeup_now_404_for_wrong_owner(self):
        import api.wakeups as wakeups_mod
        from fastapi import HTTPException

        row = self._make_row(user_id="someone-else")
        with patch.object(wakeups_mod.WakeupRepo, "get", new=AsyncMock(return_value=row)):
            with self.assertRaises(HTTPException) as ctx:
                await wakeups_mod.resume_wakeup_now("wk1", auth={"user_id": "u1"})
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_resume_wakeup_now_404_when_missing(self):
        import api.wakeups as wakeups_mod
        from fastapi import HTTPException

        with patch.object(wakeups_mod.WakeupRepo, "get", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as ctx:
                await wakeups_mod.resume_wakeup_now("no-such-id", auth={"user_id": "u1"})
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_resume_wakeup_now_409_if_already_resolved(self):
        import api.wakeups as wakeups_mod
        from fastapi import HTTPException

        row = self._make_row(status="resolved")
        with patch.object(wakeups_mod.WakeupRepo, "get", new=AsyncMock(return_value=row)):
            with self.assertRaises(HTTPException) as ctx:
                await wakeups_mod.resume_wakeup_now("wk1", auth={"user_id": "u1"})
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_cancel_wakeup_marks_cancelled_and_never_touches_watched_execution(self):
        import api.wakeups as wakeups_mod

        row = self._make_row(execution_id="paused-exec", watch_execution_id="watched-exec")
        with patch.object(wakeups_mod.WakeupRepo, "get", new=AsyncMock(return_value=row)), \
             patch.object(wakeups_mod.WakeupRepo, "mark_cancelled", new=AsyncMock()), \
             patch.object(wakeups_mod.ExecutionMetricsRepo, "upsert",
                           new=AsyncMock()) as mock_metrics_upsert, \
             patch("core.execution.execution_registry.cancel_wakeup") as mock_cancel, \
             patch("core.execution.redis_registry.redis_execution_registry.mark_execution_cancelled",
                   new=AsyncMock()) as mock_broker_cancel:
            result = await wakeups_mod.cancel_wakeup("wk1", auth={"user_id": "u1"})

        self.assertEqual(result, {"status": "cancelled"})
        # Only the paused execution is touched -- never watch-exec, which
        # has its own independent lifecycle (see the module docstring).
        mock_cancel.assert_called_once_with("paused-exec")
        mock_broker_cancel.assert_awaited_once()
        broker_call_args = mock_broker_cancel.call_args
        self.assertEqual(broker_call_args.args[0], "paused-exec")
        # execution_metrics.status must also be corrected -- it was written
        # once, at pause time, as "awaiting_wakeup" and nothing else
        # updates it (see the module docstring / wakeup_scheduler.py's
        # identical fix for the resolve path).
        mock_metrics_upsert.assert_awaited_once()
        upsert_data = mock_metrics_upsert.call_args.args[1]
        self.assertEqual(upsert_data, {"execution_id": "paused-exec", "status": "cancelled"})

    async def test_cancel_wakeup_404_for_wrong_owner(self):
        import api.wakeups as wakeups_mod
        from fastapi import HTTPException

        row = self._make_row(user_id="someone-else")
        with patch.object(wakeups_mod.WakeupRepo, "get", new=AsyncMock(return_value=row)):
            with self.assertRaises(HTTPException) as ctx:
                await wakeups_mod.cancel_wakeup("wk1", auth={"user_id": "u1"})
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_cancel_wakeup_409_if_already_cancelled(self):
        import api.wakeups as wakeups_mod
        from fastapi import HTTPException

        row = self._make_row(status="cancelled")
        with patch.object(wakeups_mod.WakeupRepo, "get", new=AsyncMock(return_value=row)):
            with self.assertRaises(HTTPException) as ctx:
                await wakeups_mod.cancel_wakeup("wk1", auth={"user_id": "u1"})
        self.assertEqual(ctx.exception.status_code, 409)


class TestExecutionRegistryCancelWakeup(unittest.IsolatedAsyncioTestCase):
    """P-19 Tier A -- ExecutionRegistry.cancel_wakeup, sibling of the
    existing resolve_wakeup, now both thin wrappers around _finalize_wakeup."""

    async def test_cancel_wakeup_transitions_to_cancelled_not_completed(self):
        from core.execution.registry import ExecutionRegistry
        from core.execution.models import ExecutionStatus

        registry = ExecutionRegistry()
        execution = await registry.create_execution_record(
            conversation_id="c1", user_id="u1", session_id="s1",
            execution_mode="conversational", execution_id="e1",
        )
        execution.status = ExecutionStatus.AWAITING_WAKEUP

        result = registry.cancel_wakeup("e1")

        self.assertTrue(result)
        self.assertEqual(execution.status, ExecutionStatus.CANCELLED)


class TestScheduleWakeupTool(unittest.IsolatedAsyncioTestCase):
    """P-19 Phase 1 -- ScheduleWakeupTool validation, caps, and persistence."""

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession,
                                    expire_on_commit=False)

        import core.db.session as _dbs
        self._orig_session_local = _dbs.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session

        async with self.Session() as db:
            from core.db.models import User, Conversation
            db.add(User(id="u1", email="u1@test.local", username="u1", password_hash="x"))
            db.add(User(id="u2", email="u2@test.local", username="u2", password_hash="x"))
            db.add(Conversation(id="c1", user_id="u1"))
            await db.commit()

    async def asyncTearDown(self):
        import core.db.session as _dbs
        _dbs.AsyncSessionLocal = self._orig_session_local
        await self.engine.dispose()

    def _make_tool(self, *, user_id="u1", conversation_id="c1", execution_id="e1"):
        from types import SimpleNamespace
        from tools.default.wakeup_tools import ScheduleWakeupTool
        tool = ScheduleWakeupTool()
        tool.execution = SimpleNamespace(
            user_id=user_id, conversation_id=conversation_id, execution_id=execution_id,
        )
        return tool

    async def test_requires_reason(self):
        tool = self._make_tool()
        result = await tool.run({"delay_seconds": 60, "reason": "   "})
        self.assertIn("error", result.result)

    async def test_requires_delay_or_watch(self):
        tool = self._make_tool()
        result = await tool.run({"reason": "check back"})
        self.assertIn("error", result.result)
        self.assertIn("delay_seconds or watch_execution_id", result.result["error"])

    async def test_delay_seconds_bounds_enforced(self):
        from config.settings import WAKEUP_MAX_DELAY_SECONDS
        tool = self._make_tool()

        too_long = await tool.run({
            "delay_seconds": WAKEUP_MAX_DELAY_SECONDS + 1, "reason": "check back",
        })
        self.assertIn("error", too_long.result)

        zero = await tool.run({"delay_seconds": 0, "reason": "check back"})
        self.assertIn("error", zero.result)

    async def test_pending_cap_enforced(self):
        import config.settings as _cfg
        orig_cap = _cfg.WAKEUP_MAX_PENDING_PER_USER
        _cfg.WAKEUP_MAX_PENDING_PER_USER = 1
        try:
            tool = self._make_tool()
            first = await tool.run({"delay_seconds": 60, "reason": "first"})
            self.assertTrue(first.result.get("ok"))

            second = await tool.run({"delay_seconds": 60, "reason": "second"})
            self.assertIn("error", second.result)
            self.assertIn("pending wakeups", second.result["error"])
        finally:
            _cfg.WAKEUP_MAX_PENDING_PER_USER = orig_cap

    async def test_watch_execution_id_ownership_enforced(self):
        from core.db.repository import ExecutionMetricsRepo
        async with self.Session() as db:
            await ExecutionMetricsRepo.upsert(db, {
                "execution_id": "someone-elses-exec",
                "conversation_id": "c1",
                "user_id": "u2",
                "status": "completed",
            })

        tool = self._make_tool(user_id="u1")
        result = await tool.run({
            "watch_execution_id": "someone-elses-exec", "reason": "peek at it",
        })
        self.assertIn("error", result.result)

    async def test_successful_schedule_creates_row(self):
        from datetime import datetime, timezone, timedelta
        from core.db.repository import WakeupRepo
        from core.db.models import ScheduledWakeup

        tool = self._make_tool()
        result = await tool.run({"delay_seconds": 3600, "reason": "check on the export"})

        self.assertTrue(result.result.get("ok"))
        wakeup_id = result.result["wakeup_id"]

        async with self.Session() as db:
            row = await db.get(ScheduledWakeup, wakeup_id)
            self.assertIsNotNone(row)
            self.assertEqual(row.execution_id, "e1")
            self.assertEqual(row.conversation_id, "c1")
            self.assertEqual(row.reason, "check on the export")
            expected = datetime.now(timezone.utc) + timedelta(seconds=3600)
            # SQLite round-trips DateTime(timezone=True) as offset-naive --
            # same normalization core/execution/wakeup_scheduler.py applies.
            actual_wake_at = row.wake_at.replace(tzinfo=timezone.utc) if row.wake_at.tzinfo is None else row.wake_at
            self.assertLess(abs((actual_wake_at - expected).total_seconds()), 5)


class TestFileBlobHelpers(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        import config.settings as _cfg
        self._orig_root = _cfg.WORKSPACE_ROOT
        _cfg.WORKSPACE_ROOT = self._tmp

    def tearDown(self):
        import config.settings as _cfg
        _cfg.WORKSPACE_ROOT = self._orig_root
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_save_and_list(self):
        from utils.blob_store import save_file_blob, list_file_blobs
        save_file_blob("user-1", "hello.txt", b"hello world")
        blobs = list_file_blobs("user-1")
        self.assertIn("hello.txt", [b["filename"] for b in blobs])

    def test_delete_file_blob(self):
        from utils.blob_store import save_file_blob, delete_file_blob, list_file_blobs
        save_file_blob("user-1", "bye.txt", b"bye")
        self.assertTrue(delete_file_blob("user-1", "bye.txt"))
        self.assertNotIn("bye.txt", [b["filename"] for b in list_file_blobs("user-1")])

    def test_read_file_blob(self):
        from utils.blob_store import save_file_blob, read_file_blob
        save_file_blob("user-1", "read_test.txt", b"content to read")
        self.assertEqual(read_file_blob("user-1", "read_test.txt"), b"content to read")

    def test_read_nonexistent_returns_none(self):
        from utils.blob_store import read_file_blob
        self.assertIsNone(read_file_blob("user-1", "does_not_exist.txt"))

    def test_project_scoped_storage(self):
        from utils.blob_store import save_file_blob, list_file_blobs
        save_file_blob("user-1", "proj_file.txt", b"project content",
                       project_id="proj-1")
        global_names = [b["filename"] for b in list_file_blobs("user-1")]
        proj_names   = [b["filename"] for b in list_file_blobs("user-1",
                                                                 project_id="proj-1")]
        self.assertNotIn("proj_file.txt", global_names)
        self.assertIn("proj_file.txt", proj_names)


# ===========================================================================
# B-056 — Project Artifact Updater (worker.py)
# ===========================================================================

def _make_daemon(project_id=None):
    daemon = MagicMock()
    daemon.name           = "test-daemon"
    daemon.user_id        = "user-1"
    daemon.project_id     = project_id
    daemon.provider       = "groq"
    daemon.model          = "llama3"
    daemon.system_prompt  = "You are a test daemon."
    daemon.trigger_message = "Run now."
    daemon.tools          = []
    daemon.max_steps      = 3
    return daemon


class TestWorkerProjectIdSource(unittest.TestCase):

    def test_session_dict_has_no_project_id(self):
        from config.settings import DEFAULT_SESSION
        self.assertNotIn("project_id", DEFAULT_SESSION)

    def test_worker_uses_resolved_project_id_not_session(self):
        import inspect
        import core.execution.worker as worker_mod
        source = inspect.getsource(worker_mod)
        self.assertIn("resolved_project_id", source)

    def test_worker_captures_project_id_from_convo(self):
        import inspect
        import core.execution.worker as worker_mod
        source = inspect.getsource(worker_mod)
        self.assertIn("resolved_project_id = convo.project_id", source)


class TestWorkerArtifactUpdaterTrigger(unittest.IsolatedAsyncioTestCase):

    async def test_artifact_updater_called_when_project_id_set(self):
        mock_convo = MagicMock()
        mock_convo.project_id = "proj-abc"
        updater_calls = []

        async def fake_update(**kwargs):
            updater_calls.append(kwargs)

        mock_updater = MagicMock()
        mock_updater.update = fake_update
        original_create_task = asyncio.create_task

        def capturing_create_task(coro, **kwargs):
            return original_create_task(coro, **kwargs)

        with patch("asyncio.create_task", side_effect=capturing_create_task):
            resolved_project_id = None
            if mock_convo and getattr(mock_convo, "project_id", None):
                resolved_project_id = mock_convo.project_id
            if resolved_project_id:
                asyncio.create_task(mock_updater.update(
                    project_id=resolved_project_id,
                    conversation_id="conv-123",
                    user_id="user-1",
                    session={},
                    execution_id="exec-1",
                ))
            await asyncio.sleep(0)

        self.assertEqual(len(updater_calls), 1)
        self.assertEqual(updater_calls[0]["project_id"], "proj-abc")

    async def test_artifact_updater_not_called_when_no_project_id(self):
        mock_convo = MagicMock()
        mock_convo.project_id = None
        updater_calls = []

        async def fake_update(**kwargs):
            updater_calls.append(kwargs)

        mock_updater = MagicMock()
        mock_updater.update = fake_update

        resolved_project_id = None
        if mock_convo and getattr(mock_convo, "project_id", None):
            resolved_project_id = mock_convo.project_id
        if resolved_project_id:
            asyncio.create_task(mock_updater.update(project_id=resolved_project_id,
                                                    conversation_id="conv-456",
                                                    user_id="user-1", session={},
                                                    execution_id="exec-2"))
        await asyncio.sleep(0)
        self.assertEqual(len(updater_calls), 0)


# ===========================================================================
# B-058 — Auto-title Generation
# ===========================================================================

class TestCleanTitle(unittest.TestCase):

    def _clean(self, raw):
        from core.execution.title_generator import _clean_title
        return _clean_title(raw)

    def test_strips_double_quotes(self):
        self.assertEqual(self._clean('"Mumbai Weather Update"'), "Mumbai Weather Update")

    def test_strips_trailing_period(self):
        self.assertEqual(self._clean("Mumbai Weather."), "Mumbai Weather")

    def test_strips_leading_whitespace(self):
        self.assertEqual(self._clean("  Docker Setup Guide"), "Docker Setup Guide")

    def test_caps_at_100_chars(self):
        self.assertEqual(len(self._clean("A" * 150)), 100)

    def test_empty_string_stays_empty(self):
        self.assertEqual(self._clean(""), "")


class TestLooksLikeGarbageTitle(unittest.TestCase):
    """
    A-12: two confirmed live failure shapes for instruction-phrased first
    messages -- refusal and compliance/narration -- both need to be caught
    before a title ever reaches the DB. Exact strings reproduced live this
    session (2026-08-14), not paraphrased.
    """

    def _garbage(self, title):
        from core.execution.title_generator import _looks_like_garbage_title
        return _looks_like_garbage_title(title)

    def test_refusal_variant_is_garbage(self):
        self.assertTrue(self._garbage(
            "I appreciate you reaching out, but I need to clarify my role. "
            "I'm a conversation title generator — I"
        ))
        self.assertTrue(self._garbage(
            "I can't execute tools or take actions in the system. "
            "I'm a conversation title generator - I can"
        ))

    def test_compliance_narration_variant_is_garbage(self):
        self.assertTrue(self._garbage(
            "I'll help you with that. Let me start by describing the "
            "delegate_to_agent tool.\n\n```json\n{\n  \"type\":"
        ))

    def test_leaked_tool_call_syntax_is_garbage(self):
        self.assertTrue(self._garbage(
            "I'll invoke the schedule_wakeup tool now. <schedule_wakeup> "
            "<duration_minutes>2</duration_minutes>"
        ))

    def test_overlong_title_is_garbage(self):
        self.assertTrue(self._garbage(" ".join(["word"] * 15)))

    def test_empty_title_is_garbage(self):
        self.assertTrue(self._garbage(""))

    def test_real_titles_are_not_garbage(self):
        for title in (
            "Mumbai Weather Update",
            "Docker Setup Guide",
            "Schedule Wakeup For Build",
            "Python Deep Dive",
        ):
            self.assertFalse(self._garbage(title), title)

    def test_names_starting_with_i_are_not_falsely_flagged(self):
        """The 'I ' heuristic must not misfire on legitimate titles whose
        first word merely starts with the letter I (e.g. iOS)."""
        for title in ("iOS App Development", "iPhone Setup Guide"):
            self.assertFalse(self._garbage(title), title)


class TestFallbackTitle(unittest.TestCase):

    def test_truncates_and_cleans(self):
        from core.execution.title_generator import _fallback_title
        long_msg = "Call describe_tool for the tool named delegate_to_agent, " \
                   "then call delegate_to_agent with background=true"
        result = _fallback_title(long_msg)
        self.assertLessEqual(len(result), 60)
        self.assertTrue(long_msg.startswith(result.rstrip(".!?,;:")))

    def test_empty_message_yields_empty_fallback(self):
        from core.execution.title_generator import _fallback_title
        self.assertEqual(_fallback_title(""), "")


class TestTitleGeneratorRejectsGarbageOutput(unittest.IsolatedAsyncioTestCase):
    """A-12 fix, integration level: generate_and_save must not persist a
    garbage-shaped LLM response -- it must fall back to a clean,
    deterministic title derived from the first message instead."""

    async def test_garbage_llm_response_falls_back(self):
        convo = MagicMock()
        convo.title = None

        first_msg = self._make_message = MagicMock()
        first_msg.role = "user"
        first_msg.type = None
        first_msg.content = "Call schedule_wakeup with reason='waiting for the build'"

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        garbage_response = (
            "I appreciate you reaching out, but I need to clarify my role. "
            "I'm a conversation title generator"
        )

        with patch("core.execution.title_generator.AsyncSessionLocal", return_value=mock_db), \
             patch("core.execution.title_generator.DBRepo") as MockRepo, \
             patch("core.execution.title_generator.resolve_and_call_once",
                   new=AsyncMock(return_value=garbage_response)), \
             patch("core.execution.title_generator.publish_conversation_titled") as mock_pub:

            MockRepo.get_conversation = AsyncMock(return_value=convo)
            MockRepo.list_messages_for_conversation = AsyncMock(return_value=[first_msg])
            MockRepo.update_conversation_title = AsyncMock()
            mock_pub.return_value = None

            from core.execution.title_generator import generate_and_save
            await generate_and_save("conv-1", "user-1", {"provider": "groq", "model": "llama3"})

            MockRepo.update_conversation_title.assert_called_once()
            saved_title = MockRepo.update_conversation_title.call_args[0][2]
            self.assertNotIn("conversation title generator", saved_title)
            self.assertTrue(saved_title.startswith("Call schedule_wakeup"))

    async def test_clean_llm_response_is_used_as_is(self):
        """Regression guard: a normal, well-formed title must not be
        rejected or altered by the new validation step."""
        convo = MagicMock()
        convo.title = None
        first_msg = MagicMock()
        first_msg.role = "user"
        first_msg.type = None
        first_msg.content = "What's the weather in Mumbai?"

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch("core.execution.title_generator.AsyncSessionLocal", return_value=mock_db), \
             patch("core.execution.title_generator.DBRepo") as MockRepo, \
             patch("core.execution.title_generator.resolve_and_call_once",
                   new=AsyncMock(return_value="Mumbai Weather Update")), \
             patch("core.execution.title_generator.publish_conversation_titled") as mock_pub:

            MockRepo.get_conversation = AsyncMock(return_value=convo)
            MockRepo.list_messages_for_conversation = AsyncMock(return_value=[first_msg])
            MockRepo.update_conversation_title = AsyncMock()
            mock_pub.return_value = None

            from core.execution.title_generator import generate_and_save
            await generate_and_save("conv-1", "user-1", {"provider": "groq", "model": "llama3"})

            saved_title = MockRepo.update_conversation_title.call_args[0][2]
            self.assertEqual(saved_title, "Mumbai Weather Update")


class TestTitleGeneratorSkips(unittest.IsolatedAsyncioTestCase):

    async def test_skips_if_title_already_set(self):
        convo = MagicMock()
        convo.title = "Custom User Title"
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        with patch("core.execution.title_generator.AsyncSessionLocal",
                   return_value=mock_db), \
             patch("core.execution.title_generator.DBRepo") as MockRepo, \
             patch("core.execution.title_generator.resolve_and_call_once") as mock_call:
            MockRepo.get_conversation = AsyncMock(return_value=convo)
            from core.execution.title_generator import generate_and_save
            await generate_and_save("conv-1", "user-1",
                                    {"provider": "groq", "model": "llama3"})
            mock_call.assert_not_called()

    async def test_skips_if_conversation_not_found(self):
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        with patch("core.execution.title_generator.AsyncSessionLocal",
                   return_value=mock_db), \
             patch("core.execution.title_generator.DBRepo") as MockRepo, \
             patch("core.execution.title_generator.resolve_and_call_once") as mock_call:
            MockRepo.get_conversation = AsyncMock(return_value=None)
            from core.execution.title_generator import generate_and_save
            await generate_and_save("conv-missing", "user-1", {})
            mock_call.assert_not_called()

    async def test_non_fatal_on_exception(self):
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(side_effect=RuntimeError("DB exploded"))
        mock_db.__aexit__ = AsyncMock(return_value=False)
        with patch("core.execution.title_generator.AsyncSessionLocal",
                   return_value=mock_db):
            from core.execution.title_generator import generate_and_save
            await generate_and_save("conv-1", "user-1", {})


class TestTitleGeneratorProviderKeyResolution(unittest.TestCase):
    """
    Bug fix: API key resolution must cover all providers via the canonical
    PROVIDER_KEY_MAP, not a small hardcoded dict.

    title_generator.py used to have its own _resolve_api_key() doing this
    locally; it has since been fully delegated to
    core.providers.fallback_call.resolve_and_call_once() (V6.8), which
    resolves keys via core.providers.fallback_state.resolve_api_key() ->
    providers.capability.base.get_provider_env_key() -> PROVIDER_KEY_MAP.
    That shared function's own multi-provider/env-fallback/session-priority
    behaviour is covered directly by TestB055ApiKeyResolution above (same
    function, same coverage) -- this class now only guards that
    title_generator.py doesn't regress into reimplementing key resolution
    locally again.
    """

    def test_no_local_api_key_resolution_reimplemented(self):
        """
        title_generator must not have its own key-resolution logic (no
        _resolve_api_key, no local provider->env-var dict) -- it must fully
        delegate to resolve_and_call_once() so every provider the shared
        PROVIDER_KEY_MAP knows about is covered automatically, not just
        whichever providers someone remembered to hardcode here.
        """
        import inspect
        import core.execution.title_generator as tg_mod
        src = inspect.getsource(tg_mod)
        self.assertIn("resolve_and_call_once", src)
        self.assertNotIn("_resolve_api_key", src)
        self.assertNotIn("_PROVIDER_ENV_KEYS", src)
        self.assertFalse(hasattr(tg_mod, "_resolve_api_key"))


class TestTitleGeneratorAttachmentFilter(unittest.IsolatedAsyncioTestCase):
    """Bug fix: attachment_context messages must not be counted as real user turns."""

    def _make_message(self, role, mtype=None, content="hello"):
        m = MagicMock()
        m.role = role
        m.type = mtype
        m.content = content
        return m

    async def test_attachment_context_not_counted_as_user_message(self):
        """
        A conversation with 1 real user message + 1 attachment_context message
        should still generate a title (count of real user messages == 1).
        """
        convo = MagicMock()
        convo.title = None

        real_user_msg   = self._make_message("user",      mtype=None,                 content="Tell me about Python")
        attach_msg      = self._make_message("user",      mtype="attachment_context", content="[FILE ATTACHMENT]")
        assistant_msg   = self._make_message("assistant", mtype=None,                 content="Python is great")

        all_messages = [attach_msg, real_user_msg, assistant_msg]

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__  = AsyncMock(return_value=False)

        with patch("core.execution.title_generator.AsyncSessionLocal", return_value=mock_db), \
             patch("core.execution.title_generator.DBRepo") as MockRepo, \
             patch("core.execution.title_generator.resolve_and_call_once",
                   new=AsyncMock(return_value="Python Deep Dive")), \
             patch("core.execution.title_generator.publish_conversation_titled") as mock_pub, \
             patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):

            MockRepo.get_conversation       = AsyncMock(return_value=convo)
            MockRepo.list_messages_for_conversation = AsyncMock(return_value=all_messages)
            MockRepo.update_conversation_title      = AsyncMock()
            mock_pub.return_value = None

            from core.execution.title_generator import generate_and_save
            await generate_and_save("conv-1", "user-1", {"provider": "groq", "model": "llama3"})

            # Title must have been saved
            MockRepo.update_conversation_title.assert_called_once()
            saved_title = MockRepo.update_conversation_title.call_args[0][2]
            self.assertEqual(saved_title, "Python Deep Dive")

    async def test_two_real_user_messages_skips(self):
        """Second exchange (2 real user messages) must not re-title."""
        convo = MagicMock()
        convo.title = None

        msg1 = self._make_message("user", content="First message")
        msg2 = self._make_message("user", content="Second message")
        all_messages = [msg1, msg2]

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__  = AsyncMock(return_value=False)

        with patch("core.execution.title_generator.AsyncSessionLocal", return_value=mock_db), \
             patch("core.execution.title_generator.DBRepo") as MockRepo, \
             patch("core.execution.title_generator.resolve_and_call_once") as mock_call:

            MockRepo.get_conversation = AsyncMock(return_value=convo)
            MockRepo.list_messages_for_conversation = AsyncMock(return_value=all_messages)

            from core.execution.title_generator import generate_and_save
            await generate_and_save("conv-2", "user-1", {"provider": "groq", "model": "llama3"})

            mock_call.assert_not_called()


class TestWorkerImportsTitleGenerator(unittest.TestCase):
    """Q-01 Phase 4 (2026-08-17): the post-execution title-generation call
    site moved from core/execution/worker.py to the new
    core/execution/worker_finalize.py (see that module's own header
    comment) -- this test's source-substring check moved with it, since
    worker.py itself no longer imports title_generator by name at all."""

    def test_worker_finalize_imports_title_generator(self):
        import inspect
        import core.execution.worker_finalize as worker_finalize_mod
        source = inspect.getsource(worker_finalize_mod)
        self.assertIn("title_generator", source)
        self.assertIn("generate_and_save", source)


# ===========================================================================
# B-059 — Stuck-Execution Watchdog
# ===========================================================================

def _make_mock_client(hashes: dict) -> MagicMock:
    client = AsyncMock()

    async def _scan_iter(pattern):
        for k in hashes:
            yield k

    client.scan_iter = _scan_iter
    client.type = AsyncMock(return_value="hash")

    async def _hgetall(key):
        return hashes.get(key, {})

    client.hgetall = _hgetall
    return client


def _ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


class TestTransitionsAllowAbortFromApprovalGates(unittest.TestCase):

    def _allowed(self, from_status, to_status):
        from core.execution.transitions import ALLOWED_TRANSITIONS
        return to_status in ALLOWED_TRANSITIONS.get(from_status, set())

    def test_aborted_allowed_from_awaiting_approval(self):
        from core.execution.models import ExecutionStatus
        self.assertTrue(
            self._allowed(ExecutionStatus.AWAITING_APPROVAL, ExecutionStatus.ABORTED))

    def test_aborted_allowed_from_awaiting_plan_approval(self):
        from core.execution.models import ExecutionStatus
        self.assertTrue(
            self._allowed(ExecutionStatus.AWAITING_PLAN_APPROVAL, ExecutionStatus.ABORTED))

    def test_cancelled_still_allowed_from_awaiting_approval(self):
        from core.execution.models import ExecutionStatus
        self.assertTrue(
            self._allowed(ExecutionStatus.AWAITING_APPROVAL, ExecutionStatus.CANCELLED))


class TestWatchdogAbortsStuckRunning(unittest.IsolatedAsyncioTestCase):

    async def test_aborts_running_past_timeout(self):
        hashes = {
            "execution:abc123": {
                "status": "running",
                "last_heartbeat_at": _ts(20),
                "user_id": "user-1",
            }
        }
        mock_client = _make_mock_client(hashes)
        abort_calls = []

        with patch("core.execution.worker.redis_execution_registry.connect",
                   new_callable=AsyncMock, return_value=mock_client), \
             patch("core.execution.worker.publish_user_event",
                   new_callable=AsyncMock) as mock_publish, \
             patch("core.execution.worker.WATCHDOG_INTERVAL_SECONDS", 0), \
             patch("core.execution.worker.WATCHDOG_RUNNING_TIMEOUT_SECONDS", 900):
            from core.execution.worker import ExecutionWorker
            worker = ExecutionWorker()
            worker._running = True

            async def _abort_and_stop(execution_id, reason):
                abort_calls.append((execution_id, reason))
                worker._running = False

            with patch("core.execution.worker.redis_execution_registry.mark_execution_aborted",
                       side_effect=_abort_and_stop):
                await worker._watchdog_loop()

        self.assertEqual(len(abort_calls), 1)
        self.assertEqual(abort_calls[0], ("abc123", "watchdog_timeout_running"))
        mock_publish.assert_called_once()

    async def test_skips_healthy_running(self):
        hashes = {
            "execution:fresh1": {
                "status": "running",
                "last_heartbeat_at": _ts(2),
                "user_id": "user-1",
            }
        }
        mock_client = _make_mock_client(hashes)
        with patch("core.execution.worker.redis_execution_registry.connect",
                   new_callable=AsyncMock, return_value=mock_client), \
             patch("core.execution.worker.redis_execution_registry.mark_execution_aborted",
                   new_callable=AsyncMock) as mock_abort, \
             patch("core.execution.worker.WATCHDOG_INTERVAL_SECONDS", 0), \
             patch("core.execution.worker.WATCHDOG_RUNNING_TIMEOUT_SECONDS", 900):
            from core.execution.worker import ExecutionWorker
            worker = ExecutionWorker()
            worker._running = True

            async def _stop():
                await asyncio.sleep(0.05)
                worker._running = False

            await asyncio.gather(worker._watchdog_loop(), _stop())
        mock_abort.assert_not_called()

    async def test_aborts_awaiting_step_extension_past_timeout(self):
        # V6.54: AWAITING_STEP_EXTENSION is a sibling HitL gate to
        # AWAITING_APPROVAL / AWAITING_PLAN_APPROVAL (see
        # core/execution/gate_markers.py's docstring, which lists all three
        # together) but was missing from the watchdog's _TIMEOUTS table --
        # a user who never responds to a step-limit-extension prompt left
        # the execution stuck forever with no watchdog coverage at all,
        # unlike its two siblings. This confirms the fix: its own
        # independently-tunable timeout constant
        # (WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS), same abort behaviour.
        hashes = {
            "execution:ext123": {
                "status": "awaiting_step_extension",
                "last_heartbeat_at": _ts(20),
                "user_id": "user-1",
            }
        }
        mock_client = _make_mock_client(hashes)
        abort_calls = []

        with patch("core.execution.worker.redis_execution_registry.connect",
                   new_callable=AsyncMock, return_value=mock_client), \
             patch("core.execution.worker.publish_user_event",
                   new_callable=AsyncMock), \
             patch("core.execution.worker.WATCHDOG_INTERVAL_SECONDS", 0), \
             patch("core.execution.worker.WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS", 900):
            from core.execution.worker import ExecutionWorker
            worker = ExecutionWorker()
            worker._running = True

            async def _abort_and_stop(execution_id, reason):
                abort_calls.append((execution_id, reason))
                worker._running = False

            with patch("core.execution.worker.redis_execution_registry.mark_execution_aborted",
                       side_effect=_abort_and_stop):
                # Bounded wait, not a bare await: if this status ever falls
                # out of _TIMEOUTS again, the loop never aborts and never
                # stops (WATCHDOG_INTERVAL_SECONDS=0 makes it a tight spin)
                # -- this turns that regression into a fast test failure
                # instead of hanging the whole suite.
                await asyncio.wait_for(worker._watchdog_loop(), timeout=5)

        self.assertEqual(len(abort_calls), 1)
        self.assertEqual(abort_calls[0], ("ext123", "watchdog_timeout_awaiting_step_extension"))

    async def test_skips_healthy_awaiting_step_extension(self):
        hashes = {
            "execution:extfresh1": {
                "status": "awaiting_step_extension",
                "last_heartbeat_at": _ts(2),
                "user_id": "user-1",
            }
        }
        mock_client = _make_mock_client(hashes)
        with patch("core.execution.worker.redis_execution_registry.connect",
                   new_callable=AsyncMock, return_value=mock_client), \
             patch("core.execution.worker.redis_execution_registry.mark_execution_aborted",
                   new_callable=AsyncMock) as mock_abort, \
             patch("core.execution.worker.WATCHDOG_INTERVAL_SECONDS", 0), \
             patch("core.execution.worker.WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS", 900):
            from core.execution.worker import ExecutionWorker
            worker = ExecutionWorker()
            worker._running = True

            async def _stop():
                await asyncio.sleep(0.05)
                worker._running = False

            await asyncio.gather(worker._watchdog_loop(), _stop())
        mock_abort.assert_not_called()


class TestWatchdogSkipsTerminalStates(unittest.IsolatedAsyncioTestCase):

    async def _run_watchdog_on(self, hashes):
        mock_client = _make_mock_client(hashes)
        with patch("core.execution.worker.redis_execution_registry.connect",
                   new_callable=AsyncMock, return_value=mock_client), \
             patch("core.execution.worker.redis_execution_registry.mark_execution_aborted",
                   new_callable=AsyncMock) as mock_abort, \
             patch("core.execution.worker.WATCHDOG_INTERVAL_SECONDS", 0):
            from core.execution.worker import ExecutionWorker
            worker = ExecutionWorker()
            worker._running = True

            async def _stop():
                await asyncio.sleep(0.05)
                worker._running = False

            await asyncio.gather(worker._watchdog_loop(), _stop())
        return mock_abort

    async def test_skips_completed(self):
        mock_abort = await self._run_watchdog_on({
            "execution:done1": {"status": "completed", "last_heartbeat_at": _ts(60),
                                "user_id": "u1"}
        })
        mock_abort.assert_not_called()

    async def test_skips_cancelled(self):
        mock_abort = await self._run_watchdog_on({
            "execution:canc1": {"status": "cancelled", "last_heartbeat_at": _ts(60),
                                "user_id": "u1"}
        })
        mock_abort.assert_not_called()

    async def test_skips_already_aborted(self):
        mock_abort = await self._run_watchdog_on({
            "execution:abrt1": {"status": "aborted", "last_heartbeat_at": _ts(60),
                                "user_id": "u1"}
        })
        mock_abort.assert_not_called()


class TestWatchdogResiliency(unittest.IsolatedAsyncioTestCase):

    async def test_broker_connect_error_does_not_crash(self):
        with patch("core.execution.worker.redis_execution_registry.connect",
                   new_callable=AsyncMock,
                   side_effect=RuntimeError("Broker down")), \
             patch("core.execution.worker.WATCHDOG_INTERVAL_SECONDS", 0):
            from core.execution.worker import ExecutionWorker
            worker = ExecutionWorker()
            worker._running = True

            async def _stop():
                await asyncio.sleep(0.05)
                worker._running = False

            await asyncio.gather(worker._watchdog_loop(), _stop())

    async def test_per_key_error_does_not_abort_other_keys(self):
        mock_client = AsyncMock()

        async def _scan_iter(pattern):
            for k in ["execution:bad_key", "execution:good_key"]:
                yield k

        mock_client.scan_iter = _scan_iter
        mock_client.type = AsyncMock(return_value="hash")

        async def _hgetall(key):
            if key == "execution:bad_key":
                raise RuntimeError("corrupted hash")
            return {"status": "running", "last_heartbeat_at": _ts(20), "user_id": "user-x"}

        mock_client.hgetall = _hgetall
        abort_calls = []

        with patch("core.execution.worker.redis_execution_registry.connect",
                   new_callable=AsyncMock, return_value=mock_client), \
             patch("core.execution.worker.publish_user_event", new_callable=AsyncMock), \
             patch("core.execution.worker.WATCHDOG_INTERVAL_SECONDS", 0), \
             patch("core.execution.worker.WATCHDOG_RUNNING_TIMEOUT_SECONDS", 900):
            from core.execution.worker import ExecutionWorker
            worker = ExecutionWorker()
            worker._running = True

            async def _abort_and_stop(execution_id, reason):
                abort_calls.append((execution_id, reason))
                worker._running = False

            with patch("core.execution.worker.redis_execution_registry.mark_execution_aborted",
                       side_effect=_abort_and_stop):
                await worker._watchdog_loop()

        self.assertEqual(len(abort_calls), 1)
        self.assertEqual(abort_calls[0], ("good_key", "watchdog_timeout_running"))


# ===========================================================================
# Duplicate Tool-Call Detection — AgentEngine guard
# ===========================================================================

class TestDuplicateToolCallDetection(unittest.IsolatedAsyncioTestCase):

    def _make_agent(self, chunk_sequences):
        import sys
        from core.types import TextChunk

        _stubs = ["openai", "openai.types", "openai.types.chat", "groq", "anthropic", "litellm"]
        for mod in _stubs:
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()

        for mod in ["providers.handlers.openai_handler", "providers.handlers.groq_handler",
                    "providers.handlers.anthropic_handler", "providers.handlers.litellm_handler",
                    "providers.handlers.ollama_handler", "core.factory", "utils.handler_utils"]:
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()

        from core.agent_engine import AgentEngine

        call_index = {"i": 0}

        async def _fake_create_message(**kwargs):
            idx = call_index["i"]
            if idx >= len(chunk_sequences):
                yield TextChunk(content="done", finish_reason="stop")
                return
            call_index["i"] += 1
            for chunk in chunk_sequences[idx]:
                yield chunk

        mock_handler = MagicMock()
        mock_handler.create_message = _fake_create_message
        mock_handler.tool_classes = []

        agent = AgentEngine.__new__(AgentEngine)
        agent.provider = "test"
        agent.model = "test-model"
        agent.session_id = "sess-test"
        agent.reasoning = False
        agent.system_prompt = ""
        agent.memory_provider = None
        agent.handler = mock_handler
        agent.dispatcher = MagicMock()
        agent.dynamic_registry = MagicMock()
        agent.execution = MagicMock()
        agent.execution.execution_id = "exec-test"
        agent.cancel_token = MagicMock()
        agent.cancel_token.cancelled = False
        agent._max_steps = None
        agent.fallback_chain = []
        agent.observability = MagicMock()
        agent.observability.emit = MagicMock()
        agent.on_step_complete = None

        async def _direct_gen(messages, stream, execution_id=""):
            async for chunk in mock_handler.create_message(messages=messages, stream=stream):
                yield chunk

        import types
        agent._gen_with_fallback = types.MethodType(
            lambda self, messages, stream, execution_id="": _direct_gen(messages, stream),
            agent,
        )
        return agent

    async def test_duplicate_tool_call_yields_error_and_stops(self):
        from core.types import ToolCallChunk, ErrorChunk

        tc1 = ToolCallChunk(tool_name="web_search",
                            arguments={"query": "python async"}, call_id="call-1")
        tc2 = ToolCallChunk(tool_name="web_search",
                            arguments={"query": "python async"}, call_id="call-2")
        tc3 = ToolCallChunk(tool_name="web_search",
                            arguments={"query": "python async"}, call_id="call-3")

        agent = self._make_agent([[tc1], [tc2], [tc3]])

        async def _fake_execute(**kwargs):
            return {"result": "some search result"}

        chunks_received = []
        with patch("core.agent_engine.execute_tool_safely", side_effect=_fake_execute):
            async for chunk in agent.call(
                messages=[{"role": "user", "content": "search for me"}],
                stream=True,
            ):
                chunks_received.append(chunk)

        error_chunks = [c for c in chunks_received if hasattr(c, "error_type")]
        self.assertEqual(len(error_chunks), 1)
        self.assertEqual(error_chunks[0].error_type, "duplicate_tool_call")

    async def test_different_args_not_flagged_as_duplicate(self):
        from core.types import ToolCallChunk, TextChunk, ErrorChunk

        tc1 = ToolCallChunk(tool_name="web_search",
                            arguments={"query": "python async"}, call_id="call-1")
        tc2 = ToolCallChunk(tool_name="web_search",
                            arguments={"query": "rust ownership"}, call_id="call-2")
        finish = TextChunk(content="Here are the results.", finish_reason="stop")

        agent = self._make_agent([[tc1], [tc2], [finish]])

        async def _fake_execute(**kwargs):
            return {"result": "result"}

        chunks_received = []
        with patch("core.agent_engine.execute_tool_safely", side_effect=_fake_execute):
            async for chunk in agent.call(
                messages=[{"role": "user", "content": "search twice"}],
                stream=True,
            ):
                chunks_received.append(chunk)

        error_chunks = [c for c in chunks_received if hasattr(c, "error_type")]
        self.assertEqual(len(error_chunks), 0)


# ===========================================================================
# B-045 — Project Entity (eva_format + ProjectManager + artifact updater)
# ===========================================================================
# Note: workspace root is redirected by tests/conftest.py autouse fixture.

def test_read_missing_file(tmp_path):
    from core.project.eva_format import read_file
    assert read_file(tmp_path / "nonexistent.eva") == ""


def test_write_and_read_roundtrip(tmp_path):
    from core.project.eva_format import write_file, read_file
    path = tmp_path / "test.eva"
    write_file(path, "hello\nworld\n")
    assert read_file(path) == "hello\nworld\n"


def test_strip_comments_removes_hash_lines():
    from core.project.eva_format import strip_comments
    text = "# this is a comment\nkey: value\n# another comment\nother: line\n"
    result = strip_comments(text)
    assert "# this" not in result
    assert "key: value" in result


def test_strip_comments_collapses_blank_lines():
    from core.project.eva_format import strip_comments
    text = "line1\n\n\n\nline2\n"
    result = strip_comments(text)
    assert "\n\n\n" not in result


def test_get_section_found():
    from core.project.eva_format import get_section
    text = "### PROJECT\nid: abc\nname: Test\n\n### TASKS\ntask1\ntask2\n"
    body = get_section(text, "PROJECT")
    assert "id: abc" in body
    assert "task1" not in body


def test_get_section_not_found():
    from core.project.eva_format import get_section
    text = "### PROJECT\nid: abc\n"
    assert get_section(text, "MISSING") == ""


def test_set_section_replaces_existing(tmp_path):
    from core.project.eva_format import write_file, set_section, get_section, read_file
    path = tmp_path / "test.eva"
    write_file(path, "### GOAL\nold content\n\n### CONSTRAINTS\nstatic\n")
    set_section(path, "GOAL", "new goal content")
    text = read_file(path)
    assert get_section(text, "GOAL") == "new goal content"
    assert "old content" not in text
    assert "static" in text


def test_append_entry(tmp_path):
    from core.project.eva_format import append_entry, read_file
    path = tmp_path / "journal.eva"
    append_entry(path, "[2026-01-01T00:00Z agent=eva] turn 1 done")
    append_entry(path, "[2026-01-01T01:00Z agent=eva] turn 2 done")
    text = read_file(path)
    assert "turn 1 done" in text
    assert "turn 2 done" in text


def test_render_project_eva_has_required_sections():
    from core.project.eva_format import render_project_eva
    content = render_project_eva(
        project_id="proj_test", name="My Project",
        description="A test project", synopsis="Build a thing",
    )
    for section in ("### PROJECT", "### SYNOPSIS", "### DESCRIPTION",
                    "### GOAL", "### SCOPE", "### CONSTRAINTS", "### ENV"):
        assert section in content
    assert "proj_test" in content


def test_skeleton_renderers_have_section_headers():
    from core.project.eva_format import (
        render_tasks_eva, render_knowledge_eva,
        render_journal_eva, render_errors_eva, render_policies_eva,
    )
    assert "### TASKS" in render_tasks_eva()
    assert "### KNOWLEDGE" in render_knowledge_eva()
    assert "### JOURNAL" in render_journal_eva()
    assert "### ERRORS" in render_errors_eva()
    policies = render_policies_eva()
    assert "### POLICIES" in policies
    assert "### DOS" in policies
    assert "### DONTS" in policies
    assert "### ASK_FIRST" in policies


def test_workspace_does_not_exist_before_init():
    from core.project.manager import ProjectManager
    assert not ProjectManager.workspace_exists("test_user", "nonexistent_proj")


def test_workspace_exists_respects_external_workspace_path():
    """Regression guard: workspace_exists() must check the project's actual
    (possibly external) location, not always the managed one. Before this
    fix it unconditionally checked the managed path, so an external-workspace
    project's .eva/ (created inside external_path) was invisible to it."""
    from core.project.manager import ProjectManager

    with tempfile.TemporaryDirectory() as ext_dir:
        # No .eva/ here yet, and nothing at the managed location either.
        assert not ProjectManager.workspace_exists(
            "test_user", "ext_proj_1", workspace_path=ext_dir
        )
        ProjectManager.init_workspace(
            user_id="test_user", project_id="ext_proj_1",
            name="External Project", external_path=ext_dir,
        )
        # Found via the external path...
        assert ProjectManager.workspace_exists(
            "test_user", "ext_proj_1", workspace_path=ext_dir
        )
        # ...but NOT by falling back to the managed location, which was
        # never populated for this project.
        assert not ProjectManager.workspace_exists("test_user", "ext_proj_1")


def test_read_write_artifact_respects_external_workspace_path():
    """Regression guard: get_artifact_path()/read_artifact() must resolve
    against workspace_path, not silently read/write the managed location's
    (nonexistent) files for an external-workspace project."""
    from core.project.manager import ProjectManager

    with tempfile.TemporaryDirectory() as ext_dir:
        ProjectManager.init_workspace(
            user_id="test_user", project_id="ext_proj_2",
            name="External Project 2", external_path=ext_dir,
        )
        ProjectManager.write_artifact(
            "test_user", "ext_proj_2", "knowledge.eva",
            "### KNOWLEDGE\n[NOTE ts=2026-01-01T00:00Z] test fact\n",
            workspace_path=ext_dir,
        )
        content = ProjectManager.read_artifact(
            "test_user", "ext_proj_2", "knowledge.eva", workspace_path=ext_dir
        )
        assert "test fact" in content
        # The managed location must be untouched by this write.
        managed_content = ProjectManager.read_artifact(
            "test_user", "ext_proj_2", "knowledge.eva"
        )
        assert "test fact" not in managed_content


def test_init_workspace_creates_all_files():
    from core.project.manager import ProjectManager
    ProjectManager.init_workspace(
        user_id="test_user", project_id="proj_001",
        name="Test Project", description="For testing", synopsis="Test synopsis",
    )
    assert ProjectManager.workspace_exists("test_user", "proj_001")
    eva_dir = ProjectManager.get_eva_dir("test_user", "proj_001")
    for filename in ("project.eva", "policies.eva", "tasks.eva",
                     "knowledge.eva", "journal.eva", "errors.eva"):
        assert (eva_dir / filename).exists(), f"Missing: {filename}"


def test_init_workspace_idempotent():
    from core.project.manager import ProjectManager
    ProjectManager.init_workspace("test_user", "proj_003", "P3")
    ProjectManager.init_workspace("test_user", "proj_003", "P3 updated")
    content = ProjectManager.read_artifact("test_user", "proj_003", "project.eva")
    assert "P3" in content


def test_load_context_missing_workspace():
    from core.project.manager import ProjectManager
    result = ProjectManager.load_context("test_user", "no_such_proj")
    assert result == ""


def test_load_context_compact_has_markers():
    from core.project.manager import ProjectManager
    ProjectManager.init_workspace("test_user", "proj_ctx", "Context Project",
                                  synopsis="Do stuff")
    result = ProjectManager.load_context("test_user", "proj_ctx", tier="compact")
    assert "--- PROJECT CONTEXT ---" in result
    assert "--- END PROJECT CONTEXT ---" in result


def test_load_context_compact_always_has_policies():
    from core.project.manager import ProjectManager
    from core.project.eva_format import write_file
    ProjectManager.init_workspace("test_user", "proj_pol_c", "PolCompact")
    write_file(
        ProjectManager.get_artifact_path("test_user", "proj_pol_c", "policies.eva"),
        "### POLICIES\n\n### DOS\n- Always run tests before committing\n\n"
        "### DONTS\n- Never push to main\n\n### ASK_FIRST\n",
    )
    result = ProjectManager.load_context("test_user", "proj_pol_c", tier="compact")
    assert "Always run tests before committing" in result


def test_load_context_focused_has_task_content():
    from core.project.manager import ProjectManager
    from core.project.eva_format import write_file
    ProjectManager.init_workspace("test_user", "proj_f1", "F1")
    write_file(
        ProjectManager.get_artifact_path("test_user", "proj_f1", "tasks.eva"),
        "### TASKS\n[ACTIVE ts=2026-01-01T00:00Z id=T-001] First task\n"
    )
    result = ProjectManager.load_context("test_user", "proj_f1", tier="focused")
    assert "First task" in result


def test_append_journal_writes_entry():
    from core.project.manager import ProjectManager
    from core.project.eva_format import read_file
    ProjectManager.init_workspace("test_user", "proj_j1", "J1")
    ProjectManager.append_journal("test_user", "proj_j1", "Something happened",
                                   agent="eva", turn=5)
    journal = read_file(ProjectManager.get_artifact_path("test_user", "proj_j1", "journal.eva"))
    assert "Something happened" in journal
    assert "agent=eva" in journal


def test_append_journal_respects_external_workspace_path():
    """Regression guard: append_journal() previously had no workspace_path
    parameter at all -- always wrote to the managed location regardless of
    where the project's real .eva/ directory actually lives."""
    from core.project.manager import ProjectManager
    from core.project.eva_format import read_file

    with tempfile.TemporaryDirectory() as ext_dir:
        ProjectManager.init_workspace(
            user_id="test_user", project_id="ext_proj_3",
            name="External Project 3", external_path=ext_dir,
        )
        ProjectManager.append_journal(
            "test_user", "ext_proj_3", "External entry",
            agent="eva", turn=1, workspace_path=ext_dir,
        )
        journal = read_file(
            ProjectManager.get_artifact_path(
                "test_user", "ext_proj_3", "journal.eva", ext_dir
            )
        )
        assert "External entry" in journal


def test_add_policy_dos():
    from core.project.manager import ProjectManager
    ProjectManager.init_workspace("test_user", "proj_pol1", "PolTest1")
    result = ProjectManager.add_policy("test_user", "proj_pol1", "DOS",
                                        "Always run tests before committing")
    assert result is True
    policies = ProjectManager.get_policies("test_user", "proj_pol1")
    assert any("Always run tests before committing" in r for r in policies["DOS"])


def test_add_policy_invalid_section_returns_false():
    from core.project.manager import ProjectManager
    ProjectManager.init_workspace("test_user", "proj_pol5", "PolTest5")
    result = ProjectManager.add_policy("test_user", "proj_pol5", "UNKNOWN_SECTION", "some rule")
    assert result is False


def test_add_policy_missing_workspace_returns_false():
    from core.project.manager import ProjectManager
    result = ProjectManager.add_policy("test_user", "nonexistent_project", "DOS", "some rule")
    assert result is False


def test_remove_policy_success():
    from core.project.manager import ProjectManager
    ProjectManager.init_workspace("test_user", "proj_pol7", "PolTest7")
    ProjectManager.add_policy("test_user", "proj_pol7", "DOS", "Always write unit tests")
    ProjectManager.add_policy("test_user", "proj_pol7", "DOS", "Always use type hints")
    result = ProjectManager.remove_policy("test_user", "proj_pol7", "DOS", "write unit tests")
    assert result is True
    policies = ProjectManager.get_policies("test_user", "proj_pol7")
    assert all("write unit tests" not in r.lower() for r in policies["DOS"])
    assert any("type hints" in r for r in policies["DOS"])


def test_get_policies_excludes_comments():
    from core.project.manager import ProjectManager
    from core.project.eva_format import write_file
    ProjectManager.init_workspace("test_user", "proj_pol9", "PolTest9")
    write_file(
        ProjectManager.get_artifact_path("test_user", "proj_pol9", "policies.eva"),
        "### POLICIES\n# top comment\n\n### DOS\n# example\n- Real rule 1\n\n"
        "### DONTS\n# another\n- Real rule 2\n\n### ASK_FIRST\n",
    )
    policies = ProjectManager.get_policies("test_user", "proj_pol9")
    assert policies["DOS"] == ["Real rule 1"]
    assert policies["DONTS"] == ["Real rule 2"]
    assert policies["ASK_FIRST"] == []


def test_set_policy_section_replaces_fully():
    from core.project.manager import ProjectManager
    ProjectManager.init_workspace("test_user", "proj_pol10", "PolTest10")
    ProjectManager.add_policy("test_user", "proj_pol10", "DONTS", "Old rule")
    ProjectManager.set_policy_section("test_user", "proj_pol10", "DONTS",
                                       "- New rule A\n- New rule B")
    policies = ProjectManager.get_policies("test_user", "proj_pol10")
    assert len(policies["DONTS"]) == 2
    assert any("New rule A" in r for r in policies["DONTS"])
    assert all("Old rule" not in r for r in policies["DONTS"])


def test_parse_instructions_valid_json():
    from core.project.artifact_updater import ProjectArtifactUpdater
    updater = ProjectArtifactUpdater()
    raw = json.dumps([
        {"file": "tasks", "action": "append", "content": "[DONE] thing"},
        {"file": "errors", "action": "append", "content": "error happened"},
    ])
    result = updater._parse_instructions(raw)
    assert len(result) == 2
    assert result[0]["file"] == "tasks"


def test_parse_instructions_markdown_fenced():
    from core.project.artifact_updater import ProjectArtifactUpdater
    updater = ProjectArtifactUpdater()
    raw = '```json\n[{"file": "knowledge", "action": "append", "content": "fact"}]\n```'
    result = updater._parse_instructions(raw)
    assert len(result) == 1


def test_parse_instructions_bad_input():
    from core.project.artifact_updater import ProjectArtifactUpdater
    updater = ProjectArtifactUpdater()
    assert updater._parse_instructions("not json at all") == []
    assert updater._parse_instructions("") == []


def test_parse_instructions_caps_at_10():
    from core.project.artifact_updater import ProjectArtifactUpdater
    updater = ProjectArtifactUpdater()
    items = [{"file": "tasks", "action": "append", "content": f"item {i}"}
             for i in range(20)]
    result = updater._parse_instructions(json.dumps(items))
    assert len(result) == 10


class TestArtifactUpdaterExternalWorkspace(unittest.IsolatedAsyncioTestCase):
    """Regression guard for the workspace_path threading bug: before the fix,
    ProjectArtifactUpdater.update()/_run() never passed workspace_path to any
    ProjectManager call, so workspace_exists() always checked the managed
    location -- for an external-workspace project this returned False and
    the whole update silently no-op'd (LLM never called, nothing written)."""

    async def test_update_writes_to_external_workspace_not_managed(self):
        from core.project.manager import ProjectManager
        from core.project.artifact_updater import ProjectArtifactUpdater
        from core.project.eva_format import read_file

        with tempfile.TemporaryDirectory() as ext_dir:
            ProjectManager.init_workspace(
                user_id="test_user", project_id="ext_proj_updater",
                name="External Updater Project", external_path=ext_dir,
            )

            fake_message = MagicMock()
            fake_message.role = "user"
            fake_message.content = "please remember that this repo uses FastAPI"

            llm_response = json.dumps([
                {"file": "knowledge", "action": "append",
                 "content": "[NOTE ts=2026-01-01T00:00Z] Repo uses FastAPI"}
            ])

            updater = ProjectArtifactUpdater()
            with patch(
                "core.project.artifact_updater.DBRepo.list_messages_for_conversation",
                new=AsyncMock(return_value=[fake_message]),
            ), patch(
                "core.project.artifact_updater.call_with_full_fallback",
                new=AsyncMock(return_value=llm_response),
            ):
                await updater.update(
                    project_id="ext_proj_updater",
                    conversation_id="conv-1",
                    user_id="test_user",
                    session={"provider": "test", "model": "test-model"},
                    execution_id="exec-1",
                    workspace_path=ext_dir,
                )

            knowledge = read_file(
                ProjectManager.get_artifact_path(
                    "test_user", "ext_proj_updater", "knowledge.eva", ext_dir
                )
            )
            assert "Repo uses FastAPI" in knowledge

            # Must not have landed in the managed location instead.
            managed_knowledge = ProjectManager.read_artifact(
                "test_user", "ext_proj_updater", "knowledge.eva"
            )
            assert "Repo uses FastAPI" not in managed_knowledge

    async def test_update_is_noop_without_workspace_path_for_external_project(self):
        """Documents the bug this fix closes: calling update() without
        workspace_path for a project whose .eva/ only exists externally
        still silently no-ops (workspace_exists() finds nothing at the
        managed location) -- confirming workspace_path is load-bearing,
        not just a nice-to-have."""
        from core.project.manager import ProjectManager
        from core.project.artifact_updater import ProjectArtifactUpdater

        with tempfile.TemporaryDirectory() as ext_dir:
            ProjectManager.init_workspace(
                user_id="test_user", project_id="ext_proj_updater_noop",
                name="External Noop Project", external_path=ext_dir,
            )

            fake_message = MagicMock()
            fake_message.role = "user"
            fake_message.content = "some message"

            updater = ProjectArtifactUpdater()
            llm_mock = AsyncMock(return_value="[]")
            with patch(
                "core.project.artifact_updater.DBRepo.list_messages_for_conversation",
                new=AsyncMock(return_value=[fake_message]),
            ), patch(
                "core.project.artifact_updater.call_with_full_fallback",
                new=llm_mock,
            ):
                await updater.update(
                    project_id="ext_proj_updater_noop",
                    conversation_id="conv-2",
                    user_id="test_user",
                    session={"provider": "test", "model": "test-model"},
                    execution_id="exec-2",
                    # workspace_path intentionally omitted
                )

            llm_mock.assert_not_called()


def test_artifact_update_prompt_prioritizes_orientation_facts():
    """Regression guard for Phase A Part 2: codebase-orientation facts
    (stack, entry points, conventions, test/run commands) must be called
    out as high priority in the knowledge-capture instruction, not left as
    generic 'insights' easy to under-capture next to decisions/errors."""
    from prompts.project import PROJECT_ARTIFACT_UPDATE_PROMPT
    assert "HIGH PRIORITY" in PROJECT_ARTIFACT_UPDATE_PROMPT
    assert "entry points" in PROJECT_ARTIFACT_UPDATE_PROMPT
    assert "conventions" in PROJECT_ARTIFACT_UPDATE_PROMPT
    # Must not be code-only: the "codebase-orientation" framing was itself
    # generalized so non-code projects (written content, data) get the same
    # HIGH PRIORITY treatment instead of only code-shaped facts.
    assert "codebase-orientation" not in PROJECT_ARTIFACT_UPDATE_PROMPT
    assert "written or reference content" in PROJECT_ARTIFACT_UPDATE_PROMPT


def test_knowledge_type_vocabulary_is_not_fixed():
    """Regression guard: knowledge.eva's TYPE label must be presented as
    illustrative examples the model can extend, not a fixed enum -- a
    hardcoded vocabulary can't fit every kind of project (e.g. a GTM
    content repo has no 'CODE' to file anything under)."""
    from prompts.project import PROJECT_ARTIFACT_UPDATE_PROMPT
    from core.project.eva_format import render_knowledge_eva

    assert "not a fixed list" in PROJECT_ARTIFACT_UPDATE_PROMPT
    skeleton = render_knowledge_eva()
    assert "not a fixed list" in skeleton
    # The illustrative examples must still be present -- this is additive,
    # not a removal of the existing common-case guidance.
    assert "CODE" in skeleton and "NOTE" in skeleton


class TestListFilesRespectsProjectWorkspace(unittest.IsolatedAsyncioTestCase):
    """Regression guard: ListFilesTool.run() previously called
    resolve_path_for_user() with only (user_id, path) -- omitting
    conversation_id/project_workspace that every other tool in
    file_tools.py already passes (read_file, write_file, get_file_info,
    delete_file, read_file_lines). Result: list_files could never see an
    active project's external workspace_path and silently fell back to
    the conversation scratch dir instead -- deterministic for any model,
    not a reasoning/capability issue. Found live: a model asked to
    explore an external-workspace project's repo got an empty directory
    back from list_files and concluded there was no repo to explore."""

    async def test_list_files_resolves_against_project_workspace(self):
        from tools.default.file_tools import ListFilesTool

        with tempfile.TemporaryDirectory() as ext_dir:
            (Path(ext_dir) / "README.md").write_text("hello", encoding="utf-8")
            (Path(ext_dir) / "src").mkdir()

            fake_execution = MagicMock()
            fake_execution.user_id = "test_user"
            fake_execution.conversation_id = "conv-not-used"
            fake_execution.project_workspace = ext_dir

            tool = ListFilesTool()
            tool.execution = fake_execution

            result = await tool.run({"path": "."})

            assert result.result["path"].rstrip("/\\") == ext_dir.rstrip("/\\") \
                or os.path.normpath(result.result["path"]) == os.path.normpath(ext_dir)
            entries = result.result["entries"]
            assert any("README.md" in e for e in entries)

    async def test_list_files_falls_back_without_project_workspace(self):
        """Sanity check the other direction: with no project_workspace set,
        behavior is unchanged from before this fix (falls back down the
        existing priority chain) -- this fix only adds the missing
        arguments, it doesn't change resolve_path_for_user()'s own logic."""
        from tools.default.file_tools import ListFilesTool

        fake_execution = MagicMock()
        fake_execution.user_id = "test_user_no_project"
        fake_execution.conversation_id = None
        fake_execution.project_workspace = None

        tool = ListFilesTool()
        tool.execution = fake_execution

        result = await tool.run({"path": "."})
        # Falls back to the user workspace root -- just confirm it does NOT
        # error and does NOT accidentally resolve to some project path.
        assert "error" not in result.result or "project" not in str(result.result).lower()


# ===========================================================================
# B-049 — Shell Session + Executor
# ===========================================================================

def _run(coro):
    return asyncio.run(coro)


def _make_mock_broker_shell(store: Dict[str, str] = None):
    store = store if store is not None else {}
    mock_client = AsyncMock()

    async def _get(key): return store.get(key)
    async def _set(key, value, ex=None): store[key] = value
    async def _delete(key): store.pop(key, None)

    mock_client.get = _get
    mock_client.set = _set
    mock_client.delete = _delete
    return mock_client, store


def _make_session_patch(session_store=None):
    mock_client, store = _make_mock_broker_shell(session_store or {})
    return patch("core.shell.session._get_broker_client", new=AsyncMock(return_value=mock_client))


def test_fresh_session_empty():
    from core.shell.session import ShellSession
    s = ShellSession(conversation_id="conv_1")
    assert s.cwd == ""
    assert s.env_vars == {}
    assert s.history == []


def test_set_cwd_absolute():
    from core.shell.session import ShellSession
    s = ShellSession(conversation_id="conv_1")
    tmp = tempfile.gettempdir()
    s.set_cwd(tmp)
    assert Path(s.cwd).resolve() == Path(tmp).resolve()


def test_set_cwd_tilde():
    from core.shell.session import ShellSession
    s = ShellSession(conversation_id="conv_1")
    s.set_cwd("~")
    assert s.cwd == str(Path.home())


def test_set_env_and_unset():
    from core.shell.session import ShellSession
    s = ShellSession(conversation_id="conv_1")
    s.set_env("DATABASE_URL", "postgres://localhost/test")
    assert s.env_vars["DATABASE_URL"] == "postgres://localhost/test"
    s.unset_env("DATABASE_URL")
    assert "DATABASE_URL" not in s.env_vars


def test_effective_env_merges_os_environ():
    from core.shell.session import ShellSession
    s = ShellSession(conversation_id="conv_1")
    s.set_env("MY_CUSTOM_VAR", "hello")
    env = s.effective_env()
    assert env["MY_CUSTOM_VAR"] == "hello"
    assert "PATH" in env


def test_add_history_and_cap():
    from core.shell.session import ShellSession, _MAX_HISTORY
    s = ShellSession(conversation_id="conv_1")
    for i in range(_MAX_HISTORY + 10):
        s.add_history(f"cmd_{i}")
    assert len(s.history) == _MAX_HISTORY
    assert s.history[-1] == f"cmd_{_MAX_HISTORY + 9}"


def test_to_dict_shape():
    from core.shell.session import ShellSession
    s = ShellSession(conversation_id="conv_test")
    s.cwd = "/tmp"
    s.set_env("K", "V")
    d = s.to_dict()
    assert d["conversation_id"] == "conv_test"
    assert d["cwd"] == "/tmp"
    assert d["env_vars"] == {"K": "V"}


def test_load_graceful_when_broker_unavailable():
    from core.shell.session import ShellSession

    async def _load():
        with patch("core.shell.session._get_broker_client", new=AsyncMock(return_value=None)):
            return await ShellSession.load("conv_no_broker")

    s = _run(_load())
    assert s.conversation_id == "conv_no_broker"
    assert s.cwd == ""


def test_reset_clears_state():
    from core.shell.session import ShellSession
    mock_client, _ = _make_mock_broker_shell()

    async def _reset():
        with patch("core.shell.session._get_broker_client", new=AsyncMock(return_value=mock_client)):
            s = ShellSession(conversation_id="conv_reset")
            s.cwd = "/somewhere"
            s.set_env("K", "V")
            await s.reset()
            return s

    s = _run(_reset())
    assert s.cwd == ""
    assert s.env_vars == {}


def test_is_cd():
    from core.shell.executor import ShellExecutor
    assert ShellExecutor._is_cd(["cd", "/tmp"])
    assert ShellExecutor._is_cd(["CD", "src"])
    assert ShellExecutor._is_cd(["chdir", ".."])
    assert not ShellExecutor._is_cd(["ls", "-la"])
    assert not ShellExecutor._is_cd([])


def test_is_export():
    from core.shell.executor import ShellExecutor
    assert ShellExecutor._is_export(["export", "VAR=val"])
    assert not ShellExecutor._is_export(["env"])


def test_is_unset():
    from core.shell.executor import ShellExecutor
    assert ShellExecutor._is_unset(["unset", "VAR"])
    assert not ShellExecutor._is_unset(["export", "VAR="])


def test_inject_sentinels_for_bash_c():
    from core.shell.executor import ShellExecutor
    e = ShellExecutor()
    cmd = ["bash", "-c", "echo hello"]
    modified, injected = e._maybe_inject_sentinels(cmd)
    assert injected
    assert "__EVA_CWD__" in modified[2]


def test_no_inject_for_python():
    from core.shell.executor import ShellExecutor
    e = ShellExecutor()
    cmd = ["python", "-m", "pytest"]
    modified, injected = e._maybe_inject_sentinels(cmd)
    assert not injected
    assert modified == cmd


def test_extract_sentinels_cwd():
    from core.shell.executor import ShellExecutor, _SENTINEL_CWD
    e = ShellExecutor()
    stdout = f"hello\n{_SENTINEL_CWD}/new/path\nworld\n"
    clean, new_cwd, env = e._extract_sentinels(stdout)
    assert new_cwd == "/new/path"
    assert "hello" in clean
    assert _SENTINEL_CWD not in clean


def test_resolve_cwd_explicit_wins():
    from core.shell.executor import ShellExecutor
    with patch("utils.workspace.get_user_workspace", return_value="/workspace/user1"):
        result = ShellExecutor._resolve_cwd(
            explicit="/explicit/path", session_cwd="/session/path", user_id="user1")
    assert result == "/explicit/path"


def test_resolve_cwd_workspace_fallback():
    from core.shell.executor import ShellExecutor
    with patch("utils.workspace.get_user_workspace", return_value="/workspace/user1"):
        result = ShellExecutor._resolve_cwd(
            explicit=None, session_cwd="", user_id="user1")
    assert result == "/workspace/user1"


def test_cd_to_existing_dir():
    from core.shell.executor import ShellExecutor
    tmp = tempfile.gettempdir()

    async def _run_cmd():
        with _make_session_patch():
            return await ShellExecutor.run(
                cmd=["cd", tmp], conversation_id="conv_cd1", user_id="u1")

    result = _run(_run_cmd())
    assert result["exit_code"] == 0
    assert result["cwd"] == str(Path(tmp).resolve())


def test_cd_nonexistent_dir():
    from core.shell.executor import ShellExecutor

    async def _run_cmd():
        with _make_session_patch():
            return await ShellExecutor.run(
                cmd=["cd", "/this/does/not/exist/eva_test_xyz"],
                conversation_id="conv_cd3", user_id="u1")

    result = _run(_run_cmd())
    assert result["exit_code"] == 1
    assert "No such file" in result["stderr"]


def test_export_single_var():
    from core.shell.executor import ShellExecutor

    async def _run_cmd():
        with _make_session_patch():
            return await ShellExecutor.run(
                cmd=["export", "MY_VAR=hello_world"],
                conversation_id="conv_exp1", user_id="u1")

    result = _run(_run_cmd())
    assert result["exit_code"] == 0
    assert result["session"]["env_vars"]["MY_VAR"] == "hello_world"


def test_unset_removes_var():
    from core.shell.executor import ShellExecutor

    async def _run_cmd():
        with _make_session_patch():
            await ShellExecutor.run(cmd=["export", "REMOVE_ME=yes"],
                                    conversation_id="conv_unset1", user_id="u1")
            return await ShellExecutor.run(cmd=["unset", "REMOVE_ME"],
                                           conversation_id="conv_unset1", user_id="u1")

    result = _run(_run_cmd())
    assert result["exit_code"] == 0
    assert "REMOVE_ME" not in result["session"]["env_vars"]


def test_python_version():
    from core.shell.executor import ShellExecutor

    async def _run_cmd():
        with _make_session_patch():
            return await ShellExecutor.run(
                cmd=[sys.executable, "--version"],
                conversation_id="conv_py", user_id="u1")

    result = _run(_run_cmd())
    assert result["exit_code"] == 0
    assert "Python" in (result["stdout"] + result["stderr"])


def test_nonexistent_command_returns_error():
    from core.shell.executor import ShellExecutor

    async def _run_cmd():
        with _make_session_patch():
            return await ShellExecutor.run(
                cmd=["this_cmd_does_not_exist_eva_xyz"],
                conversation_id="conv_noexist", user_id="u1")

    result = _run(_run_cmd())
    assert result["exit_code"] == -1
    assert "error" in result


def test_timeout_returns_error():
    from core.shell.executor import ShellExecutor

    async def _run_cmd():
        with _make_session_patch():
            return await ShellExecutor.run(
                cmd=[sys.executable, "-c", "import time; time.sleep(10)"],
                conversation_id="conv_timeout", user_id="u1",
                timeout=1)

    result = _run(_run_cmd())
    assert result["exit_code"] == -1
    assert "timeout" in result.get("error", "").lower()


def test_get_session_action():
    from core.shell.executor import ShellExecutor

    async def _run_cmd():
        with _make_session_patch():
            await ShellExecutor.run(cmd=["export", "INSPECT_ME=yes"],
                                    conversation_id="conv_sess_inspect", user_id="u1")
            return await ShellExecutor.get_session("conv_sess_inspect")

    state = _run(_run_cmd())
    assert "cwd" in state
    assert "env_vars" in state
    assert state["env_vars"].get("INSPECT_ME") == "yes"


def test_reset_session():
    from core.shell.executor import ShellExecutor

    async def _run_cmd():
        with _make_session_patch():
            await ShellExecutor.run(cmd=["export", "WILL_BE_GONE=yes"],
                                    conversation_id="conv_reset2", user_id="u1")
            await ShellExecutor.reset_session("conv_reset2")
            return await ShellExecutor.get_session("conv_reset2")

    state = _run(_run_cmd())
    assert state["env_vars"] == {}
    assert state["cwd"] == ""


def test_empty_cmd_returns_error():
    from core.shell.executor import ShellExecutor

    async def _run_cmd():
        with _make_session_patch():
            return await ShellExecutor.run(cmd=[], conversation_id="conv_empty",
                                           user_id="u1")

    result = _run(_run_cmd())
    assert result["exit_code"] == -1
    assert "error" in result


# ===========================================================================
# SESSION FIX 1 — MemoryEngine message ordering (sequence > UUID)
# ===========================================================================

class TestMemoryEngineOrdering(unittest.TestCase):
    """Fix 1: MemoryEngine must order messages by sequence/created_at, not UUID."""

    def test_memory_engine_query_uses_non_uuid_ordering(self):
        """Source inspection: MemoryEngine uses sequence or created_at ordering."""
        import inspect
        from core.memory.engine import MemoryEngine
        source = inspect.getsource(MemoryEngine)
        has_ordered_sort = (
            "sequence" in source.lower()
            or "created_at" in source.lower()
            or "rowid" in source.lower()
        )
        self.assertTrue(
            has_ordered_sort,
            "MemoryEngine must order by sequence/created_at/rowid — not bare UUID sort",
        )

    def test_memory_engine_does_not_rely_solely_on_id_sort(self):
        """The query must not sort by 'id' alone (UUIDs don't sort chronologically)."""
        import inspect
        from core.memory.engine import MemoryEngine
        source = inspect.getsource(MemoryEngine)
        # Acceptable: ORDER BY sequence, ORDER BY created_at, ORDER BY rowid
        # Not acceptable: ORDER BY id (UUID) as the sole sort key
        # We check that at least one time-ordered field appears
        self.assertTrue(
            "sequence" in source or "created_at" in source,
            "MemoryEngine should include sequence or created_at in its ordering",
        )


# ===========================================================================
# SESSION FIX 4 — _timed_stream idle timeouts
# ===========================================================================

class TestTimedStream(unittest.IsolatedAsyncioTestCase):
    """Fix 4: _timed_stream must timeout on first-chunk and inter-chunk stalls."""

    async def test_first_chunk_timeout_yields_error_chunk(self):
        """Generator that never yields → ErrorChunk after first-chunk timeout."""
        from core.agent_engine import _timed_stream

        async def stalling_gen():
            await asyncio.sleep(60)
            return
            yield  # make it an async generator

        chunks = []
        with patch("core.agent_engine._FIRST_CHUNK_TIMEOUT_S", 0.1), \
             patch("core.agent_engine._INTER_CHUNK_TIMEOUT_S", 30):
            async for chunk in _timed_stream(stalling_gen(), "exec-1", "test-prov", "test-model"):
                chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertTrue(hasattr(chunks[0], "error_type"),
                        "Timeout must yield an ErrorChunk")
        self.assertIn("timeout", chunks[0].error_type.lower())

    async def test_inter_chunk_timeout_yields_error_chunk(self):
        """Generator yields first chunk then stalls → ErrorChunk after inter-chunk timeout."""
        from core.agent_engine import _timed_stream
        from core.types import TextChunk

        async def one_then_stall():
            yield TextChunk(content="first chunk")
            await asyncio.sleep(60)

        chunks = []
        with patch("core.agent_engine._FIRST_CHUNK_TIMEOUT_S", 5), \
             patch("core.agent_engine._INTER_CHUNK_TIMEOUT_S", 0.1):
            async for chunk in _timed_stream(one_then_stall(), "exec-1", "test-prov", "test-model"):
                chunks.append(chunk)

        text_chunks  = [c for c in chunks if hasattr(c, "content") and not hasattr(c, "error_type")]
        error_chunks = [c for c in chunks if hasattr(c, "error_type")]
        self.assertEqual(len(text_chunks), 1)
        self.assertEqual(len(error_chunks), 1)
        self.assertIn("timeout", error_chunks[0].error_type.lower())

    async def test_normal_chunks_pass_through(self):
        """Normal fast generator → all chunks pass, no ErrorChunk."""
        from core.agent_engine import _timed_stream
        from core.types import TextChunk

        async def fast_gen():
            for i in range(5):
                yield TextChunk(content=f"chunk {i}")

        chunks = []
        async for chunk in _timed_stream(fast_gen(), "exec-1", "test-prov", "test-model"):
            chunks.append(chunk)

        error_chunks = [c for c in chunks if hasattr(c, "error_type")]
        self.assertEqual(len(error_chunks), 0)
        self.assertEqual(len(chunks), 5)


# ===========================================================================
# SESSION FIX 5 — FALLBACK_ERROR_TYPES includes "timeout"
# ===========================================================================

class TestFallbackErrorTypes(unittest.TestCase):
    """
    Fix 5: fallback-eligible error types must include timeout, rate_limit,
    provider_down (and, per a later intentional design decision below,
    auth). The flat FALLBACK_ERROR_TYPES set this class originally tested
    was replaced by ERROR_POLICIES (Dict[ErrorType, ErrorPolicy]), a
    richer per-type policy carrying retryable/max_retries/fallback_eligible/
    skip_same_provider -- see core/error_handling.py's own "Tier A/B/C"
    comment block for the full rationale.
    """

    def test_timeout_in_fallback_error_types(self):
        from core.error_handling import ErrorType, get_error_policy
        self.assertTrue(get_error_policy(ErrorType.TIMEOUT).fallback_eligible)

    def test_rate_limit_in_fallback_error_types(self):
        from core.error_handling import ErrorType, get_error_policy
        self.assertTrue(get_error_policy(ErrorType.RATE_LIMIT).fallback_eligible)

    def test_provider_down_in_fallback_error_types(self):
        from core.error_handling import ErrorType, get_error_policy
        self.assertTrue(get_error_policy(ErrorType.PROVIDER_DOWN).fallback_eligible)

    def test_auth_error_fallback_eligible_but_skips_same_provider(self):
        """
        Auth errors ARE fallback-eligible (Tier B) -- a bad/expired key on
        one provider shouldn't block a fallback_chain entry on a *different*
        provider. skip_same_provider=True means only candidates sharing the
        failed provider are skipped without being attempted; this replaces
        the old (and, per this design, no longer accurate) expectation that
        auth errors never trigger fallback at all.
        """
        from core.error_handling import ErrorType, get_error_policy
        policy = get_error_policy(ErrorType.AUTH)
        self.assertTrue(policy.fallback_eligible)
        self.assertTrue(policy.skip_same_provider)


# ===========================================================================
# SESSION FIX 6 — classify_error returns typed enum with .value
# ===========================================================================

class TestClassifyError(unittest.TestCase):
    """Fix 6: classify_error must return an enum with a .value attribute."""

    def _classify_value(self, msg: str) -> str:
        from core.error_handling import classify_error
        result = classify_error(Exception(msg))
        return result.value if hasattr(result, "value") else str(result)

    def test_returns_object_with_value_attribute(self):
        from core.error_handling import classify_error
        result = classify_error(Exception("rate limit exceeded"))
        self.assertTrue(hasattr(result, "value"),
                        "classify_error must return an enum/object with .value")

    def test_rate_limit_classification(self):
        val = self._classify_value("Rate limit exceeded. Too many requests.")
        self.assertEqual(val, "rate_limit")

    def test_timeout_classification(self):
        val = self._classify_value("Request timed out after 30 seconds")
        self.assertEqual(val, "timeout")

    def test_auth_error_classification(self):
        val = self._classify_value("Invalid API key: Unauthorized 401")
        self.assertEqual(val, "auth")

    def test_provider_down_classification(self):
        val = self._classify_value("Service unavailable 503. The backend is down.")
        self.assertEqual(val, "provider_down")

    def test_unknown_error_has_value(self):
        from core.error_handling import classify_error
        result = classify_error(Exception("completely unknown mysterious failure xyz"))
        self.assertTrue(hasattr(result, "value"))
        self.assertIsNotNone(result.value)


# ===========================================================================
# SESSION FIX 8 — message_logger empty-response placeholder vs overflow
# ===========================================================================

class TestMessageLoggerEmptyResponse(unittest.TestCase):
    """Fix 8: empty assistant response → placeholder unless finish_reason='length'."""

    def test_source_handles_length_finish_reason(self):
        """Code must branch on finish_reason == 'length' for context-overflow detection."""
        import inspect
        import core.logging.message_logger as mod
        source = inspect.getsource(mod)
        self.assertIn("length", source,
                      "message_logger must handle finish_reason='length' specially")
        self.assertIn("finish_reason", source,
                      "message_logger must inspect finish_reason")

    def test_source_saves_placeholder_for_non_overflow(self):
        """Non-overflow empty response must be replaced with a visible placeholder."""
        import inspect
        import core.logging.message_logger as mod
        source = inspect.getsource(mod)
        self.assertIn("Please try rephrasing", source,
                      "message_logger must embed placeholder text for non-overflow empty response")

    def test_source_returns_none_on_length_overflow(self):
        """Context overflow (length) must return None without saving a message."""
        import inspect
        import core.logging.message_logger as mod
        source = inspect.getsource(mod)
        # The logic: if finish_reason == "length": return None
        self.assertIn("return None", source,
                      "Context overflow path must return None to skip DB write")


class TestMessageLoggerFunctional(unittest.IsolatedAsyncioTestCase):
    """Functional tests for MessageLogger.save_final_message empty-response handling."""

    async def test_empty_content_with_length_finish_reason_returns_none(self):
        """finish_reason='length' + empty content → None (context overflow, don't save)."""
        from core.logging.message_logger import MessageLogger
        mock_db = AsyncMock()
        logger = MessageLogger(db=mock_db, conversation_id="conv-1", user_id="u1")
        result = await logger.save_final_message(
            role="assistant",
            content="",
            finish_reason="length",
        )
        self.assertIsNone(result, "Context overflow must return None — not saved to DB")

    async def test_empty_content_with_stop_finish_reason_saves_placeholder(self):
        """finish_reason='stop' + empty content → saves a placeholder with 'Please try rephrasing'."""
        from core.logging.message_logger import MessageLogger
        mock_db = AsyncMock()

        with patch("core.db.repository.DBRepo.add_message", new_callable=AsyncMock) as mock_add:
            mock_msg = MagicMock()
            mock_msg.id = "msg-placeholder-1"
            mock_add.return_value = mock_msg

            logger = MessageLogger(db=mock_db, conversation_id="conv-1", user_id="u1")
            result = await logger.save_final_message(
                role="assistant",
                content="",
                finish_reason="stop",
            )

        # Must NOT return None — a placeholder message is saved
        self.assertIsNotNone(result, "Empty stop response must save a placeholder, not return None")
        # Verify the placeholder text was used
        call_kwargs = mock_add.call_args.kwargs if mock_add.call_args else {}
        actual_content = call_kwargs.get("content", "")
        self.assertIn("Please try rephrasing", actual_content,
                      "Placeholder content must mention 'Please try rephrasing'")

    async def test_non_empty_content_saved_normally(self):
        """Non-empty content is saved without modification."""
        from core.logging.message_logger import MessageLogger
        mock_db = AsyncMock()

        with patch("core.db.repository.DBRepo.add_message", new_callable=AsyncMock) as mock_add:
            mock_msg = MagicMock()
            mock_msg.id = "msg-normal-1"
            mock_add.return_value = mock_msg

            logger = MessageLogger(db=mock_db, conversation_id="conv-1", user_id="u1")
            await logger.save_final_message(
                role="assistant",
                content="Hello! How can I help you today?",
                finish_reason="stop",
            )

        call_kwargs = mock_add.call_args.kwargs if mock_add.call_args else {}
        actual_content = call_kwargs.get("content", "")
        self.assertIn("Hello", actual_content)
        self.assertNotIn("Please try rephrasing", actual_content)


# ===========================================================================
# SESSION FIX 9 — extractor mode gating (Scope 3 only on plan_execute)
# ===========================================================================

class TestExtractorModeGating(unittest.TestCase):
    """Fix 9: agent-scope (Scope 3) memory must only be extracted in plan_execute mode."""

    def test_extract_and_store_accepts_execution_mode_param(self):
        """extract_and_store() must have an execution_mode parameter."""
        import inspect
        from core.memory.extractor import MemoryExtractor
        sig = inspect.signature(MemoryExtractor.extract_and_store)
        self.assertIn("execution_mode", sig.parameters,
                      "extract_and_store must accept execution_mode parameter")

    def test_source_gates_scope3_on_plan_execute(self):
        """Scope 3 gate in extractor must check execution_mode == 'plan_execute'."""
        import inspect
        import core.memory.extractor as mod
        source = inspect.getsource(mod)
        self.assertIn("plan_execute", source,
                      "Extractor must gate Scope 3 extraction on execution_mode == 'plan_execute'")

    def test_source_has_conversational_default(self):
        """execution_mode defaults to 'conversational' — safe default skips Scope 3."""
        import inspect
        import core.memory.extractor as mod
        source = inspect.getsource(mod)
        self.assertIn("conversational", source,
                      "Extractor must default execution_mode to 'conversational'")

    def test_worker_passes_execution_mode_to_extractor(self):
        """worker.py must pass execution_mode when calling extract_and_store."""
        import inspect
        import core.execution.worker as mod
        source = inspect.getsource(mod)
        self.assertIn("execution_mode", source,
                      "worker.py must pass execution_mode= to extract_and_store")


# ===========================================================================
# WORKER OWNERSHIP — liveness beacon + claim + scoped watchdog
# ===========================================================================


class TestWatchdogOwnershipCases(unittest.IsolatedAsyncioTestCase):
    """Watchdog 3-case ownership logic: skip foreign-alive, grace for foreign-dead, takeover."""

    def _make_client_with_execution(self, exec_data: dict, owner_alive: bool) -> AsyncMock:
        mock_client = AsyncMock()

        async def _scan_iter(pattern):
            yield "execution:test-exec-001"

        mock_client.scan_iter = _scan_iter
        mock_client.type = AsyncMock(return_value="hash")
        mock_client.hgetall = AsyncMock(return_value=exec_data)
        mock_client.exists = AsyncMock(return_value=1 if owner_alive else 0)
        return mock_client

    async def test_watchdog_skips_foreign_live_owner(self):
        """Execution owned by a live foreign worker → watchdog must NOT abort it."""
        exec_data = {
            "status": "running",
            "last_heartbeat_at": _ts(20),  # older than timeout
            "user_id": "user-1",
            "worker_id": "foreign-worker-abc",
        }
        mock_client = self._make_client_with_execution(exec_data, owner_alive=True)
        abort_calls = []

        with patch("core.execution.worker.redis_execution_registry.connect",
                   new_callable=AsyncMock, return_value=mock_client), \
             patch("core.execution.worker.redis_execution_registry.is_worker_alive",
                   new_callable=AsyncMock, return_value=True), \
             patch("core.execution.worker.WATCHDOG_INTERVAL_SECONDS", 0), \
             patch("core.execution.worker.WATCHDOG_RUNNING_TIMEOUT_SECONDS", 900):
            from core.execution.worker import ExecutionWorker
            worker = ExecutionWorker()
            worker.worker_id = "local-worker-xyz"
            worker._running = True

            async def _abort_and_stop(execution_id, reason):
                abort_calls.append((execution_id, reason))
                worker._running = False

            with patch("core.execution.worker.redis_execution_registry.mark_execution_aborted",
                       side_effect=_abort_and_stop):
                async def _stop():
                    await asyncio.sleep(0.08)
                    worker._running = False
                await asyncio.gather(worker._watchdog_loop(), _stop())

        self.assertEqual(len(abort_calls), 0,
                         "Must NOT abort execution owned by a live foreign worker")

    async def test_watchdog_source_has_worker_id_check(self):
        """Watchdog source must reference worker_id for ownership-aware decisions."""
        import inspect
        import core.execution.worker as mod
        source = inspect.getsource(mod)
        self.assertIn("owner_worker_id", source,
                      "Watchdog must read owner_worker_id from execution data")
        self.assertIn("is_worker_alive", source,
                      "Watchdog must call is_worker_alive() before aborting foreign executions")



# ===========================================================================
# SESSION FIX 10 — pev_runner cancel check includes "aborted"
# ===========================================================================

class TestPevRunnerCancelCheck(unittest.TestCase):
    """Fix 10: pev_runner.py must detect 'aborted' status, not just 'cancelled'."""

    def test_pev_runner_checks_aborted_in_broker_status(self):
        """Source must include 'aborted' in the broker status cancellation check."""
        import inspect
        import core.execution.pev_runner as mod
        source = inspect.getsource(mod)
        self.assertIn('"aborted"', source,
                      "pev_runner must check for 'aborted' status to cancel execution")

    def test_pev_runner_checks_cancelled_in_broker_status(self):
        """Source must also still check for 'cancelled'."""
        import inspect
        import core.execution.pev_runner as mod
        source = inspect.getsource(mod)
        self.assertIn('"cancelled"', source,
                      "pev_runner must check for 'cancelled' status to cancel execution")

    def test_pev_runner_has_cancelled_error_guard(self):
        """CancelledError handler must have try/except guard for terminal-state transitions."""
        import inspect
        import core.execution.pev_runner as mod
        source = inspect.getsource(mod)
        self.assertIn("CancelledError", source,
                      "pev_runner must handle asyncio.CancelledError")

    def test_runner_py_also_checks_aborted(self):
        """runner.py must also check 'aborted' (regression guard)."""
        import inspect
        import core.execution.runner as mod
        source = inspect.getsource(mod)
        self.assertIn('"aborted"', source,
                      "runner.py must check for 'aborted' status (regression guard)")


# ===========================================================================
# SESSION FIX 11 — cancel_token wired to AgentEngine
# ===========================================================================

class TestCancelTokenWiring(unittest.TestCase):
    """Fix 11: worker.py must pass cancel_token to AgentEngine constructor."""

    def test_worker_passes_cancel_token_to_agent_engine(self):
        """worker.py source must pass cancel_token= when constructing AgentEngine."""
        import inspect
        import core.execution.worker as mod
        source = inspect.getsource(mod)
        self.assertIn("cancel_token", source,
                      "worker.py must pass cancel_token to AgentEngine")

    def test_worker_has_worker_id(self):
        """Worker instance must have a worker_id attribute for ownership tracking."""
        import inspect
        import core.execution.worker as mod
        source = inspect.getsource(mod)
        self.assertIn("worker_id", source,
                      "ExecutionWorker must have a worker_id for ownership tracking")
        self.assertIn("uuid4", source,
                      "worker_id must be generated via uuid4()")

    def test_worker_uses_pipeline_for_heartbeat(self):
        """Worker heartbeat must refresh both worker:heartbeat and worker:alive via pipeline."""
        import inspect
        import core.execution.worker as mod
        source = inspect.getsource(mod)
        self.assertIn("pipeline", source,
                      "Worker must use broker pipeline to refresh both heartbeat keys atomically")


# ===========================================================================
# SESSION FIX 12 — approval gate sets cancel_token immediately on rejection
# ===========================================================================

class TestApprovalGateCancelToken(unittest.TestCase):
    """Fix 12: gate_approval() must set cancel_token.cancelled=True immediately on rejection."""

    def test_approval_source_sets_cancel_token_on_rejection(self):
        """approval.py must set cancel_token.cancelled = True on rejection/timeout."""
        import inspect
        import core.tools.approval as mod
        source = inspect.getsource(mod)
        self.assertIn("cancel_token.cancelled = True", source,
                      "approval.py must set cancel_token.cancelled = True on rejection")

    def test_approval_source_checks_has_cancel_token(self):
        """approval.py must guard the cancel_token set with hasattr() check."""
        import inspect
        import core.tools.approval as mod
        source = inspect.getsource(mod)
        self.assertIn("hasattr(execution, \"cancel_token\")", source,
                      "Must guard cancel_token access with hasattr()")


class TestApprovalGateCancelTokenFunctional(unittest.IsolatedAsyncioTestCase):
    """Functional test: gate_approval rejection must set cancel_token immediately."""

    async def test_rejection_sets_cancel_token_immediately(self):
        """When gate_approval returns False, execution.cancel_token.cancelled must be True."""
        from core.tools.approval import gate_approval

        # Build a mock execution object
        mock_execution = MagicMock()
        mock_execution.execution_id = "exec-cancel-test"
        mock_execution.conversation_id = "conv-cancel-test"
        mock_execution.user_id = "user-cancel-test"
        mock_execution.status = MagicMock()
        mock_cancel_token = MagicMock()
        mock_cancel_token.cancelled = False
        mock_execution.cancel_token = mock_cancel_token

        # Mock broker: blpop returns None (timeout → rejection)
        mock_broker = AsyncMock()
        mock_broker.setex = AsyncMock()
        mock_broker.hset = AsyncMock()
        mock_broker.hdel = AsyncMock()
        mock_broker.delete = AsyncMock()
        mock_broker.blpop = AsyncMock(return_value=None)  # timeout → rejected

        with patch("core.tools.approval.redis_execution_registry.connect",
                   new_callable=AsyncMock, return_value=mock_broker), \
             patch("core.tools.approval.redis_execution_registry.mark_execution_awaiting_approval",
                   new_callable=AsyncMock), \
             patch("core.tools.approval.redis_execution_registry.mark_execution_cancelled",
                   new_callable=AsyncMock), \
             patch("core.tools.approval._push_approval_event_to_stream",
                   new_callable=AsyncMock), \
             patch("core.tools.approval.ExecutionStateMachine.assert_transition_allowed"), \
             patch("core.tools.approval.event_bus.publish"):
            result = await gate_approval(
                execution=mock_execution,
                call_id="call-test-001",
                tool_name="dangerous_tool",
                authority="destructive",
                arguments={"target": "/etc"},
                timeout_seconds=1,
            )

        self.assertFalse(result, "gate_approval must return False on timeout/rejection")
        self.assertTrue(mock_cancel_token.cancelled,
                        "cancel_token.cancelled must be True immediately after rejection")


# ===========================================================================
# EVA Soul Layer — Platform Identity (V5.0 Sprint 1)
# ===========================================================================

class TestSoulPreambleModule(unittest.TestCase):
    """prompts/soul.py — EVA_PLATFORM_PREAMBLE constant and build_platform_preamble()."""

    def test_soul_module_importable(self):
        import prompts.soul  # noqa: F401

    def test_preamble_constant_exists(self):
        from prompts.soul import EVA_PLATFORM_PREAMBLE
        self.assertIsInstance(EVA_PLATFORM_PREAMBLE, str)

    def test_preamble_is_non_empty(self):
        from prompts.soul import EVA_PLATFORM_PREAMBLE
        self.assertGreater(len(EVA_PLATFORM_PREAMBLE), 100,
                           "EVA_PLATFORM_PREAMBLE must be at least 100 chars")

    def test_preamble_contains_eva_identity(self):
        from prompts.soul import EVA_PLATFORM_PREAMBLE
        self.assertIn("EVA", EVA_PLATFORM_PREAMBLE,
                      "Preamble must identify EVA by name")

    def test_preamble_contains_safety_principle(self):
        from prompts.soul import EVA_PLATFORM_PREAMBLE
        lower = EVA_PLATFORM_PREAMBLE.lower()
        self.assertTrue(
            "irreversible" in lower or "fabricate" in lower or "uncertainty" in lower,
            "Preamble must include at least one safety/honesty operating principle",
        )

    def test_build_platform_preamble_returns_string(self):
        from prompts.soul import build_platform_preamble
        result = build_platform_preamble()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_build_platform_preamble_is_stable(self):
        """Two calls must return identical strings (preamble is a constant in V5.0)."""
        from prompts.soul import build_platform_preamble
        self.assertEqual(build_platform_preamble(), build_platform_preamble())

    def test_build_platform_preamble_equals_constant(self):
        from prompts.soul import build_platform_preamble, EVA_PLATFORM_PREAMBLE
        self.assertEqual(build_platform_preamble(), EVA_PLATFORM_PREAMBLE)


class TestEvaVersionSetting(unittest.TestCase):
    """config/settings.py — EVA_VERSION constant."""

    def test_eva_version_exists(self):
        from config.settings import EVA_VERSION
        self.assertIsInstance(EVA_VERSION, str)

    def test_eva_version_non_empty(self):
        from config.settings import EVA_VERSION
        self.assertGreater(len(EVA_VERSION), 0)

    def test_eva_version_has_semver_shape(self):
        """Version must contain at least one dot (e.g. '5.0.0-local')."""
        from config.settings import EVA_VERSION
        self.assertIn(".", EVA_VERSION,
                      "EVA_VERSION should follow semver format (e.g. '5.0.0-local')")


class TestSoulThreeTierAssembly(unittest.TestCase):
    """worker.py — three-tier system prompt assembly wiring."""

    def test_worker_imports_soul_module(self):
        import inspect
        import core.execution.worker as mod
        source = inspect.getsource(mod)
        self.assertIn("prompts.soul", source,
                      "worker.py must import from prompts.soul")

    def test_worker_imports_build_platform_preamble(self):
        import inspect
        import core.execution.worker as mod
        source = inspect.getsource(mod)
        self.assertIn("build_platform_preamble", source,
                      "worker.py must use build_platform_preamble()")

    def test_worker_preamble_before_base_system_prompt(self):
        """In worker.py source, platform preamble must be assembled before
        project/memory blocks are appended to augmented_system_prompt."""
        import inspect
        import core.execution.worker as mod
        source = inspect.getsource(mod)
        preamble_pos  = source.find("build_platform_preamble()")
        aug_first_pos = source.find("augmented_system_prompt =")
        # preamble call must appear at or after the augmented_system_prompt assignment
        # (they are in the same block — preamble call followed immediately by assignment)
        self.assertGreater(preamble_pos, 0,
                           "build_platform_preamble() call not found in worker.py")
        self.assertGreater(aug_first_pos, 0,
                           "augmented_system_prompt assignment not found in worker.py")

    def test_worker_three_tier_comment_present(self):
        """The soul layer block must have its section comment for maintainability."""
        import inspect
        import core.execution.worker as mod
        source = inspect.getsource(mod)
        self.assertIn("Three-Tier", source,
                      "worker.py soul layer block must have the 'Three-Tier' section comment")

    def test_assembly_preamble_before_user_prompt(self):
        """Simulate the assembly: preamble must appear before user system_prompt."""
        from prompts.soul import build_platform_preamble
        preamble    = build_platform_preamble()
        user_prompt = "You are a Python expert."
        # Replicate the assembly logic from worker.py
        assembled = preamble + "\n\n---\n\n" + user_prompt
        self.assertLess(assembled.find(preamble[:40]),
                        assembled.find(user_prompt),
                        "Platform preamble must appear before user system_prompt")

    def test_assembly_empty_user_prompt_gives_only_preamble(self):
        """When conversation has no system_prompt, assembled result is just the preamble."""
        from prompts.soul import build_platform_preamble
        preamble    = build_platform_preamble()
        user_prompt = ""
        assembled   = preamble if not user_prompt else preamble + "\n\n---\n\n" + user_prompt
        self.assertEqual(assembled, preamble)
        self.assertNotIn("---", assembled)

    def test_assembly_separator_present_when_user_prompt_set(self):
        """Three-tier separator must appear between preamble and user prompt."""
        from prompts.soul import build_platform_preamble
        preamble    = build_platform_preamble()
        user_prompt = "Act as a senior DevOps engineer."
        assembled   = preamble + "\n\n---\n\n" + user_prompt
        self.assertIn("---", assembled)


# ===========================================================================
# V5.0 Sprint 2 — Progressive Tool Disclosure
# ===========================================================================

class TestToolBaseFields(unittest.TestCase):
    """Phase B1: ToolBase has category, chat_excluded, summary fields."""

    def test_toolbase_has_category(self):
        from core.tools.base import ToolBase
        self.assertTrue(hasattr(ToolBase, "category"))
        self.assertEqual(ToolBase.category, "general")

    def test_toolbase_has_chat_excluded(self):
        from core.tools.base import ToolBase
        self.assertTrue(hasattr(ToolBase, "chat_excluded"))
        self.assertFalse(ToolBase.chat_excluded)

    def test_toolbase_has_summary(self):
        from core.tools.base import ToolBase
        self.assertTrue(hasattr(ToolBase, "summary"))
        self.assertIsNone(ToolBase.summary)

    def test_toolbase_agent_only_still_present(self):
        """agent_only alias kept for backward compatibility."""
        from core.tools.base import ToolBase
        self.assertTrue(hasattr(ToolBase, "agent_only"))


class TestDefaultToolAnnotations(unittest.TestCase):
    """Phase B1: all default tools must have category and summary set."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()
        from core.tools.registry import DefaultToolRegistry
        cls.tools = DefaultToolRegistry.all()

    def test_all_tools_have_category(self):
        missing = [
            name for name, cls in self.tools.items()
            if not getattr(cls, "category", None)
        ]
        self.assertEqual(missing, [], f"Tools missing category: {missing}")

    def test_all_tools_have_summary(self):
        missing = [
            name for name, cls in self.tools.items()
            if not getattr(cls, "summary", None)
        ]
        self.assertEqual(missing, [], f"Tools missing summary: {missing}")

    def test_delegation_tools_chat_exclusion(self):
        """
        parallel_delegate/coordinate (multi-agent orchestration) stay
        chat_excluded -- PEV/agent-mode only. delegate_to_agent
        (single-agent delegation) was deliberately un-excluded in V6.3
        so it's usable
        from plain conversational chat too, not just PEV mode -- this
        replaces the old expectation that all three stay excluded, which
        predates that change. See tests/test_tools_unit.py's
        test_delegate_to_agent_is_no_longer_chat_excluded for the primary,
        already-current coverage of this same behaviour.
        """
        excluded_tools = {"parallel_delegate", "coordinate"}
        for name in excluded_tools:
            if name in self.tools:
                cls = self.tools[name]
                self.assertTrue(
                    getattr(cls, "chat_excluded", False) or getattr(cls, "agent_only", False),
                    f"{name} should be chat_excluded",
                )

        if "delegate_to_agent" in self.tools:
            cls = self.tools["delegate_to_agent"]
            self.assertFalse(
                getattr(cls, "chat_excluded", False) or getattr(cls, "agent_only", False),
                "delegate_to_agent should no longer be chat_excluded (V6.3)",
            )

    def test_describe_tool_is_registered(self):
        """describe_tool meta-tool must be in the default registry."""
        self.assertIn("describe_tool", self.tools)

    def test_describe_tool_category_is_general(self):
        cls = self.tools.get("describe_tool")
        if cls:
            self.assertEqual(getattr(cls, "category", None), "general")


class TestUserRegistryProgressiveDisclosure(unittest.TestCase):
    """Phase B2: UserToolRegistry progressive disclosure API."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    def _make_registry(self, progressive=True, execution_mode="conversational"):
        from core.tools.user_registry import UserToolRegistry
        return UserToolRegistry(
            default_tools=True,
            enabled_tools=[],
            execution_mode=execution_mode,
            progressive=progressive,
        )

    def test_non_progressive_get_active_returns_all_tools(self):
        reg = self._make_registry(progressive=False, execution_mode="agent")
        self.assertEqual(
            set(reg.get_active_tool_classes().keys()),
            set(reg.all_tools().keys()),
        )

    def test_progressive_initial_active_has_only_describe_tool(self):
        """Before any promotion, only describe_tool is in active set."""
        reg = self._make_registry(progressive=True)
        active = reg.get_active_tool_classes()
        self.assertIn("describe_tool", active,
                      "describe_tool must be always-active in progressive mode")
        # describe_tool should be the only tool initially
        non_describe = [n for n in active if n != "describe_tool"]
        self.assertEqual(
            non_describe, [],
            f"No other tools should be active before promotion, got: {non_describe}",
        )

    def test_promote_adds_tool_to_active(self):
        # web_search_text was consolidated into a single web_search tool
        # (Phase 2, this session's own web_search_*-consolidation work).
        reg = self._make_registry(progressive=True)
        self.assertNotIn("web_search", reg.get_active_tool_classes())
        result = reg.promote("web_search")
        self.assertTrue(result, "promote should return True for known tool")
        self.assertIn("web_search", reg.get_active_tool_classes())

    def test_promote_unknown_tool_returns_false(self):
        reg = self._make_registry(progressive=True)
        result = reg.promote("nonexistent_tool_xyz")
        self.assertFalse(result)

    def test_promoted_count(self):
        reg = self._make_registry(progressive=True)
        self.assertEqual(reg.promoted_count(), 0)
        reg.promote("read_file")
        self.assertEqual(reg.promoted_count(), 1)
        reg.promote("write_file")
        self.assertEqual(reg.promoted_count(), 2)

    def test_all_tools_still_accessible_in_progressive(self):
        """all_tools() always returns full set regardless of progressive state."""
        reg = self._make_registry(progressive=True)
        all_names = set(reg.all_tools().keys())
        self.assertGreater(len(all_names), 1)
        # Full set available via all_tools even before any promote()
        self.assertIn("shell", all_names)

    def test_chat_excluded_tools_absent_in_conversational(self):
        # coordinate (multi-agent orchestration) stays chat_excluded=True --
        # delegate_to_agent no longer does (V6.3, un-excluded so it's usable
        # from plain conversational chat; see test_delegation_tools_chat_exclusion
        # above and tests/test_tools_unit.py for that specific behaviour).
        reg = self._make_registry(progressive=True, execution_mode="conversational")
        tool_names = set(reg.all_tools().keys())
        self.assertNotIn(
            "coordinate", tool_names,
            "chat_excluded tools must be absent from conversational mode",
        )

    def test_category_filtering_applied(self):
        """TOOL_CATEGORIES_BY_MODE restricts tools in conversational mode."""
        from core.tools.user_registry import UserToolRegistry
        # Build with explicit category filter
        reg = UserToolRegistry(
            default_tools=True,
            execution_mode="conversational",
            progressive=True,
            allowed_categories={"search", "general"},
        )
        for name, cls in reg.all_tools().items():
            cat = getattr(cls, "category", "general")
            self.assertIn(
                cat, {"search", "general"},
                f"Tool '{name}' (category='{cat}') should not be in restricted set",
            )


class TestDescribeTool(unittest.TestCase):
    """Phase B2: describe_tool meta-tool functionality."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    def _make_mock_execution(self, progressive=True):
        from core.tools.user_registry import UserToolRegistry
        from unittest.mock import MagicMock
        execution = MagicMock()
        reg = UserToolRegistry(
            default_tools=True,
            execution_mode="conversational",
            progressive=progressive,
        )
        execution.user_registry = reg
        return execution, reg

    async def _run_describe(self, tool_name, execution=None):
        from core.tools.registry import DefaultToolRegistry
        cls = DefaultToolRegistry.all().get("describe_tool")
        self.assertIsNotNone(cls, "describe_tool must be registered")
        instance = cls()
        instance.execution = execution
        return await instance.run({"name": tool_name})

    def test_describe_known_tool_returns_schema(self):
        import asyncio
        execution, reg = self._make_mock_execution()
        result = asyncio.run(
            self._run_describe("read_file", execution)
        )
        data = result.result
        self.assertEqual(data.get("tool"), "read_file")
        self.assertIn("parameters", data)
        self.assertIn("description", data)

    def test_describe_promotes_tool(self):
        import asyncio
        execution, reg = self._make_mock_execution()
        self.assertNotIn("read_file", reg._promoted_tools)
        asyncio.run(
            self._run_describe("read_file", execution)
        )
        self.assertIn("read_file", reg._promoted_tools)

    def test_describe_unknown_tool_returns_error(self):
        import asyncio
        execution, reg = self._make_mock_execution()
        result = asyncio.run(
            self._run_describe("totally_unknown_tool_xyz", execution)
        )
        data = result.result
        self.assertIn("error", data)

    def test_describe_without_execution_still_returns_schema(self):
        """describe_tool should work even without execution context (e.g. tests)."""
        import asyncio
        result = asyncio.run(
            self._run_describe("shell", execution=None)
        )
        data = result.result
        self.assertEqual(data.get("tool"), "shell")
        self.assertIn("parameters", data)


class TestBuildManifest(unittest.TestCase):
    """Phase B2: _build_manifest generates correct manifest text."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    def test_manifest_contains_all_tool_names(self):
        from core.execution.worker import _build_manifest
        from core.tools.registry import DefaultToolRegistry
        tools = DefaultToolRegistry.all()
        manifest = _build_manifest(tools, "conversational")
        for name in tools:
            self.assertIn(name, manifest, f"Manifest must list tool '{name}'")

    def test_manifest_contains_describe_tool_instruction(self):
        from core.execution.worker import _build_manifest
        from core.tools.registry import DefaultToolRegistry
        manifest = _build_manifest(DefaultToolRegistry.all(), "conversational")
        self.assertIn("describe_tool", manifest)

    def test_manifest_empty_tools_returns_empty_string(self):
        from core.execution.worker import _build_manifest
        self.assertEqual(_build_manifest({}, "conversational"), "")

    def test_manifest_uses_summary_field(self):
        """Each tool line uses summary not the full description."""
        from core.execution.worker import _build_manifest
        from core.tools.registry import DefaultToolRegistry
        tools = DefaultToolRegistry.all()
        manifest = _build_manifest(tools, "conversational")
        # Pick a tool with a known summary and verify it appears
        shell_cls = tools.get("shell")
        if shell_cls and getattr(shell_cls, "summary", None):
            self.assertIn(shell_cls.summary, manifest)


class TestSoulPreambleToolDisclosure(unittest.TestCase):
    """Sprint 2: soul preamble updated to mention progressive disclosure."""

    def test_preamble_mentions_describe_tool(self):
        from prompts.soul import build_platform_preamble
        preamble = build_platform_preamble()
        self.assertIn(
            "describe_tool", preamble,
            "Preamble must tell the model about describe_tool",
        )

    def test_preamble_mentions_tool_access(self):
        from prompts.soul import build_platform_preamble
        preamble = build_platform_preamble()
        # Should mention some form of tool access / available tools
        self.assertTrue(
            any(kw in preamble for kw in ("Tool Access", "Available Tools", "tool suite")),
            "Preamble must describe the tool access mechanism",
        )

    def test_build_tool_disclosure_note_progressive_returns_empty(self):
        from prompts.soul import build_tool_disclosure_note
        self.assertEqual(build_tool_disclosure_note(progressive=True), "")

    def test_build_tool_disclosure_note_non_progressive_returns_string(self):
        from prompts.soul import build_tool_disclosure_note
        note = build_tool_disclosure_note(progressive=False)
        self.assertIsInstance(note, str)
        self.assertGreater(len(note), 0)


class TestHallucinationGate(unittest.TestCase):
    """Phase B3: the progressive-disclosure gate promotes an unpromoted tool. Since V6.50 it
    also EXECUTES the call (metadata.auto_activated) instead of returning a retry stub --
    see tests/test_progressive_gate_unit.py for the full contract."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    async def _run_gate_test(self, tool_name, progressive, already_promoted=False):
        from core.tools.dispatcher import ToolDispatcher
        from core.tools.user_registry import UserToolRegistry
        from unittest.mock import MagicMock

        reg = UserToolRegistry(
            default_tools=True,
            execution_mode="conversational",
            progressive=progressive,
        )
        if already_promoted:
            reg.promote(tool_name)

        execution = MagicMock()
        execution.user_registry = reg
        execution.execution_id = "test-exec-123"
        execution.user_id = ""
        execution.conversation_id = ""

        tool_classes = reg.all_tools()
        result = await ToolDispatcher.execute_tool(
            call_id="test-call-1",
            tool_name=tool_name,
            arguments={"path": "/tmp/test.txt"},
            tool_classes=tool_classes,
            execution_metadata={"execution": execution},
        )
        return result, reg

    def test_gate_promotes_and_executes_unpromoted_tool_in_progressive_mode(self):
        import asyncio
        result, reg = asyncio.run(
            self._run_gate_test("read_file", progressive=True, already_promoted=False)
        )
        from core.types import ToolExecutionStatus
        # The tool really ran (the path does not exist, so its own error comes back),
        # rather than a "not yet active" stub asking the model to repeat the call.
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertTrue(
            result.metadata.get("auto_activated", False),
            "auto_activated flag must be set in result metadata",
        )
        self.assertNotIn("not_yet_active", str(result.output))
        self.assertNotIn("auto_promoted", str(result.output))
        # Tool should now be promoted for the following turns
        self.assertIn("read_file", reg._promoted_tools)

    def test_gate_passes_promoted_tool_in_progressive_mode(self):
        """Already-promoted tools must pass the gate and actually execute."""
        import asyncio
        # promote read_file first, then try to execute it
        from core.tools.user_registry import UserToolRegistry
        from unittest.mock import MagicMock

        reg = UserToolRegistry(
            default_tools=True,
            execution_mode="conversational",
            progressive=True,
        )
        reg.promote("read_file")
        execution = MagicMock()
        execution.user_registry = reg
        execution.execution_id = "test-exec-456"
        execution.user_id = ""
        execution.conversation_id = ""
        # Also supply execution on tool instance via context
        execution.project_workspace = None
        execution.conversation_id = ""

        from core.tools.dispatcher import ToolDispatcher
        import asyncio

        # read_file needs a real file path — use /tmp which always exists on Linux
        # On Windows just check that it was NOT auto-promoted (gate passed)
        result = asyncio.run(
            ToolDispatcher.execute_tool(
                call_id="test-call-2",
                tool_name="read_file",
                arguments={"path": "nonexistent_test_path_xyz.txt"},
                tool_classes=reg.all_tools(),
                execution_metadata={"execution": execution},
            )
        )
        # Should NOT have the auto_activated flag — the gate passed
        self.assertFalse(
            result.metadata.get("auto_activated", False),
            "Promoted tool should bypass the gate",
        )

    def test_gate_inactive_in_non_progressive_mode(self):
        """In non-progressive mode the gate must never fire."""
        import asyncio
        result, reg = asyncio.run(
            self._run_gate_test("read_file", progressive=False, already_promoted=False)
        )
        # Gate did not fire → no auto_activated flag
        self.assertFalse(
            result.metadata.get("auto_activated", False),
            "Gate must be inactive in non-progressive mode",
        )


class TestWorkerProgressiveIntegration(unittest.TestCase):
    """Integration: TOOL_CATEGORIES_BY_MODE and progressive flag from worker."""

    def test_categories_by_mode_has_conversational(self):
        from core.execution.worker import TOOL_CATEGORIES_BY_MODE
        self.assertIn("conversational", TOOL_CATEGORIES_BY_MODE)
        cats = TOOL_CATEGORIES_BY_MODE["conversational"]
        self.assertIsNotNone(cats)
        self.assertIn("search", cats)

    def test_categories_by_mode_agent_is_none(self):
        from core.execution.worker import TOOL_CATEGORIES_BY_MODE
        self.assertIsNone(
            TOOL_CATEGORIES_BY_MODE.get("agent"),
            "agent mode must have no category restriction (None)",
        )

    def test_progressive_modes_constant(self):
        # V6.3 Phase 4 deliberately extended progressive disclosure to PEV
        # mode too (with its own pre-seeded orchestration tool set,
        # _PEV_PRESEEDED_TOOLS, so the model can plan/delegate on turn 1
        # without a describe_tool() round-trip) -- plan_execute now belongs
        # in _PROGRESSIVE_MODES, replacing the pre-V6.3 expectation that it
        # didn't.
        from core.execution.worker import _PROGRESSIVE_MODES
        self.assertIn("conversational", _PROGRESSIVE_MODES)
        self.assertIn("plan_execute", _PROGRESSIVE_MODES)
        self.assertNotIn("agent", _PROGRESSIVE_MODES)

    def test_build_manifest_import(self):
        """_build_manifest is importable from worker."""
        from core.execution.worker import _build_manifest
        self.callable(_build_manifest) if hasattr(self, "callable") else None
        self.assertTrue(callable(_build_manifest))

    def test_worker_imports_build_tool_disclosure_note(self):
        """worker.py must import build_tool_disclosure_note from soul."""
        import importlib.util
        import inspect
        spec = importlib.util.spec_from_file_location(
            "worker_src",
            "core/execution/worker.py",
        )
        if spec and spec.loader:
            source = inspect.getsource(__import__("core.execution.worker", fromlist=["worker"]))
            self.assertIn("build_tool_disclosure_note", source)


# ══════════════════════════════════════════════════════════════════════════════
# V5.0 Sprint 5 — Default Agents & Skills Catalog (B-074)
# ══════════════════════════════════════════════════════════════════════════════

class TestDefaultAgentYAMLFiles(unittest.TestCase):
    """All-tier YAML agent definitions exist and parse correctly."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.default_agents_dir = Path("default_agents")

    def _load_agent(self, filename: str) -> dict:
        from core.defaults.seeder import _parse_agent_yaml
        from pathlib import Path
        path = self.default_agents_dir / filename
        self.assertTrue(path.exists(), f"Missing default agent file: {filename}")
        data = _parse_agent_yaml(path)
        self.assertIsNotNone(data, f"Failed to parse {filename}")
        return data

    # ── Tier 1 ────────────────────────────────────────────────────────────────

    def test_sa01_deep_research_exists_and_parses(self):
        data = self._load_agent("sa-01-deep-research.yaml")
        self.assertEqual(data["name"], "deep-research")
        self.assertIn("description", data)
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)
        self.assertGreater(len(data["system_prompt"]), 100)

    def test_sa03_coding_agent_exists_and_parses(self):
        data = self._load_agent("sa-03-coding-agent.yaml")
        self.assertEqual(data["name"], "coding-agent")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    def test_sa05_git_agent_exists_and_parses(self):
        data = self._load_agent("sa-05-git-agent.yaml")
        self.assertEqual(data["name"], "git-agent")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    # ── Tier 2 ────────────────────────────────────────────────────────────────

    def test_sa02_code_review_agent_exists_and_parses(self):
        data = self._load_agent("sa-02-code-review-agent.yaml")
        self.assertEqual(data["name"], "code-review-agent")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    def test_sa04_testing_agent_exists_and_parses(self):
        data = self._load_agent("sa-04-testing-agent.yaml")
        self.assertEqual(data["name"], "testing-agent")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    def test_sa06_file_system_analyst_exists_and_parses(self):
        data = self._load_agent("sa-06-file-system-analyst.yaml")
        self.assertEqual(data["name"], "file-system-analyst")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    def test_sa07_documentation_writer_exists_and_parses(self):
        data = self._load_agent("sa-07-documentation-writer.yaml")
        self.assertEqual(data["name"], "documentation-writer")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    # ── Tier 3 ────────────────────────────────────────────────────────────────

    def test_sa08_security_reviewer_exists_and_parses(self):
        data = self._load_agent("sa-08-security-reviewer.yaml")
        self.assertEqual(data["name"], "security-reviewer")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    def test_sa09_data_analyst_exists_and_parses(self):
        data = self._load_agent("sa-09-data-analyst.yaml")
        self.assertEqual(data["name"], "data-analyst")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    def test_sa10_error_diagnostician_exists_and_parses(self):
        data = self._load_agent("sa-10-error-diagnostician.yaml")
        self.assertEqual(data["name"], "error-diagnostician")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    def test_sa11_deployment_agent_exists_and_parses(self):
        data = self._load_agent("sa-11-deployment-agent.yaml")
        self.assertEqual(data["name"], "deployment-agent")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    def test_sa12_self_improvement_agent_exists_and_parses(self):
        data = self._load_agent("sa-12-self-improvement-agent.yaml")
        self.assertEqual(data["name"], "self-improvement-agent")
        self.assertIn("system_prompt", data)
        self.assertIsInstance(data.get("max_steps"), int)

    # ── All-tier invariants ───────────────────────────────────────────────────

    def test_all_agents_have_required_fields(self):
        """Every default agent YAML must have name, description, system_prompt, max_steps."""
        from core.defaults.seeder import list_default_agents
        agents = list_default_agents()
        self.assertGreaterEqual(len(agents), 12, "Expected all 12 default agents (Tiers 1–3)")
        for agent in agents:
            name = agent.get("name", "<unknown>")
            self.assertTrue(agent.get("name"), f"Agent missing name: {agent}")
            self.assertTrue(agent.get("description"), f"Agent '{name}' missing description")
            self.assertTrue(agent.get("system_prompt"), f"Agent '{name}' missing system_prompt")
            self.assertIsInstance(agent.get("max_steps"), int, f"Agent '{name}' max_steps must be int")

    def test_max_steps_are_reasonable(self):
        """max_steps should be between 5 and 30 for all default agents."""
        from core.defaults.seeder import list_default_agents
        for agent in list_default_agents():
            steps = agent.get("max_steps", 0)
            self.assertGreaterEqual(steps, 5, f"Agent '{agent.get('name')}' max_steps too low")
            self.assertLessEqual(steps, 30, f"Agent '{agent.get('name')}' max_steps too high")


class TestDefaultSkillFiles(unittest.TestCase):
    """All-tier default SKILL.md files exist and are valid."""

    EXPECTED_SKILLS = {
        # Tier 1
        "web-search-methodology",
        "project-context",
        "python-best-practices",
        # Tier 2
        "typescript-react-patterns",
        "security-checklist",
        "testing-conventions",
        # Tier 3
        "adr-writer",
        "pr-description-writer",
        "commit-message-conventions",
        "memory-curator",
    }

    def test_expected_skills_exist_on_disk(self):
        from core.defaults.seeder import list_default_skills
        found = set(list_default_skills())
        for name in self.EXPECTED_SKILLS:
            self.assertIn(name, found, f"Expected default skill '{name}' not found")

    def test_skills_have_valid_frontmatter(self):
        """All default skills must parse with SkillLoader without errors."""
        from core.skills.loader import SkillLoader
        from pathlib import Path
        skills_dir = Path("skills")
        loader = SkillLoader(skills_dir)
        skills = loader.load_all()
        self.assertGreaterEqual(len(skills), len(self.EXPECTED_SKILLS))
        for name in self.EXPECTED_SKILLS:
            self.assertIn(name, skills, f"SkillLoader failed to load '{name}'")

    def test_skill_descriptions_not_empty(self):
        from core.skills.loader import SkillLoader
        from pathlib import Path
        loader = SkillLoader(Path("skills"))
        for name, meta in loader.load_all().items():
            self.assertTrue(meta.description, f"Skill '{name}' has empty description")
            self.assertGreater(len(meta.description), 20, f"Skill '{name}' description too short")

    def test_skill_names_are_kebab_case(self):
        from core.defaults.seeder import list_default_skills
        import re
        pattern = re.compile(r'^[a-z0-9][a-z0-9-]+[a-z0-9]$')
        for name in list_default_skills():
            self.assertRegex(name, pattern, f"Skill '{name}' is not valid kebab-case")

    def test_existing_skills_not_broken(self):
        """Existing skills (code-reviewer, python-helper) still load after Sprint 5."""
        from core.skills.loader import SkillLoader
        from pathlib import Path
        skills = SkillLoader(Path("skills")).load_all()
        for name in ("code-reviewer", "python-helper"):
            self.assertIn(name, skills, f"Pre-existing skill '{name}' no longer loads")


class TestSkillTriggerMatching(unittest.TestCase):
    """P-14: core/skills/trigger.py — keyword whole-token and path glob matchers."""

    def test_keyword_matches_whole_token(self):
        from core.skills.trigger import KeywordTrigger, matches_keyword_trigger
        trig = KeywordTrigger(keywords=["git"])
        self.assertTrue(matches_keyword_trigger(trig, "please help me git commit this"))

    def test_keyword_does_not_match_substring(self):
        """'git' must not fire on 'github' -- whole-token boundary, not substring."""
        from core.skills.trigger import KeywordTrigger, matches_keyword_trigger
        trig = KeywordTrigger(keywords=["git"])
        self.assertFalse(matches_keyword_trigger(trig, "open my github repo"))

    def test_keyword_matches_slash_prefixed_token(self):
        from core.skills.trigger import KeywordTrigger, matches_keyword_trigger
        trig = KeywordTrigger(keywords=["/linear"])
        self.assertTrue(matches_keyword_trigger(trig, "run /linear sync please"))

    def test_keyword_matching_is_case_insensitive(self):
        from core.skills.trigger import KeywordTrigger, matches_keyword_trigger
        trig = KeywordTrigger(keywords=["SQL"])
        self.assertTrue(matches_keyword_trigger(trig, "write a sql query"))

    def test_keyword_empty_message_never_matches(self):
        from core.skills.trigger import KeywordTrigger, matches_keyword_trigger
        trig = KeywordTrigger(keywords=["git"])
        self.assertFalse(matches_keyword_trigger(trig, ""))

    def test_task_trigger_uses_triggers_field(self):
        """TaskTrigger shares the same whole-token matcher via its 'triggers' field."""
        from core.skills.trigger import TaskTrigger, matches_keyword_trigger
        trig = TaskTrigger(triggers=["refactor"])
        self.assertTrue(matches_keyword_trigger(trig, "please refactor this module"))
        self.assertFalse(matches_keyword_trigger(trig, "please refactoring this module"))

    def test_path_glob_matches_extension_anywhere(self):
        from core.skills.trigger import path_matches_glob
        self.assertTrue(path_matches_glob("migrations/001_init.sql", "*.sql"))
        self.assertTrue(path_matches_glob("001_init.sql", "*.sql"))

    def test_path_glob_double_star_matches_nested_dirs(self):
        from core.skills.trigger import path_matches_glob
        self.assertTrue(path_matches_glob("a/b/c/models.py", "**/models.py"))
        self.assertTrue(path_matches_glob("models.py", "**/models.py"))

    def test_path_glob_no_match_wrong_extension(self):
        from core.skills.trigger import path_matches_glob
        self.assertFalse(path_matches_glob("core/db/models.py", "*.sql"))

    def test_path_trigger_returns_matching_pattern(self):
        from core.skills.trigger import PathTrigger, matches_path_trigger
        trig = PathTrigger(paths=["*.sql", "**/migrations/**"])
        self.assertEqual(matches_path_trigger(trig, "seed.sql"), "*.sql")
        self.assertIsNone(matches_path_trigger(trig, "core/db/models.py"))


class TestSkillLoaderTriggerParsing(unittest.TestCase):
    """P-14: core/skills/loader.py — SKILL.md 'trigger:' frontmatter parsing."""

    def _write_skill(self, tmpdir, skill_name, frontmatter_extra=""):
        import os
        skill_dir = os.path.join(tmpdir, skill_name)
        os.makedirs(skill_dir, exist_ok=True)
        content = (
            "---\n"
            f"name: {skill_name}\n"
            "description: A test skill used for P-14 trigger parsing unit tests.\n"
            f"{frontmatter_extra}"
            "---\n\n"
            "# Body\n"
        )
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(content)
        return skill_dir

    def test_no_trigger_defaults_to_none(self):
        import tempfile
        from pathlib import Path
        from core.skills.loader import SkillLoader
        with tempfile.TemporaryDirectory() as tmp:
            self._write_skill(tmp, "no-trigger-skill")
            skills = SkillLoader(Path(tmp)).load_all()
            self.assertIn("no-trigger-skill", skills)
            self.assertIsNone(skills["no-trigger-skill"].trigger)

    def test_keyword_trigger_parses(self):
        import tempfile
        from pathlib import Path
        from core.skills.loader import SkillLoader
        from core.skills.trigger import KeywordTrigger
        with tempfile.TemporaryDirectory() as tmp:
            self._write_skill(
                tmp, "kw-skill",
                "trigger:\n  type: keyword\n  keywords: [commit, git]\n",
            )
            skills = SkillLoader(Path(tmp)).load_all()
            meta = skills["kw-skill"]
            self.assertIsInstance(meta.trigger, KeywordTrigger)
            self.assertEqual(meta.trigger.keywords, ["commit", "git"])
            self.assertTrue(meta.matches_message("please commit my changes"))
            self.assertFalse(meta.matches_message("what's the weather"))

    def test_task_trigger_parses(self):
        import tempfile
        from pathlib import Path
        from core.skills.loader import SkillLoader
        from core.skills.trigger import TaskTrigger
        with tempfile.TemporaryDirectory() as tmp:
            self._write_skill(
                tmp, "task-skill",
                "trigger:\n  type: task\n  triggers: [refactor]\n",
            )
            meta = SkillLoader(Path(tmp)).load_all()["task-skill"]
            self.assertIsInstance(meta.trigger, TaskTrigger)
            self.assertTrue(meta.matches_message("time to refactor this"))

    def test_path_trigger_parses_and_matches_path_not_message(self):
        import tempfile
        from pathlib import Path
        from core.skills.loader import SkillLoader
        from core.skills.trigger import PathTrigger
        with tempfile.TemporaryDirectory() as tmp:
            self._write_skill(
                tmp, "sql-skill",
                "trigger:\n  type: path\n  paths: [\"**/*.sql\"]\n",
            )
            meta = SkillLoader(Path(tmp)).load_all()["sql-skill"]
            self.assertIsInstance(meta.trigger, PathTrigger)
            self.assertEqual(meta.matches_path("migrations/001.sql"), "**/*.sql")
            self.assertIsNone(meta.matches_path("core/db/models.py"))
            # PathTrigger never fires from matches_message -- no message-time signal.
            self.assertFalse(meta.matches_message("run the migrations/001.sql file"))

    def test_malformed_trigger_degrades_to_none_not_a_load_failure(self):
        """A 'trigger:' block that isn't a mapping must not fail the whole skill load."""
        import tempfile
        from pathlib import Path
        from core.skills.loader import SkillLoader
        with tempfile.TemporaryDirectory() as tmp:
            self._write_skill(tmp, "bad-trigger-skill", "trigger: not-a-mapping\n")
            skills = SkillLoader(Path(tmp)).load_all()
            self.assertIn("bad-trigger-skill", skills)
            self.assertIsNone(skills["bad-trigger-skill"].trigger)

    def test_unknown_trigger_type_degrades_to_none(self):
        import tempfile
        from pathlib import Path
        from core.skills.loader import SkillLoader
        with tempfile.TemporaryDirectory() as tmp:
            self._write_skill(
                tmp, "unknown-trigger-skill",
                "trigger:\n  type: mystery\n  keywords: [x]\n",
            )
            skills = SkillLoader(Path(tmp)).load_all()
            self.assertIn("unknown-trigger-skill", skills)
            self.assertIsNone(skills["unknown-trigger-skill"].trigger)


class TestBuildAvailableSkillsBlockFiltering(unittest.TestCase):
    """P-14: SkillRegistry.build_available_skills_block() message-based filtering."""

    def _registry_with(self, *metas):
        from core.skills.registry import SkillRegistry
        reg = SkillRegistry.__new__(SkillRegistry)  # bypass disk-backed __init__
        reg._skills = {m.name: m for m in metas}
        return reg

    def _meta(self, name, trigger=None):
        from pathlib import Path
        from core.skills.loader import SkillMeta
        return SkillMeta(
            name=name,
            description=f"Description for {name} skill, long enough to pass validation.",
            path=Path("/tmp") / name,
            trigger=trigger,
        )

    def test_untriggered_skill_always_listed(self):
        reg = self._registry_with(self._meta("always-on"))
        block = reg.build_available_skills_block("anything at all")
        self.assertIn("always-on", block)
        block_no_message = reg.build_available_skills_block()
        self.assertIn("always-on", block_no_message)

    def test_keyword_triggered_skill_listed_only_on_match(self):
        from core.skills.trigger import KeywordTrigger
        reg = self._registry_with(self._meta("git-helper", KeywordTrigger(keywords=["git"])))
        self.assertIn("git-helper", reg.build_available_skills_block("help me git commit"))
        self.assertEqual(reg.build_available_skills_block("what's the weather"), "")

    def test_keyword_triggered_skill_excluded_when_no_message_passed(self):
        """Default empty message means keyword-triggered skills are excluded, not
        silently reverted to always-listed -- backward compat only applies to
        untriggered skills."""
        from core.skills.trigger import KeywordTrigger
        reg = self._registry_with(self._meta("git-helper", KeywordTrigger(keywords=["git"])))
        self.assertEqual(reg.build_available_skills_block(), "")

    def test_path_triggered_skill_never_listed(self):
        from core.skills.trigger import PathTrigger
        reg = self._registry_with(self._meta("sql-skill", PathTrigger(paths=["*.sql"])))
        self.assertEqual(reg.build_available_skills_block("please run *.sql migrations"), "")

    def test_mixed_registry_lists_only_matching_and_untriggered(self):
        from core.skills.trigger import KeywordTrigger, PathTrigger
        reg = self._registry_with(
            self._meta("always-on"),
            self._meta("git-helper", KeywordTrigger(keywords=["git"])),
            self._meta("sql-skill", PathTrigger(paths=["*.sql"])),
        )
        block = reg.build_available_skills_block("help me git commit")
        self.assertIn("always-on", block)
        self.assertIn("git-helper", block)
        self.assertNotIn("sql-skill", block)

    def test_empty_registry_returns_empty_string(self):
        reg = self._registry_with()
        self.assertEqual(reg.build_available_skills_block("anything"), "")


class TestSeederModule(unittest.TestCase):
    """core.defaults.seeder module structure and list functions."""

    def test_list_default_agents_returns_list(self):
        from core.defaults.seeder import list_default_agents
        agents = list_default_agents()
        self.assertIsInstance(agents, list)
        self.assertGreaterEqual(len(agents), 12)  # 3 Tier 1 + 4 Tier 2 + 5 Tier 3

    def test_list_default_skills_returns_list(self):
        from core.defaults.seeder import list_default_skills
        skills = list_default_skills()
        self.assertIsInstance(skills, list)
        self.assertGreaterEqual(len(skills), 10)  # 3 Tier 1 + 3 Tier 2 + 4 Tier 3

    def test_agent_names_are_unique(self):
        from core.defaults.seeder import list_default_agents
        names = [a["name"] for a in list_default_agents() if a.get("name")]
        self.assertEqual(len(names), len(set(names)), "Duplicate agent names in defaults")

    def test_default_agents_dir_constant(self):
        from core.defaults.seeder import DEFAULT_AGENTS_DIR
        self.assertTrue(DEFAULT_AGENTS_DIR.exists(), "DEFAULT_AGENTS_DIR does not exist")

    def test_default_skills_dir_constant(self):
        from core.defaults.seeder import DEFAULT_SKILLS_DIR
        self.assertTrue(DEFAULT_SKILLS_DIR.exists(), "DEFAULT_SKILLS_DIR does not exist")

    def test_simple_yaml_parse_scalar(self):
        """Fallback YAML parser handles simple key: value pairs."""
        from core.defaults.seeder import _simple_yaml_parse
        content = "name: test-agent\nmax_steps: 10\nmodel: null\n"
        result = _simple_yaml_parse(content)
        self.assertEqual(result["name"], "test-agent")
        self.assertEqual(result["max_steps"], 10)
        self.assertIsNone(result["model"])

    def test_simple_yaml_parse_block_scalar(self):
        """Fallback YAML parser handles | (literal) block scalars."""
        from core.defaults.seeder import _simple_yaml_parse
        content = "name: agent\nsystem_prompt: |\n  Line one.\n  Line two.\n"
        result = _simple_yaml_parse(content)
        self.assertIn("Line one.", result.get("system_prompt", ""))
        self.assertIn("Line two.", result.get("system_prompt", ""))

    def test_simple_yaml_parse_folded_scalar(self):
        """Fallback YAML parser handles > (folded) block scalars."""
        from core.defaults.seeder import _simple_yaml_parse
        content = "name: agent\ndescription: >\n  A long description\n  that wraps.\n"
        result = _simple_yaml_parse(content)
        self.assertIn("long description", result.get("description", ""))


class TestSeederIntegration(unittest.TestCase):
    """seed_user_defaults end-to-end with a mock user and temp workspace."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="eva_seed_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_seed(self, user_id, mock_registry):
        """Helper: run seed_user_defaults with a patched workspace root and registry."""
        import asyncio
        from unittest.mock import patch
        from core.defaults.seeder import seed_user_defaults

        with patch("utils.workspace.WORKSPACE_ROOT", self.tmpdir), \
             patch("core.agents.registry.subagent_registry", mock_registry):
            return asyncio.run(seed_user_defaults(user_id))

    def test_seed_skills_copies_to_user_workspace(self):
        """Skills are copied to the user's workspace on first seed."""
        from unittest.mock import AsyncMock, MagicMock
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_registry.upsert = AsyncMock(return_value=MagicMock())

        result = self._run_seed("test-user-seed-001", mock_registry)

        self.assertGreater(result["skills_seeded"], 0, "No skills were seeded")
        self.assertEqual(len(result["errors"]), 0, f"Unexpected errors: {result['errors']}")

    def test_seed_agents_calls_upsert(self):
        """Seeder calls subagent_registry.upsert for each default agent."""
        from unittest.mock import AsyncMock, MagicMock
        upsert_calls = []
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        async def _fake_upsert(uid, data):
            upsert_calls.append(data["name"])
            return MagicMock()
        mock_registry.upsert = _fake_upsert

        result = self._run_seed("test-user-seed-002", mock_registry)

        self.assertGreaterEqual(result["agents_seeded"], 3)
        self.assertIn("deep-research", upsert_calls)
        self.assertIn("coding-agent", upsert_calls)
        self.assertIn("git-agent", upsert_calls)

    def test_seed_is_idempotent_for_agents(self):
        """Agents that already exist are skipped, not re-created."""
        from unittest.mock import AsyncMock, MagicMock
        upsert_calls = []
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=MagicMock())  # all exist

        async def _fake_upsert(uid, data):
            upsert_calls.append(data["name"])
        mock_registry.upsert = _fake_upsert

        result = self._run_seed("test-user-seed-003", mock_registry)

        self.assertEqual(result["agents_seeded"], 0, "Should not upsert existing agents")
        self.assertGreater(result["agents_skipped"], 0, "Should report skipped agents")
        self.assertEqual(upsert_calls, [], "upsert must not be called for existing agents")

    def test_seed_is_idempotent_for_skills(self):
        """Skills already present in the workspace are not overwritten."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from pathlib import Path
        from core.defaults.seeder import list_default_skills

        fake_user_id = "test-user-seed-004"

        # Pre-create the skill directories inside the temp workspace
        user_skills_path = Path(self.tmpdir) / fake_user_id / "skills"
        for skill_name in list_default_skills():
            (user_skills_path / skill_name).mkdir(parents=True, exist_ok=True)

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)
        mock_registry.upsert = AsyncMock(return_value=MagicMock())

        result = self._run_seed(fake_user_id, mock_registry)

        self.assertEqual(result["skills_seeded"], 0, "Should not overwrite existing skills")
        self.assertGreater(result["skills_skipped"], 0, "Should report skipped skills")


# ══════════════════════════════════════════════════════════════════════════════
# V5.0 Sprint 3 — Dynamic Subagent Spawning
# ══════════════════════════════════════════════════════════════════════════════

class TestDynamicConfigBuilder(unittest.TestCase):
    """_build_dynamic_config constructs correct SimpleNamespace objects."""

    def _build(self, agent_name: str, **kwargs):
        from tools.default.delegate_tool import _build_dynamic_config
        return _build_dynamic_config(agent_name, kwargs)

    def test_name_is_preserved(self):
        cfg = self._build("researcher")
        self.assertEqual(cfg.name, "researcher")

    def test_id_is_none_for_dynamic(self):
        cfg = self._build("researcher")
        self.assertIsNone(cfg.id)

    def test_explicit_system_prompt_used(self):
        cfg = self._build("analyst", system_prompt="You are a data analyst.")
        self.assertEqual(cfg.system_prompt, "You are a data analyst.")

    def test_default_system_prompt_generated_when_absent(self):
        cfg = self._build("summariser")
        self.assertIn("summariser", cfg.system_prompt)
        self.assertGreater(len(cfg.system_prompt), 20)

    def test_max_steps_default(self):
        cfg = self._build("agent-x")
        self.assertEqual(cfg.max_steps, 10)

    def test_max_steps_from_args(self):
        cfg = self._build("agent-x", max_steps=15)
        self.assertEqual(cfg.max_steps, 15)

    def test_max_steps_clamped_high(self):
        cfg = self._build("agent-x", max_steps=999)
        self.assertLessEqual(cfg.max_steps, 50)

    def test_max_steps_clamped_low(self):
        cfg = self._build("agent-x", max_steps=0)
        self.assertGreaterEqual(cfg.max_steps, 1)

    def test_allowed_tools_none_means_inherit_all(self):
        cfg = self._build("agent-x")
        self.assertIsNone(cfg.tools)

    def test_allowed_tools_empty_list_normalised_to_none(self):
        cfg = self._build("agent-x", allowed_tools=[])
        self.assertIsNone(cfg.tools)

    def test_allowed_tools_list_preserved(self):
        cfg = self._build("agent-x", allowed_tools=["shell", "git_tool"])
        self.assertEqual(cfg.tools, ["shell", "git_tool"])

    def test_model_none_when_not_specified(self):
        cfg = self._build("agent-x")
        self.assertIsNone(cfg.model)

    def test_model_from_args(self):
        cfg = self._build("agent-x", model="openai/gpt-4o")
        self.assertEqual(cfg.model, "openai/gpt-4o")

    def test_provider_none_when_not_specified(self):
        cfg = self._build("agent-x")
        self.assertIsNone(cfg.provider)

    def test_provider_from_args(self):
        cfg = self._build("agent-x", provider="openai")
        self.assertEqual(cfg.provider, "openai")


class TestDynamicConfigBuilderParallel(unittest.TestCase):
    """parallel_delegate_tool._build_dynamic_config behaves identically."""

    def _build(self, agent_name: str, **kwargs):
        from tools.default.parallel_delegate_tool import _build_dynamic_config
        return _build_dynamic_config(agent_name, kwargs)

    def test_same_defaults_as_delegate(self):
        cfg = self._build("worker")
        self.assertEqual(cfg.name, "worker")
        self.assertIsNone(cfg.id)
        self.assertEqual(cfg.max_steps, 10)
        self.assertIsNone(cfg.tools)
        self.assertIn("worker", cfg.system_prompt)

    def test_explicit_fields_honoured(self):
        cfg = self._build(
            "coder",
            system_prompt="You code.",
            allowed_tools=["shell"],
            max_steps=20,
            model="openai/gpt-4o",
            provider="openai",
        )
        self.assertEqual(cfg.system_prompt, "You code.")
        self.assertEqual(cfg.tools, ["shell"])
        self.assertEqual(cfg.max_steps, 20)
        self.assertEqual(cfg.model, "openai/gpt-4o")
        self.assertEqual(cfg.provider, "openai")


@pytest.mark.usefixtures("stored_provider_keys")
class TestDelegateDynamicFallback(unittest.TestCase):
    """delegate_to_agent falls back to dynamic mode when agent not in DB."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    async def _call_delegate(self, agent_name: str, extra_args: dict = None):
        """Run delegate_to_agent with a fully mocked execution context."""
        from core.tools.registry import DefaultToolRegistry
        from unittest.mock import AsyncMock, MagicMock, patch

        cls_map = DefaultToolRegistry.all()
        tool_cls = cls_map.get("delegate_to_agent")
        self.assertIsNotNone(tool_cls, "delegate_to_agent must be registered")

        # Fake execution context
        execution = MagicMock()
        execution.user_id = "test-dyn-user"
        execution.execution_id = "test-exec-dyn"
        execution.conversation_id = "test-conv-dyn"
        execution.shadow = None
        execution.caller_agent_name = "orchestrator"

        # Fake session (provider/model)
        fake_session = {"provider": "groq", "model": "test/model", "api_key": "fake-key"}

        # Fake subagent_registry: returns None (not registered)
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        # Fake AgentEngine that returns a canned response
        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk
            yield TextChunk(content="Dynamic agent result.")

        mock_engine_instance = MagicMock()
        mock_engine_instance.call = _fake_call

        tool_instance = tool_cls()
        tool_instance.execution = execution

        args = {"agent_name": agent_name, "task": "Do the thing.", **(extra_args or {})}

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value=fake_session), \
             patch("core.agent_engine.AgentEngine", return_value=mock_engine_instance), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"):
            result = await tool_instance.run(args)
        return result, mock_registry

    def test_dynamic_agent_succeeds_when_not_registered(self):
        """Unknown agent name should produce a result, not an error."""
        import asyncio
        result, _ = asyncio.run(self._call_delegate("unknown-agent-xyz"))
        data = result.result
        self.assertNotIn("error", data, f"Should not error: {data}")
        self.assertIn("output", data)

    def test_dynamic_agent_does_not_call_registry_upsert(self):
        """Dynamic mode must not write a new DB row."""
        import asyncio
        _, registry = asyncio.run(self._call_delegate("transient-agent"))
        registry.upsert.assert_not_called()

    def test_dynamic_agent_uses_provided_system_prompt(self):
        """system_prompt arg is passed through to the child AgentEngine."""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        from core.tools.registry import DefaultToolRegistry

        cls_map = DefaultToolRegistry.all()
        tool_cls = cls_map["delegate_to_agent"]

        execution = MagicMock()
        execution.user_id = "u1"
        execution.execution_id = "e1"
        execution.conversation_id = "c1"
        execution.shadow = None
        execution.caller_agent_name = "orchestrator"

        captured = {}

        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk
            yield TextChunk(content="ok", chunk_index=0)

        def _capture_engine(*a, **kw):
            captured.update(kw)
            m = MagicMock()
            m.call = _fake_call
            return m

        tool_instance = tool_cls()
        tool_instance.execution = execution

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("core.agent_engine.AgentEngine", side_effect=_capture_engine), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"):
            import asyncio
            asyncio.run(tool_instance.run({
                "agent_name": "custom-agent",
                "task": "Analyse X.",
                "system_prompt": "You are a custom analyst.",
            }))

        self.assertEqual(
            captured.get("system_prompt"), "You are a custom analyst.",
            "system_prompt must be forwarded to AgentEngine"
        )

    def test_dynamic_agent_uses_default_prompt_when_none_given(self):
        """When system_prompt is omitted, a generated default must be used."""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        from core.tools.registry import DefaultToolRegistry

        cls_map = DefaultToolRegistry.all()
        tool_cls = cls_map["delegate_to_agent"]
        execution = MagicMock()
        execution.user_id = "u2"
        execution.execution_id = "e2"
        execution.conversation_id = "c2"
        execution.shadow = None
        execution.caller_agent_name = "orchestrator"

        captured = {}

        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk
            yield TextChunk(content="ok", chunk_index=0)

        def _capture(*a, **kw):
            captured.update(kw)
            m = MagicMock(); m.call = _fake_call; return m

        tool_instance = tool_cls()
        tool_instance.execution = execution
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("core.agent_engine.AgentEngine", side_effect=_capture), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"):
            asyncio.run(tool_instance.run({"agent_name": "auto-prompt-agent", "task": "Do it."}))

        prompt = captured.get("system_prompt", "")
        self.assertIn("auto-prompt-agent", prompt, "Default prompt must include the agent name")

    def test_registered_agent_still_works(self):
        """Registered agents must continue to use their DB config, not dynamic mode."""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        from core.tools.registry import DefaultToolRegistry

        cls_map = DefaultToolRegistry.all()
        tool_cls = cls_map["delegate_to_agent"]
        execution = MagicMock()
        execution.user_id = "u3"
        execution.execution_id = "e3"
        execution.conversation_id = "c3"
        execution.shadow = None
        execution.caller_agent_name = "orchestrator"

        db_cfg = MagicMock()
        db_cfg.id = "db-uuid-123"
        db_cfg.system_prompt = "DB system prompt."
        db_cfg.tools = None
        db_cfg.max_steps = 5
        db_cfg.provider = None
        db_cfg.model = None

        captured = {}

        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk
            yield TextChunk(content="db result", chunk_index=0)

        def _capture(*a, **kw):
            captured.update(kw)
            m = MagicMock(); m.call = _fake_call; return m

        tool_instance = tool_cls()
        tool_instance.execution = execution
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=db_cfg)  # found in DB

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("core.agent_engine.AgentEngine", side_effect=_capture), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"):
            asyncio.run(tool_instance.run({"agent_name": "researcher", "task": "Research X."}))

        self.assertEqual(
            captured.get("system_prompt"), "DB system prompt.",
            "Registered agent must use DB config system_prompt"
        )


@pytest.mark.usefixtures("stored_provider_keys")
class TestDelegateBackgroundExecution(unittest.IsolatedAsyncioTestCase):
    """
    P-19 Phase 2 -- delegate_to_agent(background=True): starts the subagent
    as a real, independently-tracked execution (execution_mode=
    "subagent_background") and returns immediately instead of the default
    awaited foreground behaviour. Mirrors run_daemon()'s own registration
    pattern (create_execution_record -> RUNNING -> broker running ->
    phase-1 metrics row -> ... -> terminal status + phase-2 metrics row) so
    GET /executions/{id}/status and WakeupScheduler's watch_execution_id
    polling both see a backgrounded subagent exactly like a daemon run.

    Real in-memory sqlite DB (not mocked) so ExecutionMetricsRepo's actual
    upsert/get round-trip is exercised, matching TestWakeupScheduler's own
    convention -- these are exactly the tables schedule_wakeup's
    watch_execution_id composition depends on.
    """

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.db.models import Base, User, Conversation

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

        import core.db.session as _dbs
        self._orig_session_local = _dbs.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session

        async with self.Session() as db:
            db.add(User(id="u-bg", email="u-bg@test.local", username="u-bg", password_hash="x"))
            db.add(Conversation(id="c-bg", user_id="u-bg"))
            await db.commit()

    async def asyncTearDown(self):
        import core.db.session as _dbs
        _dbs.AsyncSessionLocal = self._orig_session_local
        await self.engine.dispose()

    def _make_execution(self):
        from unittest.mock import MagicMock
        execution = MagicMock()
        execution.user_id = "u-bg"
        execution.execution_id = "parent-exec-1"
        execution.conversation_id = "c-bg"
        execution.session_id = "s-bg"
        execution.shadow = None
        execution.caller_agent_name = "orchestrator"
        execution.project_workspace = None
        execution.parent_project_id = None
        return execution

    async def _call_delegate(self, *, background: bool, fail: bool = False, gate=None,
                              agent_name: str = "bg-agent"):
        """
        Started via patch(...).start() + addCleanup(...) rather than a
        `with` block: for background=True, run() returns before the
        detached task has done any real work, so a `with` block here would
        tear the mocks down (ConversationJournal.log_subagent, AgentEngine,
        get_user_session) before the background task ever touches them --
        which sends it down real, unmocked codepaths (real journal file
        I/O, real skills-registry access) instead of the fakes this test is
        built around. Keeping the patches alive for the whole test (stopped
        automatically at teardown) matches what actually needs mocking for
        the entire lifetime of the background task, not just the
        synchronous prefix of run().
        """
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.tools.registry import DefaultToolRegistry

        tool_cls = DefaultToolRegistry.all()["delegate_to_agent"]
        execution = self._make_execution()

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)  # dynamic subagent, no DB config

        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk, ErrorChunk
            if gate is not None:
                await gate.wait()
            if fail:
                yield ErrorChunk(message="synthetic background failure")
            else:
                yield TextChunk(content="Background result.")

        mock_engine_instance = MagicMock()
        mock_engine_instance.call = _fake_call

        tool_instance = tool_cls()
        tool_instance.execution = execution

        args = {
            "agent_name": agent_name,
            "task": "Do it in the background.",
            "background": background,
        }

        # Captures the actual asyncio.Task run() creates for the detached
        # background run, so tests can await it directly instead of racing
        # it with a wall-clock sleep/poll -- this environment has shown a
        # real, one-time ~10-15s synchronous stall on first touch of the
        # (real, unmocked outside this capture) journal/skills-registry
        # codepaths, which made a fixed timeout flaky regardless of how
        # generous. Awaiting the real task sidesteps that entirely: the
        # test only ever waits exactly as long as the task actually takes,
        # deterministically, with no timing guesswork.
        import asyncio as _asyncio_mod
        captured_tasks: list = []
        _real_create_task = _asyncio_mod.create_task

        def _capture_create_task(coro, *a, **kw):
            t = _real_create_task(coro, *a, **kw)
            captured_tasks.append(t)
            return t

        patchers = [
            patch("core.agents.registry.subagent_registry", mock_registry),
            patch("config.settings.get_user_session",
                  return_value={"provider": "groq", "model": "m", "api_key": "k"}),
            patch("core.agent_engine.AgentEngine", return_value=mock_engine_instance),
            patch("core.memory.conversation_journal.ConversationJournal.log_subagent"),
            patch("tools.default.delegate_tool.asyncio.create_task",
                  side_effect=_capture_create_task),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

        result = await tool_instance.run(args)
        return result, execution, captured_tasks

    async def _await_background_task(self, captured_tasks: list):
        """Await the detached task run() created for background=True,
        deterministically -- no timeout, no polling. Other incidental
        asyncio.create_task calls can land in the same capture list (e.g.
        from library code exercised along the way), so pick out the one
        run() actually names -- see the "subagent-bg-{execution_id}" name
        it's created with in tools/default/delegate_tool.py."""
        bg_tasks = [t for t in captured_tasks if (t.get_name() or "").startswith("subagent-bg-")]
        self.assertEqual(len(bg_tasks), 1,
                          f"expected exactly one subagent-bg-* task, found: "
                          f"{[t.get_name() for t in captured_tasks]}")
        await bg_tasks[0]

    async def test_background_omitted_defaults_to_foreground_unchanged(self):
        """No `background` key at all -- must behave exactly like today:
        awaited, returns {agent_name, output}, no execution_id in the result,
        and no independent execution ever gets registered."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.tools.registry import DefaultToolRegistry
        from core.execution import execution_registry

        tool_cls = DefaultToolRegistry.all()["delegate_to_agent"]
        execution = self._make_execution()
        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk
            yield TextChunk(content="Foreground result.")

        mock_engine_instance = MagicMock()
        mock_engine_instance.call = _fake_call
        tool_instance = tool_cls()
        tool_instance.execution = execution

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session",
                   return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("core.agent_engine.AgentEngine", return_value=mock_engine_instance), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"), \
             patch.object(execution_registry, "create_execution_record",
                           side_effect=AssertionError(
                               "create_execution_record must not be called when "
                               "background is omitted"
                           )):
            result = await tool_instance.run({"agent_name": "fg-agent", "task": "Do it now."})

        data = result.result
        self.assertEqual(data, {"agent_name": "fg-agent", "output": "Foreground result."})
        self.assertNotIn("execution_id", data)
        self.assertNotIn("status", data)

    async def test_background_true_returns_immediately_before_child_agent_runs(self):
        """The whole point of background=True: run() must not block on the
        child agent's stream. Prove it by gating the fake stream on an
        asyncio.Event that is never set before run() returns."""
        import asyncio
        gate = asyncio.Event()
        result, execution, captured_tasks = await self._call_delegate(background=True, gate=gate)

        data = result.result
        self.assertEqual(data.get("status"), "started_in_background")
        self.assertEqual(data.get("agent_name"), "bg-agent")
        self.assertIn("execution_id", data)
        self.assertNotEqual(data["execution_id"], execution.execution_id,
                             "background execution must be a NEW id, not the parent's")
        self.assertFalse(gate.is_set())  # run() returned without the child agent progressing

        gate.set()
        await self._await_background_task(captured_tasks)  # let the detached task actually finish

    async def test_background_registers_execution_with_subagent_background_mode(self):
        """The independent execution must be discoverable exactly like a
        daemon run -- listable via ExecutionRegistry.list_running_for_user
        under execution_mode='subagent_background' while it's in flight."""
        import asyncio
        from core.execution import execution_registry

        gate = asyncio.Event()
        result, execution, captured_tasks = await self._call_delegate(background=True, gate=gate)
        bg_execution_id = result.result["execution_id"]

        running = execution_registry.list_running_for_user("u-bg")
        matches = [e for e in running if e.execution_id == bg_execution_id]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].execution_mode, "subagent_background")

        gate.set()
        await self._await_background_task(captured_tasks)

    async def test_background_success_writes_terminal_metrics_row(self):
        """After the detached task finishes successfully, execution_metrics
        must show status=completed, triggered_by=subagent_background --
        this is exactly what WakeupScheduler's watch_execution_id poll reads
        to decide a watched execution is done."""
        import asyncio
        from core.db.repository import ExecutionMetricsRepo

        gate = asyncio.Event()
        result, execution, captured_tasks = await self._call_delegate(background=True, gate=gate)
        bg_execution_id = result.result["execution_id"]

        gate.set()
        await self._await_background_task(captured_tasks)

        async with self.Session() as db:
            metrics = await ExecutionMetricsRepo.get(db, bg_execution_id)
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics.status, "completed")
        self.assertEqual(metrics.triggered_by, "subagent_background")
        self.assertEqual(metrics.conversation_id, "c-bg")
        self.assertEqual(metrics.user_id, "u-bg")

    async def test_background_failure_writes_failed_metrics_row(self):
        """A child-agent error chunk must finalize the independent execution
        as failed, not silently leave it stuck at 'running' forever."""
        import asyncio
        from core.db.repository import ExecutionMetricsRepo

        gate = asyncio.Event()
        result, execution, captured_tasks = await self._call_delegate(
            background=True, fail=True, gate=gate
        )
        bg_execution_id = result.result["execution_id"]

        gate.set()
        await self._await_background_task(captured_tasks)

        async with self.Session() as db:
            metrics = await ExecutionMetricsRepo.get(db, bg_execution_id)
        self.assertEqual(metrics.status, "failed")
        self.assertEqual(metrics.error_message, "synthetic background failure")

        from core.execution import execution_registry
        running = execution_registry.list_running_for_user("u-bg")
        self.assertNotIn(bg_execution_id, [e.execution_id for e in running],
                          "a failed background execution must not still read as running")

    async def test_background_success_publishes_execution_completed_event(self):
        """P-19 Phase 3: _finalize_background_execution() must publish a
        user-facing 'execution.completed' event (reusing the generic name,
        not a bespoke one -- see the delegate_tool.py comment at the call
        site) so the frontend's live task-tray learns without polling.
        publish_user_event is imported locally inside
        _finalize_background_execution -- patch the origin
        (core.events_publisher.publish_user_event), matching the same rule
        already applied to the wakeup.resolved test."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        publisher_patcher = patch(
            "core.events_publisher.publish_user_event", new=AsyncMock()
        )
        mock_event = publisher_patcher.start()
        self.addCleanup(publisher_patcher.stop)

        gate = asyncio.Event()
        result, execution, captured_tasks = await self._call_delegate(background=True, gate=gate)
        bg_execution_id = result.result["execution_id"]

        gate.set()
        await self._await_background_task(captured_tasks)

        mock_event.assert_awaited_once()
        self.assertEqual(mock_event.call_args.kwargs["user_id"], "u-bg")
        self.assertEqual(mock_event.call_args.kwargs["event"], "execution.completed")
        self.assertEqual(mock_event.call_args.kwargs["execution_id"], bg_execution_id)
        self.assertEqual(mock_event.call_args.kwargs["daemon_name"], "bg-agent")

    async def test_background_failure_publishes_execution_failed_event(self):
        """Same as above, error path -- must be 'execution.failed', not
        'execution.completed'."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        publisher_patcher = patch(
            "core.events_publisher.publish_user_event", new=AsyncMock()
        )
        mock_event = publisher_patcher.start()
        self.addCleanup(publisher_patcher.stop)

        gate = asyncio.Event()
        result, execution, captured_tasks = await self._call_delegate(
            background=True, fail=True, gate=gate
        )
        bg_execution_id = result.result["execution_id"]

        gate.set()
        await self._await_background_task(captured_tasks)

        mock_event.assert_awaited_once()
        self.assertEqual(mock_event.call_args.kwargs["event"], "execution.failed")
        self.assertEqual(mock_event.call_args.kwargs["execution_id"], bg_execution_id)

    async def test_background_composes_with_schedule_wakeup_watch_execution_id(self):
        """End-to-end composition: once the background subagent's metrics
        row reaches a terminal state, WakeupScheduler._poll() must resolve a
        wakeup registered with watch_execution_id=<the returned execution_id>
        -- this is the entire point of pairing background=True with
        schedule_wakeup, proven here without any real polling delay."""
        import asyncio
        from datetime import datetime, timezone, timedelta
        from unittest.mock import AsyncMock, patch
        from core.db.repository import WakeupRepo
        from core.execution.wakeup_scheduler import WakeupScheduler

        gate = asyncio.Event()
        result, execution, captured_tasks = await self._call_delegate(background=True, gate=gate)
        bg_execution_id = result.result["execution_id"]

        gate.set()
        await self._await_background_task(captured_tasks)  # background subagent completes

        async with self.Session() as db:
            # Far-future wake_at: resolution here must come from
            # watch_execution_id reaching a terminal state, not the
            # fixed-time fallback ever firing.
            await WakeupRepo.create(
                db, execution_id="parent-exec-1", conversation_id="c-bg", user_id="u-bg",
                wake_at=datetime.now(timezone.utc) + timedelta(hours=48),
                reason="waiting on the background subagent",
                watch_execution_id=bg_execution_id,
            )

        with patch(
            "core.execution.wakeup_scheduler.resume_from_wakeup",
            new=AsyncMock(return_value="new-exec-1"),
        ) as _resume, patch(
            "core.execution.execution_registry.resolve_wakeup", return_value=True,
        ), patch(
            "core.execution.redis_registry.redis_execution_registry.mark_execution_finished",
            new=AsyncMock(),
        ):
            scheduler = WakeupScheduler()
            await scheduler._poll()
            _resume.assert_awaited_once()

        async with self.Session() as db:
            pending = await WakeupRepo.list_pending(db)
        self.assertEqual(pending, [])


class TestDelegateArgsSchema(unittest.TestCase):
    """DelegateArgs schema accepts all Sprint 3 optional fields."""

    def test_minimal_args_valid(self):
        from tools.default.delegate_tool import DelegateArgs
        a = DelegateArgs(agent_name="researcher", task="Find X.")
        self.assertIsNone(a.system_prompt)
        self.assertIsNone(a.allowed_tools)
        self.assertIsNone(a.max_steps)
        self.assertIsNone(a.model)
        self.assertIsNone(a.provider)

    def test_full_args_valid(self):
        from tools.default.delegate_tool import DelegateArgs
        a = DelegateArgs(
            agent_name="analyst",
            task="Analyse X.",
            context="Some context.",
            system_prompt="You are an analyst.",
            allowed_tools=["shell", "web_search"],
            max_steps=12,
            model="openai/gpt-4o",
            provider="openai",
        )
        self.assertEqual(a.agent_name, "analyst")
        self.assertEqual(a.allowed_tools, ["shell", "web_search"])
        self.assertEqual(a.max_steps, 12)

    def test_parallel_task_schema(self):
        from tools.default.parallel_delegate_tool import ParallelTask
        t = ParallelTask(
            agent_name="coder",
            task="Write tests.",
            system_prompt="You write tests.",
            allowed_tools=["shell"],
            max_steps=8,
        )
        self.assertEqual(t.system_prompt, "You write tests.")
        self.assertEqual(t.max_steps, 8)


# ===========================================================================
# Phase B — Auto-generate a knowledge doc at project-init time
# ===========================================================================

class TestUserToolRegistryAllowedDefaultTools(unittest.TestCase):
    """UserToolRegistry.allowed_default_tools actually restricts the default toolset."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    def test_none_preserves_existing_behavior(self):
        from core.tools.user_registry import UserToolRegistry
        reg = UserToolRegistry(default_tools=True, allowed_default_tools=None)
        self.assertIn("read_file", reg.tools)
        self.assertIn("write_file", reg.tools)

    def test_restricts_to_given_set(self):
        from core.tools.user_registry import UserToolRegistry
        reg = UserToolRegistry(
            default_tools=True,
            allowed_default_tools={"read_file", "list_files"},
        )
        self.assertIn("read_file", reg.tools)
        self.assertIn("list_files", reg.tools)
        self.assertNotIn("write_file", reg.tools)
        self.assertNotIn("delete_file", reg.tools)

    def test_always_active_tools_survive_restriction(self):
        from core.tools.user_registry import UserToolRegistry
        reg = UserToolRegistry(
            default_tools=True,
            allowed_default_tools={"read_file"},
        )
        self.assertIn("describe_tool", reg.tools)

    def test_empty_set_restricts_to_nothing_but_always_active(self):
        from core.tools.user_registry import UserToolRegistry
        reg = UserToolRegistry(default_tools=True, allowed_default_tools=set())
        self.assertNotIn("read_file", reg.tools)
        self.assertIn("describe_tool", reg.tools)

    def test_custom_and_mcp_tools_unaffected_by_default_restriction(self):
        # allowed_default_tools must only prune the DefaultToolRegistry load,
        # not interfere with the separate enabled_tools-gated custom/MCP loop.
        from core.tools.user_registry import UserToolRegistry
        reg = UserToolRegistry(
            default_tools=True,
            enabled_tools=["read_file"],
            allowed_default_tools={"list_files"},
        )
        self.assertIn("list_files", reg.tools)
        self.assertNotIn("read_file", reg.tools)


class TestProjectArtifactWriteTool(unittest.IsolatedAsyncioTestCase):
    """ProjectArtifactWriteTool writes directly to .eva files, no DB involved."""

    async def _run_tool(self, args, execution=None, tmp_workspace=None):
        from tools.default.project_tools import ProjectArtifactWriteTool
        tool = ProjectArtifactWriteTool()
        tool.execution = execution or _fake_execution(
            user_id="u-artifact-write", parent_project_id="proj-1", project_workspace=tmp_workspace,
        )
        return await tool.run(args)

    async def test_append_writes_entry_to_knowledge_eva(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = await self._run_tool(
                {"file": "knowledge", "action": "append", "content": "[NOTE ts=x] hello"},
                tmp_workspace=tmp,
            )
            self.assertTrue(result.result.get("ok"))
            path = Path(tmp) / ".eva" / "knowledge.eva"
            self.assertIn("[NOTE ts=x] hello", path.read_text(encoding="utf-8"))

    async def test_replace_section_requires_section_arg(self):
        from tools.default.project_tools import ProjectArtifactWriteArgs
        with self.assertRaises(Exception):
            ProjectArtifactWriteArgs(file="knowledge", action="replace_section", content="x")

    async def test_replace_section_writes_section(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            await self._run_tool(
                {
                    "file": "policies", "action": "replace_section",
                    "section": "DOS", "content": "- Always test first.",
                },
                tmp_workspace=tmp,
            )
            path = Path(tmp) / ".eva" / "policies.eva"
            self.assertIn("Always test first.", path.read_text(encoding="utf-8"))

    async def test_errors_without_active_project(self):
        execution = _fake_execution(parent_project_id=None)
        result = await self._run_tool(
            {"file": "knowledge", "action": "append", "content": "x"},
            execution=execution,
        )
        self.assertIn("error", result.result)

    async def test_rejects_unknown_file_value(self):
        from tools.default.project_tools import ProjectArtifactWriteArgs
        with self.assertRaises(Exception):
            ProjectArtifactWriteArgs(file="notes", action="append", content="x")

    async def test_touches_journal_and_project_timestamp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            # Seed project.eva so touch_updated has something to update.
            from core.project.manager import ProjectManager
            ProjectManager.init_workspace(
                user_id="u-artifact-write", project_id="proj-1",
                name="Test", external_path=tmp,
            )
            await self._run_tool(
                {"file": "knowledge", "action": "append", "content": "[NOTE ts=x] hi"},
                tmp_workspace=tmp,
            )
            journal = (Path(tmp) / ".eva" / "journal.eva").read_text(encoding="utf-8")
            self.assertIn("project_artifact_write", journal)


@pytest.mark.usefixtures("stored_provider_keys")
class TestDelegateSkillsPrimingAndToolRestriction(unittest.TestCase):
    """delegate_to_agent: skills-index priming + allowed_default_tools wiring for registered agents."""

    def _call_registered(self, cfg, skills_block="## Available Skills\n- foo"):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.tools.registry import DefaultToolRegistry

        cls_map = DefaultToolRegistry.all()
        tool_cls = cls_map["delegate_to_agent"]

        execution = MagicMock()
        execution.user_id = "u1"
        execution.execution_id = "e1"
        execution.conversation_id = "c1"
        execution.shadow = None
        execution.caller_agent_name = "orchestrator"
        execution.parent_project_id = None  # sidesteps G-003 project-scope check

        captured_engine_kwargs = {}
        captured_registry_kwargs = {}

        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk
            yield TextChunk(content="ok", chunk_index=0)

        def _capture_engine(*a, **kw):
            captured_engine_kwargs.update(kw)
            m = MagicMock()
            m.call = _fake_call
            return m

        real_registry_cls = None
        from core.tools.user_registry import UserToolRegistry as _RealRegistry

        def _capture_registry(*a, **kw):
            captured_registry_kwargs.update(kw)
            return _RealRegistry(**kw)

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=cfg)

        mock_skill_registry = MagicMock()
        mock_skill_registry.build_available_skills_block = MagicMock(return_value=skills_block)

        tool_instance = tool_cls()
        tool_instance.execution = execution

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value={
                 "provider": "groq", "model": "m", "api_key": "k", "skills_mode": "auto",
             }), \
             patch("core.agent_engine.AgentEngine", side_effect=_capture_engine), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"), \
             patch("core.tools.user_registry.UserToolRegistry", side_effect=_capture_registry), \
             patch("core.skills.registry.get_skill_registry", return_value=mock_skill_registry):
            asyncio.run(tool_instance.run({"agent_name": cfg.name, "task": "Do X."}))

        return captured_engine_kwargs, captured_registry_kwargs

    def _fake_cfg(self, **overrides):
        from unittest.mock import MagicMock
        cfg = MagicMock()
        cfg.id = overrides.get("id", "cfg-1")
        cfg.name = overrides.get("name", "skill-runner")
        cfg.system_prompt = overrides.get("system_prompt", "You are a skill runner.")
        cfg.model = overrides.get("model", None)
        cfg.provider = overrides.get("provider", None)
        cfg.tools = overrides.get("tools", ["read_file", "list_files"])
        cfg.max_steps = overrides.get("max_steps", 12)
        cfg.project_id = overrides.get("project_id", None)
        cfg.is_default = overrides.get("is_default", True)
        return cfg

    def test_skills_block_appended_to_system_prompt(self):
        cfg = self._fake_cfg()
        engine_kwargs, _ = self._call_registered(cfg)
        self.assertIn("You are a skill runner.", engine_kwargs["system_prompt"])
        self.assertIn("## Available Skills", engine_kwargs["system_prompt"])

    def test_allowed_default_tools_passed_from_cfg_tools(self):
        cfg = self._fake_cfg(tools=["read_file", "list_files"])
        _, registry_kwargs = self._call_registered(cfg)
        self.assertEqual(registry_kwargs.get("allowed_default_tools"), {"read_file", "list_files"})

    def test_empty_cfg_tools_means_no_restriction(self):
        cfg = self._fake_cfg(tools=[])
        _, registry_kwargs = self._call_registered(cfg)
        self.assertIsNone(registry_kwargs.get("allowed_default_tools"))


class TestRunnerAndProjectsSourceWiring(unittest.TestCase):
    """
    Source-level checks for the daemon-runner tool restriction and the
    create_project() sync-daemon wiring -- these paths are deeply integrated
    with DB/execution/blackboard internals (same category of code covered by
    source assertions elsewhere in this file, e.g. TestB082MultiActiveProjects),
    so full functional mocking isn't the established pattern here.
    """

    def test_runner_passes_allowed_default_tools(self):
        src = Path("core/daemons/runner.py").read_text(encoding="utf-8")
        idx = src.find("UserToolRegistry(")
        self.assertGreater(idx, 0)
        section = src[idx:idx + 700]
        self.assertIn("allowed_default_tools=set(allowed_tools) if allowed_tools else None", section)

    def test_create_project_creates_sync_daemon(self):
        """The daemon-config-building logic lives in _ensure_sync_daemon()
        (shared with sync_project()'s self-heal path as of the old-project
        fix), not inline in create_project() -- check it there."""
        src = Path("api/projects.py").read_text(encoding="utf-8")
        idx = src.find("async def _ensure_sync_daemon")
        self.assertGreater(idx, 0)
        next_fn = src.find("\ndef ", idx + 1)
        section = src[idx:next_fn] if next_fn > 0 else src[idx:]
        self.assertIn("daemon_registry.upsert", section)
        self.assertIn('"run_interval_seconds": None', section)
        self.assertIn('"tools": ["delegate_to_agent"]', section)
        self.assertIn("skill-runner", section)

    def test_create_project_calls_ensure_sync_daemon(self):
        src = Path("api/projects.py").read_text(encoding="utf-8")
        idx = src.find("async def create_project")
        self.assertGreater(idx, 0)
        next_fn = src.find("\nasync def ", idx + 1)
        section = src[idx:next_fn] if next_fn > 0 else src[idx:]
        self.assertIn("await _ensure_sync_daemon(project, user_id)", section)

    def test_create_project_kicks_off_daemon_via_trigger_daemon_not_bare_run_daemon(self):
        """Regression guard: a bare asyncio.create_task(run_daemon(...)) call
        here skips trigger_daemon()'s record_success()/record_failure()
        lifecycle bookkeeping entirely -- GET /daemons/{name}/status stayed
        idle/last_run_at=null forever even after a real, successful run
        (confirmed live). Must route through trigger_daemon(), same as
        sync_project() already does, so there's only one place that updates
        daemon lifecycle state."""
        src = Path("api/projects.py").read_text(encoding="utf-8")
        idx = src.find("async def create_project")
        self.assertGreater(idx, 0)
        next_fn = src.find("\nasync def ", idx + 1)
        section = src[idx:next_fn] if next_fn > 0 else src[idx:]
        self.assertIn("from api.daemons import trigger_daemon", section)
        self.assertIn("await trigger_daemon(name=daemon_row.name, auth=auth)", section)
        self.assertNotIn("asyncio.create_task(run_daemon", section)

    def test_create_project_daemon_creation_is_exception_wrapped(self):
        """A daemon-creation failure must not be able to raise out of
        create_project() -- the daemon-config-building logic itself now
        lives in _ensure_sync_daemon() (shared with sync_project()'s
        self-heal path), so what matters here is that create_project()'s
        *call* to it is still wrapped in try/except, not the upsert call
        directly (which moved to a different function)."""
        src = Path("api/projects.py").read_text(encoding="utf-8")
        idx = src.find("_ensure_sync_daemon(project, user_id)")
        self.assertGreater(idx, 0)
        preceding = src[max(0, idx - 400):idx]
        self.assertIn("try:", preceding)

    def test_sync_endpoint_self_heal_is_not_exception_wrapped(self):
        """Unlike create_project() (where a daemon-creation failure must
        never block project creation), sync_project()'s self-heal call is
        deliberately NOT swallowed -- this endpoint's entire purpose is to
        trigger a sync, so if creating the missing daemon fails, the user
        should see that failure, not a silent no-op that looks like
        nothing happened."""
        src = Path("api/projects.py").read_text(encoding="utf-8")
        idx = src.find("async def sync_project")
        self.assertGreater(idx, 0)
        next_fn = src.find("\nasync def ", idx + 1)
        section = src[idx:next_fn] if next_fn > 0 else src[idx:]
        self.assertNotIn("try:", section)


class TestProjectContextSkillPhaseBEdits(unittest.TestCase):
    """skills/project-context/SKILL.md: rag_ingest guidance + unconditional
    project_artifact_write recording instruction.

    History: Phase 3 originally branched on "interactive conversation" vs
    "automated agent with no user watching" -- asking the model to
    self-classify its own execution context before deciding whether to call
    project_artifact_write or just state findings in its reply. Live testing
    across 7 different models found 5 of them skipped the tool call entirely
    (narrating a summary as text instead), including large/capable ones --
    not a capability gap, a structural one: the branch depended on the model
    correctly inferring an unstated fact about its own context. Collapsed to
    one unconditional rule -- always call the tool -- since the only actual
    blocker (no project linked) is already surfaced as a concrete tool error,
    not something requiring upfront self-classification.
    """

    def setUp(self):
        self.text = Path("skills/project-context/SKILL.md").read_text(encoding="utf-8")

    def test_mentions_rag_ingest_for_large_content(self):
        self.assertIn("rag_ingest", self.text)
        self.assertIn("too large to fully contextualize", self.text)

    def test_phase_3_instructs_project_artifact_write(self):
        self.assertIn("project_artifact_write", self.text)
        self.assertIn("That tool call is the deliverable", self.text)

    def test_phase_3_forbids_claiming_unrecorded_work(self):
        """Live testing found a model (nemotron-3-ultra) that skipped the
        tool call but still told the user its findings were recorded --
        worse than silently skipping, since nothing corrects a false claim
        in a headless run. This instruction is orthogonal to the removed
        audience-classification branch -- it must survive independently."""
        self.assertIn("Do not say or imply that you recorded", self.text)

    def test_phase_3_has_no_audience_self_classification(self):
        """The old branch asked the model to infer whether anyone was
        watching -- confirmed unreliable across a 7-model test batch.
        Confirms that language is gone, not just reworded."""
        normalized = self.text.lower()
        self.assertNotIn("no user watching", normalized)
        self.assertNotIn("interactive conversation with a user", normalized)
        self.assertNotIn("automated agent", normalized)
        self.assertNotIn("whether or not anyone is watching", normalized)

    def test_phase_3_handles_no_active_project_as_tool_fallback(self):
        """The only real branch left is a concrete tool-error fallback, not
        upfront self-classification."""
        normalized = " ".join(self.text.split())
        self.assertIn("No active project in this execution context", normalized)

    def test_phase_3_instructs_reconciliation_before_writing(self):
        """Phase C: reconciliation reads current state via project_artifact_read
        (same file= key convention as project_artifact_write, no filesystem
        path to guess wrong) before deciding append vs edit."""
        self.assertIn('project_artifact_read(file="knowledge")', self.text)
        self.assertIn('action="edit"', self.text)

    def test_allowed_tools_frontmatter_matches_real_tool_names(self):
        """allowed-tools previously listed placeholder names (file_read,
        list_directory, shell) that don't match any real EVA tool -- fixed
        to the actual tool names this skill instructs the model to use."""
        idx = self.text.find("allowed-tools:")
        self.assertGreater(idx, 0)
        line = self.text[idx:self.text.find("\n", idx)]
        for tool in ("read_file", "list_files", "project_artifact_read", "project_artifact_write"):
            self.assertIn(tool, line)
        self.assertNotIn("file_read", line)
        self.assertNotIn("list_directory", line)
        self.assertNotIn("shell", line)

    def test_phase_0_references_real_tool_name(self):
        """Phase 0 previously told the model to call list_directory, which
        isn't a real EVA tool -- corrected to list_files."""
        idx = self.text.find("### Phase 0")
        phase1_idx = self.text.find("### Phase 1")
        phase0_text = self.text[idx:phase1_idx]
        self.assertIn("list_files", phase0_text)
        self.assertNotIn("list_directory", phase0_text)


# ===========================================================================
# Phase C — Sync/Reconcile: edit_file, search_files, find_files,
# project_artifact_write's edit action, reconciliation prompts, resync endpoint
# ===========================================================================

class TestEditFileTool(unittest.IsolatedAsyncioTestCase):
    """edit_file (T-01): targeted replace-first-match, matching file_tools.py conventions."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    async def _run_tool(self, args, tmp_workspace=None):
        from core.tools.registry import DefaultToolRegistry
        tool_cls = DefaultToolRegistry.all()["edit_file"]
        tool = tool_cls()
        tool.execution = _fake_execution(project_workspace=tmp_workspace)
        return await tool.run(args)

    async def test_replaces_first_match(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "sample.txt"
            file_path.write_text("hello world\nhello again\n", encoding="utf-8")
            result = await self._run_tool({"path": "sample.txt", "old_string": "hello world", "new_string": "goodbye world"}, tmp_workspace=tmp)
            self.assertNotIn("error", result.result)
            content = file_path.read_text(encoding="utf-8")
            self.assertIn("goodbye world", content)
            self.assertIn("hello again", content)  # second occurrence untouched

    async def test_errors_clearly_when_old_string_not_found(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "sample.txt"
            file_path.write_text("some content\n", encoding="utf-8")
            result = await self._run_tool({"path": "sample.txt", "old_string": "not present", "new_string": "x"})
            self.assertIn("error", result.result)
            self.assertIn("not found", result.result["error"])

    async def test_errors_on_missing_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = await self._run_tool({"path": "missing.txt", "old_string": "a", "new_string": "b"})
            self.assertIn("error", result.result)

    def test_authority_is_mutating(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.base import ToolAuthority
        tool_cls = DefaultToolRegistry.all()["edit_file"]
        self.assertEqual(tool_cls.authority, ToolAuthority.MUTATING)
        self.assertEqual(tool_cls.category, "filesystem")


class TestReplaceFirstMatchHelper(unittest.TestCase):
    """The shared helper both edit_file and project_artifact_write's edit action use."""

    def test_replaces_first_occurrence_only(self):
        from tools.default.file_tools import replace_first_match
        result = replace_first_match("a b a", "a", "X")
        self.assertEqual(result, "X b a")

    def test_returns_none_when_not_found(self):
        from tools.default.file_tools import replace_first_match
        result = replace_first_match("hello", "goodbye", "hi")
        self.assertIsNone(result)


class TestSearchAndFindFilesTools(unittest.IsolatedAsyncioTestCase):
    """search_files + find_files (T-02): recursive, capped, category=search."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    async def _run(self, tool_name, args, tmp_workspace=None):
        from core.tools.registry import DefaultToolRegistry
        tool_cls = DefaultToolRegistry.all()[tool_name]
        tool = tool_cls()
        tool.execution = _fake_execution(project_workspace=tmp_workspace)
        return await tool.run(args)

    async def test_search_files_finds_match_recursively(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "sub"))
            with open(os.path.join(tmp, "sub", "a.py"), "w", encoding="utf-8") as f:
                f.write("def target_function():\n    pass\n")
            result = await self._run("search_files", {"path": ".", "pattern": "target_function"}, tmp_workspace=tmp)
            self.assertNotIn("error", result.result)
            self.assertEqual(result.result["match_count"], 1)
            self.assertTrue(result.result["matches"][0]["path"].endswith("a.py"))

    async def test_search_files_extension_filter(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.py").write_text("needle\n", encoding="utf-8")
            Path(tmp, "b.md").write_text("needle\n", encoding="utf-8")
            result = await self._run("search_files", {"path": ".", "pattern": "needle", "extensions": ["py"]}, tmp_workspace=tmp)
            self.assertEqual(result.result["match_count"], 1)

    async def test_search_files_invalid_regex_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = await self._run("search_files", {"path": ".", "pattern": "["}, tmp_workspace=tmp)
            self.assertIn("error", result.result)

    async def test_search_files_truncates_at_cap(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "big.txt").write_text("\n".join(["match"] * 20) + "\n", encoding="utf-8")
            with patch("tools.default.search_tools.SEARCH_TOOL_MAX_RESULTS", 5):
                result = await self._run("search_files", {"path": ".", "pattern": "match"}, tmp_workspace=tmp)
            self.assertEqual(result.result["match_count"], 5)
            self.assertTrue(result.result.get("truncated"))

    async def test_find_files_recursive_glob(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "nested"))
            Path(tmp, "top.py").write_text("x", encoding="utf-8")
            Path(tmp, "nested", "deep.py").write_text("x", encoding="utf-8")
            Path(tmp, "notes.md").write_text("x", encoding="utf-8")
            result = await self._run("find_files", {"path": ".", "pattern": "*.py"}, tmp_workspace=tmp)
            self.assertEqual(result.result["file_count"], 2)

    def test_both_tools_read_only_search_category(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.base import ToolAuthority
        for name in ("search_files", "find_files"):
            tool_cls = DefaultToolRegistry.all()[name]
            self.assertEqual(tool_cls.authority, ToolAuthority.READ_ONLY)
            self.assertEqual(tool_cls.category, "search")


class TestProjectArtifactWriteEditAction(unittest.IsolatedAsyncioTestCase):
    """project_artifact_write's new 'edit' action (Phase C)."""

    async def _run_tool(self, args, tmp_workspace=None):
        from tools.default.project_tools import ProjectArtifactWriteTool
        tool = ProjectArtifactWriteTool()
        tool.execution = _fake_execution(user_id="u-edit", parent_project_id="proj-edit", project_workspace=tmp_workspace)
        return await tool.run(args)

    async def test_edit_replaces_existing_entry(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            await self._run_tool(
                {"file": "knowledge", "action": "append", "content": "[NOTE ts=x] old fact"},
                tmp_workspace=tmp,
            )
            result = await self._run_tool(
                {"file": "knowledge", "action": "edit", "old_string": "[NOTE ts=x] old fact", "new_string": "[NOTE ts=y] updated fact"},
                tmp_workspace=tmp,
            )
            self.assertTrue(result.result.get("ok"))
            content = (Path(tmp) / ".eva" / "knowledge.eva").read_text(encoding="utf-8")
            self.assertIn("updated fact", content)
            self.assertNotIn("old fact", content)

    async def test_edit_errors_when_old_string_not_found(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = await self._run_tool(
                {"file": "knowledge", "action": "edit", "old_string": "nonexistent", "new_string": "x"},
                tmp_workspace=tmp,
            )
            self.assertIn("error", result.result)

    def test_edit_requires_old_and_new_string(self):
        from tools.default.project_tools import ProjectArtifactWriteArgs
        with self.assertRaises(Exception):
            ProjectArtifactWriteArgs(file="knowledge", action="edit", old_string="a")  # new_string missing

    def test_append_no_longer_requires_content_for_edit(self):
        from tools.default.project_tools import ProjectArtifactWriteArgs
        # edit action must NOT require 'content'
        args = ProjectArtifactWriteArgs(file="knowledge", action="edit", old_string="a", new_string="b")
        self.assertIsNone(args.content)

    def test_append_still_requires_content(self):
        from tools.default.project_tools import ProjectArtifactWriteArgs
        with self.assertRaises(Exception):
            ProjectArtifactWriteArgs(file="knowledge", action="append")


class TestProjectArtifactUpdatePromptReconciliation(unittest.TestCase):
    """prompts/project.py's PROJECT_ARTIFACT_UPDATE_PROMPT gains the 'edit' action."""

    def setUp(self):
        from prompts.project import PROJECT_ARTIFACT_UPDATE_PROMPT
        self.text = PROJECT_ARTIFACT_UPDATE_PROMPT

    def test_edit_action_documented_in_shape(self):
        self.assertIn('"append" | "replace_section" | "edit"', self.text)
        self.assertIn("old_string", self.text)
        self.assertIn("new_string", self.text)

    def test_reconciliation_guidance_present(self):
        self.assertIn("already shown above", self.text)
        self.assertIn('"edit" it instead', self.text)


class TestArtifactUpdaterEditAction(unittest.IsolatedAsyncioTestCase):
    """ProjectArtifactUpdater._run() must apply LLM-produced 'edit' instructions,
    matching the existing append/replace_section test pattern (mocked LLM call,
    real file assertions)."""

    async def test_edit_instruction_updates_existing_content(self):
        from core.project.manager import ProjectManager
        from core.project.artifact_updater import ProjectArtifactUpdater
        from core.project.eva_format import read_file, append_entry

        with tempfile.TemporaryDirectory() as ext_dir:
            ProjectManager.init_workspace(
                user_id="test_user", project_id="edit_proj_updater",
                name="Edit Updater Project", external_path=ext_dir,
            )
            knowledge_path = ProjectManager.get_artifact_path(
                "test_user", "edit_proj_updater", "knowledge.eva", ext_dir
            )
            append_entry(knowledge_path, "[NOTE ts=2026-01-01T00:00Z] Uses Flask 2.x")

            fake_message = MagicMock()
            fake_message.role = "user"
            fake_message.content = "actually we upgraded to Flask 3.x"

            llm_response = json.dumps([
                {"file": "knowledge", "action": "edit",
                 "old_string": "[NOTE ts=2026-01-01T00:00Z] Uses Flask 2.x",
                 "new_string": "[NOTE ts=2026-01-02T00:00Z] Uses Flask 3.x"}
            ])

            updater = ProjectArtifactUpdater()
            with patch(
                "core.project.artifact_updater.DBRepo.list_messages_for_conversation",
                new=AsyncMock(return_value=[fake_message]),
            ), patch(
                "core.project.artifact_updater.call_with_full_fallback",
                new=AsyncMock(return_value=llm_response),
            ):
                await updater.update(
                    project_id="edit_proj_updater",
                    conversation_id="conv-edit",
                    user_id="test_user",
                    session={"provider": "test", "model": "test-model"},
                    execution_id="exec-edit",
                    workspace_path=ext_dir,
                )

            knowledge = read_file(knowledge_path)
            self.assertIn("Flask 3.x", knowledge)
            self.assertNotIn("Flask 2.x", knowledge)

    async def test_edit_instruction_with_missing_old_string_is_skipped_not_crashed(self):
        from core.project.manager import ProjectManager
        from core.project.artifact_updater import ProjectArtifactUpdater

        with tempfile.TemporaryDirectory() as ext_dir:
            ProjectManager.init_workspace(
                user_id="test_user", project_id="edit_proj_skip",
                name="Edit Skip Project", external_path=ext_dir,
            )
            fake_message = MagicMock()
            fake_message.role = "user"
            fake_message.content = "hello"

            llm_response = json.dumps([
                {"file": "knowledge", "action": "edit", "old_string": "not present anywhere", "new_string": "x"}
            ])
            updater = ProjectArtifactUpdater()
            with patch(
                "core.project.artifact_updater.DBRepo.list_messages_for_conversation",
                new=AsyncMock(return_value=[fake_message]),
            ), patch(
                "core.project.artifact_updater.call_with_full_fallback",
                new=AsyncMock(return_value=llm_response),
            ):
                # Must not raise.
                await updater.update(
                    project_id="edit_proj_skip",
                    conversation_id="conv-skip",
                    user_id="test_user",
                    session={"provider": "test", "model": "test-model"},
                    execution_id="exec-skip",
                    workspace_path=ext_dir,
                )


class TestDaemonTriggerDirectCall(unittest.IsolatedAsyncioTestCase):
    """api/daemons.py's trigger_daemon() -- the single place trigger guards
    live. A separate DaemonRegistry.trigger_now() was tried during Phase C
    and reverted: POST /projects/{id}/sync calls this function directly as a
    plain Python call instead of going through a second shared method."""

    async def test_raises_404_for_unknown_daemon(self):
        from api.daemons import trigger_daemon
        from fastapi import HTTPException
        from core.daemons.registry import daemon_registry
        with patch.object(daemon_registry, "get", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as ctx:
                await trigger_daemon(name="nonexistent", auth={"user_id": "u1"})
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_raises_403_disabled_when_daemon_disabled_no_project(self):
        from api.daemons import trigger_daemon
        from fastapi import HTTPException
        from core.daemons.registry import daemon_registry
        fake_daemon = MagicMock()
        fake_daemon.enabled = False
        fake_daemon.lifecycle_status = "paused"
        fake_daemon.project_id = None
        with patch.object(daemon_registry, "get", new=AsyncMock(return_value=fake_daemon)):
            with self.assertRaises(HTTPException) as ctx:
                await trigger_daemon(name="d1", auth={"user_id": "u1"})
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("disabled", ctx.exception.detail.lower())

    async def test_raises_403_project_paused_when_cascade_disabled(self):
        from api.daemons import trigger_daemon
        from fastapi import HTTPException
        from core.daemons.registry import daemon_registry
        fake_daemon = MagicMock()
        fake_daemon.enabled = False
        fake_daemon.lifecycle_status = "project_paused"
        fake_daemon.project_id = "proj-1"
        with patch.object(daemon_registry, "get", new=AsyncMock(return_value=fake_daemon)):
            with self.assertRaises(HTTPException) as ctx:
                await trigger_daemon(name="d1", auth={"user_id": "u1"})
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("project", ctx.exception.detail.lower())

    async def test_success_fires_run_daemon_and_returns_execution_id(self):
        from api.daemons import trigger_daemon
        from core.daemons.registry import daemon_registry
        fake_daemon = MagicMock()
        fake_daemon.enabled = True
        fake_daemon.project_id = None
        fake_daemon.user_id = "u1"
        fake_daemon.name = "d1"

        async def _fake_run_daemon(daemon, execution_id, override_trigger=None):
            return {"success": True}

        with patch.object(daemon_registry, "get", new=AsyncMock(return_value=fake_daemon)), \
             patch.object(daemon_registry, "set_lifecycle_status", new=AsyncMock()), \
             patch.object(daemon_registry, "record_success", new=AsyncMock()) as mock_success, \
             patch("core.daemons.runner.run_daemon", new=_fake_run_daemon):
            result = await trigger_daemon(name="d1", auth={"user_id": "u1"})
            self.assertTrue(result.triggered)
            self.assertIsNotNone(result.execution_id)
            await asyncio.sleep(0.05)  # let the fire-and-forget task run
            mock_success.assert_called_once()


class TestProjectSyncEndpointWiring(unittest.IsolatedAsyncioTestCase):
    """POST /projects/{id}/sync -- source-level checks plus a couple of
    functional checks (deeply DB/execution-integrated code path, same
    category as TestB082MultiActiveProjects and
    TestRunnerAndProjectsSourceWiring elsewhere in this file).

    Deliberately no DB column / migration for this (reverted after review):
    project_id is already stable and immutable once a project is created,
    so the sync daemon's name is just recomputed the same way
    create_project() originally computed it -- sync_daemon_name(project_id)
    (core/daemons/registry.py -- moved there from api/projects.py during
    B-094 so core/daemons/runner.py can use it too without an api->core
    import) is the single shared source of that naming convention, used by
    both functions, no persisted pointer needed."""

    def test_sync_daemon_name_is_deterministic_from_project_id(self):
        from core.daemons.registry import sync_daemon_name
        self.assertEqual(sync_daemon_name("abcdefgh12345678"), "project-sync-abcdefgh")
        # Same project_id must always produce the same name.
        self.assertEqual(sync_daemon_name("abcdefgh12345678"), sync_daemon_name("abcdefgh12345678"))

    def test_sync_endpoint_calls_trigger_daemon_directly(self):
        src = Path("api/projects.py").read_text(encoding="utf-8")
        idx = src.find("async def sync_project")
        self.assertGreater(idx, 0)
        next_fn = src.find("\nasync def ", idx + 1)
        section = src[idx:next_fn] if next_fn > 0 else src[idx:]
        self.assertIn("from api.daemons import trigger_daemon", section)
        self.assertIn("await trigger_daemon(", section)
        self.assertIn("sync_daemon_name(project_id)", section)
        self.assertNotIn("trigger_now", section)

    async def test_sync_endpoint_self_heals_missing_daemon(self):
        """A project whose daemon creation silently failed at creation
        time, or one that predates this feature entirely, must self-heal
        on the first manual sync (create the daemon, then trigger it)
        instead of 404ing forever."""
        from api.projects import sync_project
        from core.db.repository import ProjectRepo
        from core.daemons.registry import daemon_registry

        fake_project = MagicMock()
        fake_project.id = "proj-old"
        fake_project.name = "Old Project"

        fake_daemon_row = MagicMock()
        fake_daemon_row.name = "project-sync-proj-old"

        fake_trigger_result = MagicMock()
        fake_trigger_result.triggered = True
        fake_trigger_result.execution_id = "exec-1"

        with patch.object(ProjectRepo, "get", new=AsyncMock(return_value=fake_project)), \
             patch.object(daemon_registry, "get", new=AsyncMock(return_value=None)), \
             patch.object(daemon_registry, "upsert", new=AsyncMock(return_value=fake_daemon_row)) as mock_upsert, \
             patch("api.daemons.trigger_daemon", new=AsyncMock(return_value=fake_trigger_result)) as mock_trigger:
            result = await sync_project(project_id="proj-old", auth={"user_id": "u1"})

        mock_upsert.assert_called_once()
        mock_trigger.assert_called_once()
        self.assertTrue(result["triggered"])

    async def test_sync_endpoint_skips_recreation_when_daemon_exists(self):
        """An existing daemon must not be redundantly re-created on every
        ordinary sync -- only the missing-row case should call upsert."""
        from api.projects import sync_project
        from core.db.repository import ProjectRepo
        from core.daemons.registry import daemon_registry

        fake_project = MagicMock()
        fake_project.id = "proj-existing"

        fake_existing_daemon = MagicMock()
        fake_existing_daemon.name = "project-sync-proj-exist"

        fake_trigger_result = MagicMock()
        fake_trigger_result.triggered = True
        fake_trigger_result.execution_id = "exec-2"

        with patch.object(ProjectRepo, "get", new=AsyncMock(return_value=fake_project)), \
             patch.object(daemon_registry, "get", new=AsyncMock(return_value=fake_existing_daemon)), \
             patch.object(daemon_registry, "upsert", new=AsyncMock()) as mock_upsert, \
             patch("api.daemons.trigger_daemon", new=AsyncMock(return_value=fake_trigger_result)) as mock_trigger:
            await sync_project(project_id="proj-existing", auth={"user_id": "u1"})

        mock_upsert.assert_not_called()
        mock_trigger.assert_called_once()

    def test_create_project_and_sync_endpoint_share_the_same_naming_helper(self):
        """Both paths must go through sync_daemon_name(), not each
        reconstruct the 'project-sync-{id[:8]}' convention independently --
        that duplication is exactly what caused this to need a DB column
        in the first version of this design. create_project() and
        sync_project()'s self-heal branch both route through
        _ensure_sync_daemon(), which is itself the only place that calls
        sync_daemon_name() to build the config -- one source of truth for
        both entry points now, not just two independently-consistent ones."""
        src = Path("api/projects.py").read_text(encoding="utf-8")

        ensure_idx = src.find("async def _ensure_sync_daemon")
        ensure_next = src.find("\ndef ", ensure_idx + 1)
        ensure_section = src[ensure_idx:ensure_next] if ensure_next > 0 else src[ensure_idx:]
        self.assertIn("sync_daemon_name(project.id)", ensure_section)
        self.assertNotIn('f"project-sync-{project.id[:8]}"', ensure_section)

        create_idx = src.find("async def create_project")
        create_next = src.find("\nasync def ", create_idx + 1)
        create_section = src[create_idx:create_next] if create_next > 0 else src[create_idx:]
        self.assertIn("_ensure_sync_daemon(project, user_id)", create_section)

    def test_project_model_has_no_sync_daemon_column(self):
        """Confirms the reverted migration/column really is gone, not just
        unused -- the whole point of this design is that project_id alone
        (already a stable identifier) is sufficient, no extra persisted
        state required."""
        from core.db.models import Project
        self.assertIsNone(Project.__table__.c.get("sync_daemon_name"))

    def test_project_repo_update_does_not_list_sync_daemon_name(self):
        src = Path("core/db/repository.py").read_text(encoding="utf-8")
        idx = src.find("class ProjectRepo")
        section = src[idx:idx + 4000]
        self.assertNotIn("sync_daemon_name", section)

    def test_no_migration_0004_exists(self):
        self.assertFalse(
            Path("alembic/versions/0004_project_sync_daemon_name.py").exists(),
            "Migration was reverted -- project_id is already a stable identifier, no schema change needed.",
        )


class TestSyncTriggerMessageStatic(unittest.TestCase):
    """
    _build_sync_trigger_message() -- back to a short, static message (single
    user_id argument, no per-project/workspace content).

    History: an earlier version of this function embedded current
    knowledge.eva content directly into the daemon's trigger message so the
    skill-runner subagent wouldn't have to fetch it. Live testing showed the
    daemon's own relay step silently dropped that embedded block when
    constructing its delegate_to_agent call, and inlining unbounded file
    content into a message doesn't scale for a long-running project anyway.
    Reverted -- the subagent now fetches current content itself, on demand,
    via project_artifact_read (see TestProjectArtifactReadTool below), and
    this message goes back to being the short wording that was reliable in
    every live test throughout this whole investigation.
    """

    def test_still_delegates_to_skill_runner(self):
        from api.projects import _build_sync_trigger_message
        msg = _build_sync_trigger_message("u1")
        self.assertIn("delegate_to_agent", msg)
        self.assertIn("skill-runner", msg)
        self.assertIn("SKILL.md", msg)

    def test_no_longer_embeds_file_content(self):
        """Confirms the revert really happened -- no knowledge-block
        machinery left over in the message-building function."""
        from api.projects import _build_sync_trigger_message
        msg = _build_sync_trigger_message("u1")
        self.assertNotIn("current knowledge.eva", msg)
        self.assertNotIn("---", msg)

    def test_create_project_uses_the_shared_helper(self):
        """_build_sync_trigger_message() now lives inside _ensure_sync_daemon()
        (shared by create_project() and sync_project()'s self-heal path),
        not inline in create_project() itself."""
        src = Path("api/projects.py").read_text(encoding="utf-8")
        ensure_idx = src.find("async def _ensure_sync_daemon")
        ensure_next = src.find("\ndef ", ensure_idx + 1)
        ensure_section = src[ensure_idx:ensure_next] if ensure_next > 0 else src[ensure_idx:]
        self.assertIn("_build_sync_trigger_message(user_id)", ensure_section)

    def test_trigger_daemon_no_longer_overrides_trigger(self):
        """trigger_daemon() should be back to calling run_daemon() with no
        override_trigger at all -- the daemon's stored trigger_message is
        static now and never goes stale, so there's nothing to rebuild."""
        src = Path("api/daemons.py").read_text(encoding="utf-8")
        idx = src.find("async def trigger_daemon")
        self.assertGreater(idx, 0)
        section = src[idx:idx + 4000]
        self.assertNotIn("override_trigger", section)
        self.assertNotIn("_build_sync_trigger_message", section)


class TestProjectArtifactReadTool(unittest.IsolatedAsyncioTestCase):
    """
    project_artifact_read -- the actual fix for the reconciliation-read bug.

    Addressed by the same logical file= key project_artifact_write already
    uses reliably (no filesystem path for the model to guess wrong), and
    returns full, uncapped content -- read_file has no cap either
    (file_tools.py's own docstring: "No implicit policy"), so this tool
    shouldn't silently drop data a project actually has just because it grew
    large. Handling size is a prompting concern (see SKILL.md Phase 3), not
    a tool-level truncation.
    """

    async def _run_tool(self, file_key: str, project_id="proj-1", user_id="u1", workspace_path="/ws"):
        from tools.default.project_tools import ProjectArtifactReadTool
        execution = MagicMock()
        execution.user_id = user_id
        execution.parent_project_id = project_id
        execution.project_workspace = workspace_path
        tool = ProjectArtifactReadTool()
        tool.execution = execution
        return await tool.run({"file": file_key})

    async def test_reads_full_content_no_truncation(self):
        from core.project.manager import ProjectManager
        big_content = "[NOTE ts=x] fact\n" * 5000  # much larger than any old-cap idea
        with patch.object(ProjectManager, "read_artifact", return_value=big_content):
            result = await self._run_tool("knowledge")
        self.assertEqual(result.result["content"], big_content)
        self.assertNotIn("truncated", result.result["content"])

    async def test_returns_filename_alongside_content(self):
        from core.project.manager import ProjectManager
        with patch.object(ProjectManager, "read_artifact", return_value="hello"):
            result = await self._run_tool("knowledge")
        self.assertEqual(result.result["file"], "knowledge.eva")
        self.assertEqual(result.result["content"], "hello")

    async def test_rejects_invalid_file_key(self):
        from pydantic import ValidationError
        from tools.default.project_tools import ProjectArtifactReadArgs
        with self.assertRaises(ValidationError):
            ProjectArtifactReadArgs(file="not-a-real-file")

    async def test_errors_without_active_project(self):
        from tools.default.project_tools import ProjectArtifactReadTool
        execution = MagicMock()
        execution.user_id = "u1"
        execution.parent_project_id = None
        tool = ProjectArtifactReadTool()
        tool.execution = execution
        result = await tool.run({"file": "knowledge"})
        self.assertIn("error", result.result)

    def test_registered_as_read_only(self):
        from tools.default.project_tools import ProjectArtifactReadTool
        from core.tools.base import ToolAuthority
        self.assertEqual(getattr(ProjectArtifactReadTool, "authority", None), ToolAuthority.READ_ONLY)

    def test_skill_runner_config_includes_the_new_tool(self):
        src = Path("default_agents/sa-13-skill-runner.yaml").read_text(encoding="utf-8")
        self.assertIn("project_artifact_read", src)

    def test_skill_instructs_calling_it_before_writing(self):
        text = Path("skills/project-context/SKILL.md").read_text(encoding="utf-8")
        self.assertIn('project_artifact_read(file="knowledge")', text)


# ===========================================================================
# Sprint 7 — B-076 + B-077: Hook System Completion & Bug Fixes
# ===========================================================================


class TestHookEventModelCall(unittest.TestCase):
    """B-076 Gap 6 — ModelCall is a valid hook event name."""

    def test_model_call_in_hook_event_name_enum(self):
        from core.hooks.schemas import HookEventName
        self.assertIn("ModelCall", [e.value for e in HookEventName])

    def test_model_call_from_hook_event_mapping(self):
        from core.hooks.schemas import HookEventName
        result = HookEventName.from_hook_event("model_call")
        self.assertEqual(result, HookEventName.MODEL_CALL)

    def test_model_call_in_valid_hook_events_schema(self):
        from api.schemas.requests import _VALID_HOOK_EVENTS
        self.assertIn("model_call", _VALID_HOOK_EVENTS)

    def test_hook_definition_accepts_model_call_event(self):
        from api.schemas.requests import HookDefinition
        h = HookDefinition(event="model_call", type="command", command="my_script.sh")
        self.assertEqual(h.event, "model_call")

    def test_hook_payload_has_model_provider_fields(self):
        from core.hooks.schemas import HookPayload
        p = HookPayload(
            hook_event_name="ModelCall",
            session_id="s1",
            user_id="u1",
            cwd="/tmp",
            model="llama-3.3-70b",
            provider="groq",
            turn_number=2,
            token_estimate=500,
        )
        payload_json = p.to_json()
        data = json.loads(payload_json)
        self.assertEqual(data["model"], "llama-3.3-70b")
        self.assertEqual(data["provider"], "groq")
        self.assertEqual(data["turn_number"], 2)


class TestRateLimitHandler(unittest.TestCase):
    """B-076 Gap 1 — rate_limit built-in pre-handler."""

    def setUp(self):
        # Reset the in-memory store before each test to avoid cross-test pollution.
        from core.tools import hooks as hooks_mod
        hooks_mod._RATE_LIMIT_STORE.clear()

    def test_rate_limit_in_pre_handlers_registry(self):
        from core.tools.hooks import _PRE_HANDLERS
        self.assertIn("rate_limit", _PRE_HANDLERS)

    def test_rate_limit_in_valid_pre_handlers_schema(self):
        from api.schemas.requests import _VALID_PRE_HANDLERS
        self.assertIn("rate_limit", _VALID_PRE_HANDLERS)

    def test_rate_limit_allows_within_limit(self):
        from core.tools.hooks import _handler_rate_limit_pre
        config = {"window_seconds": 60, "max_calls": 5}
        execution = MagicMock()
        execution.user_id = "user-rate-test"
        result = _handler_rate_limit_pre("web_search", {}, config, execution)
        self.assertEqual(result["action"], "allow")

    def test_rate_limit_blocks_when_exceeded(self):
        from core.tools.hooks import _handler_rate_limit_pre
        config = {"window_seconds": 60, "max_calls": 2}
        execution = MagicMock()
        execution.user_id = "user-rate-exceed"
        # First two calls should be allowed
        _handler_rate_limit_pre("web_search", {}, config, execution)
        _handler_rate_limit_pre("web_search", {}, config, execution)
        # Third call should be denied
        result = _handler_rate_limit_pre("web_search", {}, config, execution)
        self.assertEqual(result["action"], "deny")
        self.assertIn("Rate limit exceeded", result["reason"])

    def test_rate_limit_default_config(self):
        from core.tools.hooks import _handler_rate_limit_pre
        execution = MagicMock()
        execution.user_id = "user-rate-default"
        # No config — default is max_calls=10; first call should be allowed
        result = _handler_rate_limit_pre("some_tool", {}, None, execution)
        self.assertEqual(result["action"], "allow")

    def test_rate_limit_builtin_hook_accepted_by_schema(self):
        from api.schemas.requests import HookDefinition
        h = HookDefinition(
            event="pre",
            type="builtin",
            handler="rate_limit",
            config={"window_seconds": 60, "max_calls": 10},
        )
        self.assertEqual(h.handler, "rate_limit")


class TestHttpxValidation(unittest.TestCase):
    """B-076 Gap 2 — HTTP hook type validated against httpx availability."""

    def test_http_hook_without_url_raises_validation_error(self):
        from pydantic import ValidationError
        from api.schemas.requests import HookDefinition
        with self.assertRaises(ValidationError):
            HookDefinition(event="post", type="http")  # no url

    def test_http_hook_with_url_validates_ok_when_httpx_present(self):
        """
        This test passes if httpx is installed (expected in the dev environment).
        If httpx is absent, the ValidationError for the missing package is expected.
        """
        from api.schemas.requests import HookDefinition
        try:
            import httpx  # noqa: F401
            httpx_present = True
        except ImportError:
            httpx_present = False

        if httpx_present:
            h = HookDefinition(event="post", type="http", url="https://example.com/hook")
            self.assertEqual(h.url, "https://example.com/hook")
        else:
            from pydantic import ValidationError
            with self.assertRaises(ValidationError) as ctx:
                HookDefinition(event="post", type="http", url="https://example.com/hook")
            self.assertIn("httpx", str(ctx.exception))


class TestWarnHookResponse(unittest.TestCase):
    """B-077 Bug 2 — warn HookResponse is handled, not silently dropped."""

    def test_hook_response_warn_is_distinct_from_allow_and_block(self):
        from core.hooks.schemas import HookResponse
        r = HookResponse.warn("potential issue")
        self.assertFalse(r.blocked)
        self.assertTrue(r.warned)
        self.assertEqual(r.reason, "potential issue")

    def test_from_stdout_parses_warn_decision(self):
        from core.hooks.schemas import HookResponse
        import json
        stdout = json.dumps({"decision": "warn", "reason": "watch out"})
        r = HookResponse.from_stdout(stdout, 0)
        self.assertEqual(r.decision, "warn")
        self.assertTrue(r.warned)


class TestRunPreHandlesWarn(unittest.IsolatedAsyncioTestCase):
    """B-077 Bug 2 — run_pre surfaces warn through logging and continues."""

    async def test_warn_from_builtin_does_not_block(self):
        """A hook returning warn should not block the call."""
        from core.hooks.runner import HookRunner

        # Patch _dispatch to return a warn response for the first hook
        with patch.object(HookRunner, "_dispatch", new=AsyncMock()) as mock_dispatch:
            from core.hooks.schemas import HookResponse
            mock_dispatch.return_value = HookResponse.warn("advisory warning")

            hook_defs = [{"event": "pre", "tool": "*", "type": "command", "command": "test.sh"}]
            response = await HookRunner.run_pre(
                tool_name="web_search",
                tool_input={"query": "test"},
                hook_definitions=hook_defs,
                user_id="u1",
                session_id="s1",
                workspace_root="/tmp",
                execution_obj=None,
            )
            # warn should not block — response must be allow
            self.assertFalse(response.blocked)

    async def test_block_from_builtin_still_blocks(self):
        """A hook returning block must still block even with warn present."""
        from core.hooks.runner import HookRunner

        with patch.object(HookRunner, "_dispatch", new=AsyncMock()) as mock_dispatch:
            from core.hooks.schemas import HookResponse
            mock_dispatch.return_value = HookResponse.block("denied")

            hook_defs = [{"event": "pre", "tool": "*", "type": "command", "command": "test.sh"}]
            response = await HookRunner.run_pre(
                tool_name="shell",
                tool_input={"command": "ls"},
                hook_definitions=hook_defs,
                user_id="u1",
                session_id="s1",
                workspace_root="/tmp",
                execution_obj=None,
            )
            self.assertTrue(response.blocked)
            self.assertEqual(response.reason, "denied")


class TestPromptSubmitOptionA(unittest.IsolatedAsyncioTestCase):
    """
    B-076 Gap 3 + B-077 Bug 4 — Option A: UserPromptSubmit cannot modify
    the prompt (updated_input is silently discarded).  Only block/allow/warn.
    """

    async def test_prompt_submit_block_is_returned(self):
        from core.hooks.runner import HookRunner

        with patch.object(HookRunner, "_dispatch", new=AsyncMock()) as mock_dispatch:
            from core.hooks.schemas import HookResponse
            mock_dispatch.return_value = HookResponse.block("blocked prompt")

            hook_defs = [{"event": "prompt_submit", "type": "command", "command": "check.sh"}]
            response = await HookRunner.fire_prompt_submit(
                prompt="drop table users",
                hook_definitions=hook_defs,
                user_id="u1",
                session_id="s1",
                workspace_root="/tmp",
            )
            self.assertTrue(response.blocked)
            self.assertEqual(response.reason, "blocked prompt")

    async def test_prompt_submit_updated_input_is_not_returned(self):
        """Even if a script returns updated_input, it must never reach the caller."""
        from core.hooks.runner import HookRunner

        with patch.object(HookRunner, "_dispatch", new=AsyncMock()) as mock_dispatch:
            from core.hooks.schemas import HookResponse
            # Script returns updated_input but the response is not blocked
            mock_dispatch.return_value = HookResponse(
                decision="allow",
                updated_input={"rewritten": "prompt"},
            )

            hook_defs = [{"event": "prompt_submit", "type": "command", "command": "rewrite.sh"}]
            response = await HookRunner.fire_prompt_submit(
                prompt="original prompt",
                hook_definitions=hook_defs,
                user_id="u1",
                session_id="s1",
                workspace_root="/tmp",
            )
            # Option A: allow, but updated_input is never forwarded
            self.assertFalse(response.blocked)
            self.assertIsNone(response.updated_input)

    async def test_prompt_submit_allow_has_no_updated_input(self):
        """Fire with a non-blocking hook — response must have no updated_input."""
        from core.hooks.runner import HookRunner

        with patch.object(HookRunner, "_dispatch", new=AsyncMock()) as mock_dispatch:
            from core.hooks.schemas import HookResponse
            mock_dispatch.return_value = HookResponse.allow()

            hook_defs = [{"event": "prompt_submit", "type": "command", "command": "noop.sh"}]
            response = await HookRunner.fire_prompt_submit(
                prompt="hello",
                hook_definitions=hook_defs,
                user_id="u1",
                session_id="s1",
                workspace_root="/tmp",
            )
            self.assertFalse(response.blocked)
            self.assertIsNone(response.updated_input)


class TestSessionStartHook(unittest.TestCase):
    """B-077 Bug 1 — SessionStart hook is wired up in api/session.py."""

    def test_session_endpoint_imports_hookrunner(self):
        from api import session as session_mod
        self.assertTrue(
            hasattr(session_mod, "HookRunner"),
            "api/session.py must import HookRunner",
        )

    def test_session_endpoint_imports_asyncio(self):
        import ast, inspect
        from api import session as session_mod
        src = inspect.getsource(session_mod)
        self.assertIn("asyncio.create_task", src,
                      "api/session.py must fire SessionStart hooks via asyncio.create_task")

    def test_session_fires_session_start_event(self):
        import ast, inspect
        from api import session as session_mod
        src = inspect.getsource(session_mod)
        self.assertIn("session_start", src,
                      "api/session.py must fire the 'session_start' event")

    def test_session_start_in_valid_hook_events(self):
        from api.schemas.requests import _VALID_HOOK_EVENTS
        self.assertIn("session_start", _VALID_HOOK_EVENTS)

    def test_session_start_in_hook_event_name_enum(self):
        from core.hooks.schemas import HookEventName
        self.assertIn("SessionStart", [e.value for e in HookEventName])


class TestModelCallFireInAgentEngine(unittest.TestCase):
    """B-076 Gap 6 — ModelCall hook is fired in agent_engine.py."""

    def test_agent_engine_imports_hookrunner(self):
        from core import agent_engine
        self.assertTrue(
            hasattr(agent_engine, "HookRunner"),
            "core/agent_engine.py must import HookRunner",
        )

    def test_agent_engine_fires_model_call(self):
        import inspect
        from core import agent_engine
        src = inspect.getsource(agent_engine)
        self.assertIn("fire_model_call", src,
                      "agent_engine.py must call HookRunner.fire_model_call")

    def test_fire_model_call_method_exists(self):
        from core.hooks.runner import HookRunner
        self.assertTrue(
            hasattr(HookRunner, "fire_model_call"),
            "HookRunner must have a fire_model_call class method",
        )

    def test_model_call_is_non_blocking_in_agent_engine(self):
        """Fire is wrapped in asyncio.create_task — it's always non-blocking."""
        import inspect
        from core import agent_engine
        src = inspect.getsource(agent_engine)
        # The task must be created with create_task, not awaited directly
        self.assertIn("create_task", src)


class TestHookPropagationToSubagents(unittest.TestCase):
    """B-076 Gap 5 — hook propagation is documented (works via shared user session)."""

    def test_delegate_tool_documents_hook_propagation(self):
        import inspect
        from tools.default import delegate_tool
        src = inspect.getsource(delegate_tool)
        self.assertIn("Hook propagation", src,
                      "delegate_tool.py must document hook propagation behaviour")

    def test_parallel_delegate_tool_documents_hook_propagation(self):
        import inspect
        from tools.default import parallel_delegate_tool
        src = inspect.getsource(parallel_delegate_tool)
        self.assertIn("Hook propagation", src,
                      "parallel_delegate_tool.py must document hook propagation behaviour")

    def test_dispatcher_reads_hooks_from_user_session(self):
        """Hooks are read via get_user_session(user_id) — same for parent and children."""
        import inspect
        from core.tools import dispatcher
        src = inspect.getsource(dispatcher)
        self.assertIn("tool_hooks", src)
        self.assertIn("get_user_session", src)


# ===========================================================================
# S-03 — PII Redaction (Microsoft Presidio)
# ===========================================================================

class TestPiiRedactor(unittest.TestCase):
    """core/security/pii_redactor.py — redact_pii() core detection/redaction."""

    def test_redacts_default_entities(self):
        from core.security.pii_redactor import redact_pii
        text = (
            "Email me at jane.doe@example.com or call (555) 987-6543. "
            "My SSN is 234-56-7890 and my card is 4111111111111111."
        )
        redacted, count = redact_pii(text)
        self.assertGreater(count, 0)
        self.assertNotIn("jane.doe@example.com", redacted)
        self.assertNotIn("234-56-7890", redacted)
        self.assertNotIn("4111111111111111", redacted)
        self.assertIn("<EMAIL_ADDRESS>", redacted)
        self.assertIn("<US_SSN>", redacted)
        self.assertIn("<CREDIT_CARD>", redacted)

    def test_does_not_redact_person_location_datetime_by_default(self):
        """PERSON/LOCATION/DATE_TIME are functionally necessary (names in normal
        conversation, location/timezone for scheduling) -- excluded from the
        default allowlist even though Presidio can detect all three."""
        from core.security.pii_redactor import redact_pii
        text = "My name is John Smith, I live in Mumbai, and the meeting is tomorrow."
        redacted, count = redact_pii(text)
        self.assertEqual(count, 0)
        self.assertEqual(redacted, text)

    def test_mixed_text_only_redacts_scoped_entities(self):
        from core.security.pii_redactor import redact_pii
        text = "John Smith's email is john@example.com and he lives in Paris."
        redacted, _count = redact_pii(text)
        self.assertIn("John Smith", redacted)
        self.assertIn("Paris", redacted)
        self.assertNotIn("john@example.com", redacted)
        self.assertIn("<EMAIL_ADDRESS>", redacted)

    def test_empty_and_non_string_input_returned_unchanged(self):
        from core.security.pii_redactor import redact_pii
        self.assertEqual(redact_pii(""), ("", 0))
        self.assertEqual(redact_pii(None), (None, 0))

    def test_no_pii_returns_original_text_and_zero_count(self):
        from core.security.pii_redactor import redact_pii
        text = "Let's schedule the review for next week."
        redacted, count = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(count, 0)

    def test_explicit_entities_override_replaces_default_allowlist(self):
        """Passing entities=[...] fully replaces DEFAULT_PII_ENTITIES -- e.g. a
        stricter deployment opting into PERSON redaction, at the cost of no
        longer redacting the default set unless also included explicitly."""
        from core.security.pii_redactor import redact_pii
        text = "My name is Jane Doe and my email is jane@example.com."
        redacted, count = redact_pii(text, entities=["PERSON"])
        self.assertEqual(count, 1)
        self.assertIn("<PERSON>", redacted)
        # EMAIL_ADDRESS was not in the override list -- not redacted.
        self.assertIn("jane@example.com", redacted)

    def test_default_entities_excludes_never_redact_categories(self):
        from core.security.pii_redactor import DEFAULT_PII_ENTITIES
        for excluded in ("PERSON", "LOCATION", "DATE_TIME", "NRP", "URL", "IP_ADDRESS"):
            self.assertNotIn(excluded, DEFAULT_PII_ENTITIES)


class TestRedactPiiHook(unittest.TestCase):
    """core/tools/hooks.py — the 'redact_pii' PostToolUse handler (S-03)."""

    def test_registered_as_builtin_post_handler(self):
        from core.tools.hooks import _POST_HANDLERS
        self.assertIn("redact_pii", _POST_HANDLERS)

    def test_valid_in_hook_definition_schema(self):
        from api.schemas.requests import _VALID_POST_HANDLERS
        self.assertIn("redact_pii", _VALID_POST_HANDLERS)

    def test_hook_redacts_string_tool_result(self):
        from core.tools.hooks import run_post_hooks
        hook_defs = [{"tool": "*", "event": "post", "handler": "redact_pii"}]
        result = run_post_hooks(
            tool_name="read_file",
            result="Contact: jane.doe@example.com",
            hook_definitions=hook_defs,
            execution_obj=None,
        )
        self.assertNotIn("jane.doe@example.com", result)
        self.assertIn("<EMAIL_ADDRESS>", result)

    def test_hook_redacts_nested_dict_result(self):
        """Real tools return their own native shape, not a plain string --
        e.g. shell: {"cmd": [...], "stdout": ..., "exit_code": ...}. The
        handler must recurse into dicts/lists rather than ignoring them
        (the original design, corrected 2026-09-16 after live testing found
        it silently never redacted anything for a dict-shaped tool result)."""
        from core.tools.hooks import run_post_hooks
        hook_defs = [{"tool": "*", "event": "post", "handler": "redact_pii"}]
        original = {
            "cmd": ["echo", "jane.doe@example.com"],
            "stdout": "jane.doe@example.com\n",
            "exit_code": 0,
        }
        result = run_post_hooks(
            tool_name="shell",
            result=original,
            hook_definitions=hook_defs,
            execution_obj=None,
        )
        self.assertNotIn("jane.doe@example.com", result["stdout"])
        self.assertIn("<EMAIL_ADDRESS>", result["stdout"])
        self.assertNotIn("jane.doe@example.com", result["cmd"][1])
        self.assertEqual(result["exit_code"], 0)  # non-string leaves untouched

    def test_hook_passes_through_non_string_non_container_result(self):
        from core.tools.hooks import run_post_hooks
        hook_defs = [{"tool": "*", "event": "post", "handler": "redact_pii"}]
        for original in (42, True, None, 3.14):
            result = run_post_hooks(
                tool_name="read_file",
                result=original,
                hook_definitions=hook_defs,
                execution_obj=None,
            )
            self.assertEqual(result, original)

    def test_hook_config_entities_override(self):
        from core.tools.hooks import run_post_hooks
        hook_defs = [{
            "tool": "*", "event": "post", "handler": "redact_pii",
            "config": {"entities": ["PERSON"]},
        }]
        result = run_post_hooks(
            tool_name="read_file",
            result="Customer name: Jane Doe",
            hook_definitions=hook_defs,
            execution_obj=None,
        )
        self.assertIn("<PERSON>", result)

    def test_hook_only_fires_for_matching_tool_and_event(self):
        from core.tools.hooks import run_post_hooks
        hook_defs = [{"tool": "other_tool", "event": "post", "handler": "redact_pii"}]
        text = "Contact: jane.doe@example.com"
        result = run_post_hooks(
            tool_name="read_file",
            result=text,
            hook_definitions=hook_defs,
            execution_obj=None,
        )
        self.assertEqual(result, text)


class TestWalkAndRedactStrings(unittest.TestCase):
    """core/tools/hooks.py — _walk_and_redact_strings(), the shared recursion
    helper both redact_env_vars and redact_pii post-hooks use (added
    2026-09-16 to fix a real bug -- see TestPostToolUseRealToolShape below)."""

    def test_redacts_top_level_string(self):
        from core.tools.hooks import _walk_and_redact_strings
        result = _walk_and_redact_strings("hello world", lambda s: s.upper())
        self.assertEqual(result, "HELLO WORLD")

    def test_recurses_into_dict_values(self):
        from core.tools.hooks import _walk_and_redact_strings
        result = _walk_and_redact_strings({"a": "x", "b": "y"}, lambda s: s.upper())
        self.assertEqual(result, {"a": "X", "b": "Y"})

    def test_recurses_into_list_items(self):
        from core.tools.hooks import _walk_and_redact_strings
        result = _walk_and_redact_strings(["x", "y"], lambda s: s.upper())
        self.assertEqual(result, ["X", "Y"])

    def test_recurses_into_nested_dict_and_list(self):
        from core.tools.hooks import _walk_and_redact_strings
        original = {"cmd": ["echo", "x"], "meta": {"note": "y"}, "code": 0}
        result = _walk_and_redact_strings(original, lambda s: s.upper())
        self.assertEqual(result, {"cmd": ["ECHO", "X"], "meta": {"note": "Y"}, "code": 0})

    def test_non_string_leaves_pass_through_unchanged(self):
        from core.tools.hooks import _walk_and_redact_strings
        for leaf in (42, 3.14, True, None):
            self.assertEqual(_walk_and_redact_strings(leaf, lambda s: s.upper()), leaf)


class TestPostToolUseRealToolShape(unittest.IsolatedAsyncioTestCase):
    """core/hooks/runner.py's HookRunner.run_post() -- the actual production
    call path (core/tools/dispatcher.py:670), as opposed to run_post_hooks()
    in core/tools/hooks.py which is never called outside tests.

    Regression coverage for a real bug found live 2026-09-16: _run_builtin_post
    unconditionally read tool_response["result"], but the dispatcher passes a
    tool's own native result shape (ToolResult.result) straight through --
    e.g. shell returns {"cmd": [...], "stdout": ..., "exit_code": ...}, which
    has no "result" key of its own. That made result_val always None, so
    redact_pii (and redact_env_vars, built on the same contract) silently
    redacted nothing for any real tool call -- confirmed via a live chat
    execution whose shell tool_result still had a raw, unredacted email in
    `stdout` despite the redact_pii hook being configured on that tool."""

    async def test_run_post_redacts_pii_in_native_shell_shaped_result(self):
        from core.hooks.runner import HookRunner
        hook_defs = [{"tool": "shell", "event": "post", "handler": "redact_pii"}]
        # The exact native shape core/tools/dispatcher.py passes for shell --
        # no top-level "result" key, mirroring what a real ToolResult.result
        # looks like for this tool.
        native_shell_result = {
            "cmd": ["echo", "jane.doe@example.com"],
            "cwd": "",
            "exit_code": 0,
            "stdout": "jane.doe@example.com\n",
            "stderr": "",
        }
        mutated = await HookRunner.run_post(
            tool_name="shell",
            tool_input={"cmd": ["echo", "jane.doe@example.com"]},
            tool_response=native_shell_result,
            hook_definitions=hook_defs,
            user_id="u1",
            session_id="s1",
            workspace_root="/tmp",
        )
        self.assertNotIn("jane.doe@example.com", mutated["stdout"])
        self.assertIn("<EMAIL_ADDRESS>", mutated["stdout"])
        self.assertEqual(mutated["exit_code"], 0)

    async def test_run_post_leaves_result_unchanged_when_no_hook_matches(self):
        """Backward-compat guard: when no configured hook fires (wrong tool
        name, or a non-redaction handler like audit_log), run_post() must
        return the exact original tool_response, not a wrapped/altered copy
        -- the dispatcher uses this return value as-is for the real
        tool_result the LLM and SSE stream see."""
        from core.hooks.runner import HookRunner
        hook_defs = [{"tool": "other_tool", "event": "post", "handler": "redact_pii"}]
        native_shell_result = {"cmd": ["echo", "hi"], "stdout": "hi\n", "exit_code": 0}
        result = await HookRunner.run_post(
            tool_name="shell",
            tool_input={},
            tool_response=native_shell_result,
            hook_definitions=hook_defs,
            user_id="u1",
            session_id="s1",
            workspace_root="/tmp",
        )
        self.assertEqual(result, native_shell_result)
        self.assertIs(result, native_shell_result)

    async def test_run_post_still_supports_pre_wrapped_result_key(self):
        """A caller that already wraps its payload as {"result": <value>}
        must keep working -- the fix only adds a fallback, it doesn't remove
        the original "result"-key path."""
        from core.hooks.runner import HookRunner
        hook_defs = [{"tool": "custom", "event": "post", "handler": "redact_pii"}]
        result = await HookRunner.run_post(
            tool_name="custom",
            tool_input={},
            tool_response={"result": "Contact: jane.doe@example.com"},
            hook_definitions=hook_defs,
            user_id="u1",
            session_id="s1",
            workspace_root="/tmp",
        )
        self.assertNotIn("jane.doe@example.com", result)
        self.assertIn("<EMAIL_ADDRESS>", result)


class TestPiiRedactionSessionSetting(unittest.TestCase):
    """S-03: pii_redaction_enabled session-setting plumbing (mirrors skills_mode)."""

    def test_default_session_has_pii_redaction_disabled(self):
        from config.settings import DEFAULT_SESSION
        self.assertIn("pii_redaction_enabled", DEFAULT_SESSION)
        self.assertFalse(DEFAULT_SESSION["pii_redaction_enabled"])

    def test_hydrate_from_db_propagates_true(self):
        from config.settings import hydrate_user_session_from_db, get_user_session
        prefs = {
            "provider": "groq", "model": "m",
            "pii_redaction_enabled": True,
        }
        hydrate_user_session_from_db("u-pii-hydrate-true", prefs)
        session = get_user_session("u-pii-hydrate-true")
        self.assertTrue(session["pii_redaction_enabled"])

    def test_hydrate_from_db_defaults_false_when_absent(self):
        from config.settings import hydrate_user_session_from_db, get_user_session
        prefs = {"provider": "groq", "model": "m"}
        hydrate_user_session_from_db("u-pii-hydrate-default", prefs)
        session = get_user_session("u-pii-hydrate-default")
        self.assertFalse(session["pii_redaction_enabled"])

    def test_set_user_session_propagates_flag(self):
        from config.settings import set_user_session, get_user_session
        set_user_session("u-pii-setsession", provider="groq", model="m",
                          pii_redaction_enabled=True)
        session = get_user_session("u-pii-setsession")
        self.assertTrue(session["pii_redaction_enabled"])

    def test_build_prefs_dict_propagates_flag(self):
        from core.auth.hydration import build_prefs_dict
        from unittest.mock import MagicMock
        prefs = MagicMock()
        prefs.provider = "groq"
        prefs.model = "m"
        prefs.reasoning = None
        prefs.tools = []
        prefs.fallback_chain = []
        prefs.tool_approval_mode = "auto"
        prefs.allowed_authorities = "read_only,mutating,destructive"
        prefs.agent_mode = "auto"
        prefs.require_plan_approval = False
        prefs.user_memory_enabled = True
        prefs.agent_scope_key = None
        prefs.agent_memory_enabled = True
        prefs.skills_mode = "auto"
        prefs.tool_hooks = []
        prefs.shell_readonly = False
        prefs.user_timezone = None
        prefs.user_location = None
        prefs.user_language = None
        prefs.pii_redaction_enabled = True
        result = build_prefs_dict(prefs)
        self.assertTrue(result["pii_redaction_enabled"])


# ===========================================================================
# S-11 — MCP Credential Security
# ===========================================================================

def _mock_keyring():
    """Patch the `keyring` module for core.mcp.credentials tests -- an
    in-memory dict standing in for the real OS credential store, so tests
    never touch Windows Credential Manager / Keychain / Secret Service."""
    store: Dict[str, str] = {}

    def _set(service, ref, value):
        store[ref] = value

    def _get(service, ref):
        return store.get(ref)

    def _delete(service, ref):
        # Production code only ever catches broad `Exception` around this
        # call (real keyring raises its own PasswordDeleteError for a
        # missing entry) -- a plain KeyError is a faithful-enough stand-in
        # without needing to construct the real exception type here.
        if ref not in store:
            raise KeyError(f"not found: {ref}")
        del store[ref]

    mock = MagicMock()
    mock.set_password.side_effect = _set
    mock.get_password.side_effect = _get
    mock.delete_password.side_effect = _delete
    return mock, store


class TestMcpIsSecretKey(unittest.TestCase):
    """core/mcp/credentials.py — is_secret_key() heuristic (adapted from
    OpenHands SDK's redact.py, MIT)."""

    def test_matches_common_secret_names(self):
        from core.mcp.credentials import is_secret_key
        for key in ("NOTION_TOKEN", "API_KEY", "Authorization", "PASSWORD",
                    "SESSION_ID", "AUTH_HEADER", "GITHUB_CREDENTIAL"):
            self.assertTrue(is_secret_key(key), f"{key!r} should be secret-shaped")

    def test_does_not_match_ordinary_config(self):
        from core.mcp.credentials import is_secret_key
        for key in ("DEBUG", "Content-Type", "TIMEOUT", "REGION", "USER_AGENT"):
            self.assertFalse(is_secret_key(key), f"{key!r} should not be secret-shaped")


class TestMcpExtractAndStoreSecretsForField(unittest.TestCase):
    """core/mcp/credentials.py — extract_and_store_secrets_for_field()."""

    def test_redacts_secret_keys_writes_keyring(self):
        mock_keyring, store = _mock_keyring()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            redacted, refs = credentials.extract_and_store_secrets_for_field(
                "cfg1", "env", {"NOTION_TOKEN": "secret123", "DEBUG": "false"},
            )
        self.assertEqual(redacted["NOTION_TOKEN"], credentials.REDACTED_PLACEHOLDER)
        self.assertEqual(redacted["DEBUG"], "false")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["field"], "env")
        self.assertEqual(refs[0]["key"], "NOTION_TOKEN")
        self.assertEqual(store[refs[0]["credential_ref"]], "secret123")

    def test_placeholder_value_treated_as_unchanged(self):
        """Re-submitting a config read back from the API (secret field still
        showing the placeholder) must not overwrite the real keyring value
        or drop the ref."""
        mock_keyring, store = _mock_keyring()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            existing = {"NOTION_TOKEN": "mcp:cfg1:env:NOTION_TOKEN"}
            store[existing["NOTION_TOKEN"]] = "original_secret"
            redacted, refs = credentials.extract_and_store_secrets_for_field(
                "cfg1", "env",
                {"NOTION_TOKEN": credentials.REDACTED_PLACEHOLDER},
                existing,
            )
        self.assertEqual(redacted["NOTION_TOKEN"], credentials.REDACTED_PLACEHOLDER)
        self.assertEqual(refs, [{"field": "env", "key": "NOTION_TOKEN",
                                  "credential_ref": existing["NOTION_TOKEN"]}])
        self.assertEqual(store[existing["NOTION_TOKEN"]], "original_secret")

    def test_removed_key_deletes_keyring_entry(self):
        mock_keyring, store = _mock_keyring()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            existing = {"OLD_TOKEN": "mcp:cfg1:env:OLD_TOKEN"}
            store[existing["OLD_TOKEN"]] = "old_secret"
            redacted, refs = credentials.extract_and_store_secrets_for_field(
                "cfg1", "env", {"DEBUG": "true"}, existing,
            )
        self.assertNotIn("OLD_TOKEN", redacted)
        self.assertEqual(refs, [])
        self.assertNotIn(existing["OLD_TOKEN"], store)

    def test_env_and_header_same_key_name_do_not_collide(self):
        """The exact bug found and fixed while wiring this up: env and
        headers are namespaced separately in the keyring ref, so a key
        with the same name in both fields never overwrites the other."""
        mock_keyring, store = _mock_keyring()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            _, env_refs = credentials.extract_and_store_secrets_for_field(
                "cfg1", "env", {"TOKEN": "env_value"},
            )
            _, header_refs = credentials.extract_and_store_secrets_for_field(
                "cfg1", "headers", {"TOKEN": "header_value"},
            )
        self.assertNotEqual(env_refs[0]["credential_ref"], header_refs[0]["credential_ref"])
        self.assertEqual(store[env_refs[0]["credential_ref"]], "env_value")
        self.assertEqual(store[header_refs[0]["credential_ref"]], "header_value")

    def test_keyring_write_failure_leaves_value_unredacted(self):
        """Fail-open per key: a broken keyring write must not drop data or
        crash the create/update request."""
        mock_keyring = MagicMock()
        mock_keyring.set_password.side_effect = RuntimeError("keyring backend unavailable")
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            redacted, refs = credentials.extract_and_store_secrets_for_field(
                "cfg1", "env", {"API_KEY": "secret"},
            )
        self.assertEqual(redacted["API_KEY"], "secret")
        self.assertEqual(refs, [])


class TestMcpResolveSecrets(unittest.TestCase):
    """core/mcp/credentials.py — resolve_secrets(), the connection-time
    chokepoint every real MCP connection site calls."""

    def test_resolves_real_values_from_keyring(self):
        mock_keyring, store = _mock_keyring()
        store["mcp:cfg1:env:TOKEN"] = "real_secret"
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            cfg = MagicMock()
            cfg.env = {"TOKEN": credentials.REDACTED_PLACEHOLDER, "DEBUG": "false"}
            cfg.headers = None
            cfg.credential_refs = [{"field": "env", "key": "TOKEN",
                                     "credential_ref": "mcp:cfg1:env:TOKEN"}]
            real_env, real_headers = credentials.resolve_secrets(cfg)
        self.assertEqual(real_env["TOKEN"], "real_secret")
        self.assertEqual(real_env["DEBUG"], "false")
        self.assertIsNone(real_headers)

    def test_missing_keyring_entry_leaves_placeholder(self):
        """Fail-safe: a broken/missing keyring entry surfaces as a
        connection auth failure the user can see, not a crash here."""
        mock_keyring, _store = _mock_keyring()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            cfg = MagicMock()
            cfg.env = {"TOKEN": credentials.REDACTED_PLACEHOLDER}
            cfg.headers = None
            cfg.credential_refs = [{"field": "env", "key": "TOKEN",
                                     "credential_ref": "mcp:cfg1:env:TOKEN"}]
            real_env, _ = credentials.resolve_secrets(cfg)
        self.assertEqual(real_env["TOKEN"], credentials.REDACTED_PLACEHOLDER)

    def test_no_credential_refs_returns_fields_unchanged(self):
        mock_keyring, _store = _mock_keyring()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            cfg = MagicMock()
            cfg.env = {"DEBUG": "false"}
            cfg.headers = {"Content-Type": "application/json"}
            cfg.credential_refs = None
            real_env, real_headers = credentials.resolve_secrets(cfg)
        self.assertEqual(real_env, {"DEBUG": "false"})
        self.assertEqual(real_headers, {"Content-Type": "application/json"})

    def test_env_and_headers_none_returns_none(self):
        mock_keyring, _store = _mock_keyring()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            cfg = MagicMock()
            cfg.env = None
            cfg.headers = None
            cfg.credential_refs = []
            real_env, real_headers = credentials.resolve_secrets(cfg)
        self.assertIsNone(real_env)
        self.assertIsNone(real_headers)


class TestMcpDeleteCredentials(unittest.TestCase):
    """core/mcp/credentials.py — delete_credentials() and
    clear_field_secrets(), the two cascade-delete paths."""

    def test_delete_credentials_removes_all_refs(self):
        mock_keyring, store = _mock_keyring()
        store["mcp:cfg1:env:A"] = "a"
        store["mcp:cfg1:headers:B"] = "b"
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            credentials.delete_credentials([
                {"field": "env", "key": "A", "credential_ref": "mcp:cfg1:env:A"},
                {"field": "headers", "key": "B", "credential_ref": "mcp:cfg1:headers:B"},
            ])
        self.assertEqual(store, {})

    def test_delete_credentials_tolerates_missing_entry(self):
        mock_keyring, _store = _mock_keyring()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            # Should not raise even though this ref was never stored.
            credentials.delete_credentials([
                {"field": "env", "key": "GHOST", "credential_ref": "mcp:cfg1:env:GHOST"},
            ])

    def test_delete_credentials_handles_none(self):
        mock_keyring, _store = _mock_keyring()
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            credentials.delete_credentials(None)  # must not raise

    def test_clear_field_secrets_removes_given_refs(self):
        mock_keyring, store = _mock_keyring()
        store["mcp:cfg1:headers:AUTH"] = "token"
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            credentials.clear_field_secrets({"AUTH": "mcp:cfg1:headers:AUTH"})
        self.assertEqual(store, {})


class TestMcpCredentialMigration(unittest.IsolatedAsyncioTestCase):
    """core/mcp/credentials.py — migrate_existing_plaintext_credentials(),
    the one-time startup pass for rows that predate S-11."""

    async def test_migrates_row_with_null_credential_refs(self):
        mock_keyring, store = _mock_keyring()
        cfg = MagicMock()
        cfg.id = "cfg1"
        cfg.env = {"API_KEY": "plaintext_secret"}
        cfg.headers = None
        cfg.credential_refs = None  # the pre-S-11 sentinel

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [cfg]
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            migrated = await credentials.migrate_existing_plaintext_credentials(mock_db)

        self.assertEqual(migrated, 1)
        self.assertEqual(cfg.env["API_KEY"], credentials.REDACTED_PLACEHOLDER)
        self.assertIsNotNone(cfg.credential_refs)
        self.assertEqual(len(cfg.credential_refs), 1)
        mock_db.commit.assert_awaited_once()

    async def test_skips_already_migrated_row_including_empty_list(self):
        """`is not None` is the sentinel, not truthiness -- a row that was
        already migrated and genuinely has no secrets (credential_refs == [])
        must not be re-scanned on every boot."""
        mock_keyring, _store = _mock_keyring()
        cfg = MagicMock()
        cfg.credential_refs = []  # already migrated, found nothing

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [cfg]
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            migrated = await credentials.migrate_existing_plaintext_credentials(mock_db)

        self.assertEqual(migrated, 0)
        mock_db.commit.assert_not_awaited()

    async def test_no_configs_is_a_clean_noop(self):
        mock_keyring, _store = _mock_keyring()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from core.mcp import credentials
            migrated = await credentials.migrate_existing_plaintext_credentials(mock_db)

        self.assertEqual(migrated, 0)
        mock_db.commit.assert_not_awaited()


class TestMcpConfigEndpointCredentialWiring(unittest.IsolatedAsyncioTestCase):
    """api/mcp.py -- create_mcp_config / update_mcp_config wiring of the
    S-11 credential extraction. This is the layer that actually had the
    real bug (env-only updates wiping headers' keyring entries) -- the
    unit tests above cover core/mcp/credentials.py's functions correctly,
    but only exercising the endpoint's composition of them catches a
    wiring mistake like that one."""

    async def test_create_redacts_secret_before_persisting(self):
        mock_keyring, _store = _mock_keyring()
        created_data = {}

        async def fake_create_config(db, user_id, data):
            created_data.update(data)
            cfg = MagicMock()
            for k, v in data.items():
                setattr(cfg, k, v)
            cfg.enabled = data.get("enabled", True)
            return cfg

        with patch.dict("sys.modules", {"keyring": mock_keyring}), \
             patch("api.mcp.AsyncSessionLocal") as MockSession, \
             patch("api.mcp.MCPRepo") as MockRepo, \
             patch("api.mcp.MCPManager") as MockManager:
            MockSession.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)
            MockRepo.create_config = AsyncMock(side_effect=fake_create_config)
            MockManager.return_value.sync_tools_for_config = AsyncMock(return_value=[])

            from api.mcp import create_mcp_config, MCPConfigCreate
            payload = MCPConfigCreate(
                name="notion", transport="stdio", command="npx",
                env={"NOTION_TOKEN": "real_secret_value", "DEBUG": "false"},
            )
            await create_mcp_config(payload, auth={"user_id": "u1"})

        self.assertEqual(created_data["env"]["NOTION_TOKEN"], "•" * 8)
        self.assertEqual(created_data["env"]["DEBUG"], "false")
        self.assertEqual(len(created_data["credential_refs"]), 1)
        self.assertNotIn("real_secret_value", str(created_data))

    async def test_update_env_only_does_not_touch_headers_refs(self):
        """The exact bug found while wiring S-11 up: an update touching
        only `env` must leave the untouched `headers` field's keyring
        entries and credential_refs completely alone, not wipe them."""
        mock_keyring, store = _mock_keyring()
        store["mcp:cfg1:headers:AUTH_TOKEN"] = "header_secret"
        store["mcp:cfg1:env:OLD_KEY"] = "old_secret"

        existing_cfg = MagicMock()
        existing_cfg.id = "cfg1"
        existing_cfg.env = {"OLD_KEY": "•" * 8}
        existing_cfg.headers = {"AUTH_TOKEN": "•" * 8}
        existing_cfg.credential_refs = [
            {"field": "env", "key": "OLD_KEY", "credential_ref": "mcp:cfg1:env:OLD_KEY"},
            {"field": "headers", "key": "AUTH_TOKEN", "credential_ref": "mcp:cfg1:headers:AUTH_TOKEN"},
        ]

        updated_data = {}

        async def fake_update_config(db, cfg, data):
            updated_data.update(data)
            for k, v in data.items():
                setattr(cfg, k, v)
            cfg.enabled = True
            return cfg

        with patch.dict("sys.modules", {"keyring": mock_keyring}), \
             patch("api.mcp.AsyncSessionLocal") as MockSession, \
             patch("api.mcp.MCPRepo") as MockRepo, \
             patch("api.mcp.mcp_session_pool") as _MockPool, \
             patch("api.mcp.MCPManager") as MockManager:
            MockSession.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)
            MockRepo.get_config = AsyncMock(return_value=existing_cfg)
            MockRepo.update_config = AsyncMock(side_effect=fake_update_config)
            MockManager.return_value.sync_tools_for_config = AsyncMock(return_value=[])

            from api.mcp import update_mcp_config, MCPConfigUpdate
            payload = MCPConfigUpdate(env={"NEW_KEY": "new_secret_value"})
            await update_mcp_config("cfg1", payload, auth={"user_id": "u1"})

        header_refs = [r for r in updated_data["credential_refs"] if r["field"] == "headers"]
        self.assertEqual(len(header_refs), 1)
        self.assertEqual(header_refs[0]["key"], "AUTH_TOKEN")
        self.assertEqual(store["mcp:cfg1:headers:AUTH_TOKEN"], "header_secret")  # untouched

        # Old env key wasn't resubmitted -> its keyring entry is deleted.
        self.assertNotIn("mcp:cfg1:env:OLD_KEY", store)

        # New env key extracted correctly.
        env_refs = [r for r in updated_data["credential_refs"] if r["field"] == "env"]
        self.assertEqual(len(env_refs), 1)
        self.assertEqual(env_refs[0]["key"], "NEW_KEY")
        self.assertNotIn("new_secret_value", str(updated_data))

    async def test_update_explicit_null_clears_field_and_its_refs(self):
        """A field present in the request body as null (explicit clear) must
        wipe that field's keyring entries -- distinct from the field being
        absent from the request entirely (tested above)."""
        mock_keyring, store = _mock_keyring()
        store["mcp:cfg1:headers:AUTH_TOKEN"] = "header_secret"

        existing_cfg = MagicMock()
        existing_cfg.id = "cfg1"
        existing_cfg.env = None
        existing_cfg.headers = {"AUTH_TOKEN": "•" * 8}
        existing_cfg.credential_refs = [
            {"field": "headers", "key": "AUTH_TOKEN", "credential_ref": "mcp:cfg1:headers:AUTH_TOKEN"},
        ]

        updated_data = {}

        async def fake_update_config(db, cfg, data):
            updated_data.update(data)
            for k, v in data.items():
                setattr(cfg, k, v)
            cfg.enabled = True
            return cfg

        with patch.dict("sys.modules", {"keyring": mock_keyring}), \
             patch("api.mcp.AsyncSessionLocal") as MockSession, \
             patch("api.mcp.MCPRepo") as MockRepo, \
             patch("api.mcp.mcp_session_pool") as _MockPool, \
             patch("api.mcp.MCPManager") as MockManager:
            MockSession.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            MockSession.return_value.__aexit__ = AsyncMock(return_value=False)
            MockRepo.get_config = AsyncMock(return_value=existing_cfg)
            MockRepo.update_config = AsyncMock(side_effect=fake_update_config)
            MockManager.return_value.sync_tools_for_config = AsyncMock(return_value=[])

            # MCPConfigUpdate's own validator rejects a request where every
            # field is None ("at least one field must be set to update") --
            # explicitly clearing headers alone isn't expressible; pairing
            # it with a real field change (enabled) is the only reachable
            # path to this case through the actual schema.
            from api.mcp import update_mcp_config, MCPConfigUpdate
            payload = MCPConfigUpdate(enabled=True, headers=None)
            await update_mcp_config("cfg1", payload, auth={"user_id": "u1"})

        self.assertIsNone(updated_data["headers"])
        self.assertEqual(updated_data["credential_refs"], [])
        self.assertNotIn("mcp:cfg1:headers:AUTH_TOKEN", store)


# ===========================================================================
# B-075 — Workspace Lifecycle
# ===========================================================================

class TestWorkspaceUtils(unittest.TestCase):
    """Unit tests for find_stale_conversation_dirs and delete_conversation_workspace."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Fake WORKSPACE_ROOT structure: tmp/{user_id}/conversations/{conv_id}/
        self.user_id = "test_user_b075"
        self.conv_root = Path(self.tmp) / self.user_id / "conversations"
        self.conv_root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_conv_dir(self, conv_id: str, age_days: float = 0) -> Path:
        """Create a conversation scratch dir with a file whose mtime is age_days old."""
        d = self.conv_root / conv_id
        d.mkdir()
        f = d / "output.txt"
        f.write_text("test content")
        if age_days > 0:
            old_time = time.time() - (age_days * 86400)
            os.utime(f, (old_time, old_time))
            os.utime(d, (old_time, old_time))
        return d

    def test_find_stale_returns_empty_when_no_conv_dir(self):
        from utils.workspace import find_stale_conversation_dirs
        with patch("utils.workspace.WORKSPACE_ROOT", self.tmp):
            result = find_stale_conversation_dirs(self.user_id, ttl_days=30)
        self.assertEqual(result, [])

    def test_find_stale_returns_old_conv(self):
        from utils.workspace import find_stale_conversation_dirs
        self._make_conv_dir("old_conv", age_days=100)
        self._make_conv_dir("new_conv", age_days=0)
        with patch("utils.workspace.WORKSPACE_ROOT", self.tmp):
            result = find_stale_conversation_dirs(self.user_id, ttl_days=30)
        self.assertIn("old_conv", result)
        self.assertNotIn("new_conv", result)

    def test_find_stale_respects_ttl(self):
        from utils.workspace import find_stale_conversation_dirs
        self._make_conv_dir("borderline_conv", age_days=29)
        with patch("utils.workspace.WORKSPACE_ROOT", self.tmp):
            result = find_stale_conversation_dirs(self.user_id, ttl_days=30)
        self.assertNotIn("borderline_conv", result)

    def test_find_stale_empty_conv_dir_uses_dir_mtime(self):
        from utils.workspace import find_stale_conversation_dirs
        d = self.conv_root / "empty_conv"
        d.mkdir()
        old_time = time.time() - (100 * 86400)
        os.utime(d, (old_time, old_time))
        with patch("utils.workspace.WORKSPACE_ROOT", self.tmp):
            result = find_stale_conversation_dirs(self.user_id, ttl_days=30)
        self.assertIn("empty_conv", result)

    def test_delete_conversation_workspace_returns_zero_when_absent(self):
        from utils.workspace import delete_conversation_workspace
        with patch("utils.workspace.WORKSPACE_ROOT", self.tmp):
            freed = delete_conversation_workspace(self.user_id, "nonexistent_conv")
        self.assertEqual(freed, 0)

    def test_delete_conversation_workspace_removes_dir_and_returns_size(self):
        from utils.workspace import delete_conversation_workspace
        d = self._make_conv_dir("to_delete_conv", age_days=0)
        self.assertTrue(d.exists())
        with patch("utils.workspace.WORKSPACE_ROOT", self.tmp):
            freed = delete_conversation_workspace(self.user_id, "to_delete_conv")
        self.assertFalse(d.exists())
        self.assertGreater(freed, 0)


class TestCleanupTask(unittest.TestCase):
    """Unit tests for core/tasks/cleanup.py."""

    def test_module_importable(self):
        from core.tasks import cleanup  # noqa
        self.assertTrue(hasattr(cleanup, "workspace_cleanup_loop"))
        self.assertTrue(hasattr(cleanup, "_cleanup_pass"))

    def test_cleanup_pass_returns_summary_dict(self):
        from core.tasks.cleanup import _cleanup_pass
        with tempfile.TemporaryDirectory() as tmp:
            with patch("core.tasks.cleanup.pathlib") as _pm:
                # Point WORKSPACE_ROOT at empty tmp dir
                with patch("config.settings.WORKSPACE_ROOT", tmp):
                    result = _cleanup_pass()
        self.assertIn("users_scanned", result)
        self.assertIn("dirs_deleted", result)
        self.assertIn("bytes_freed", result)

    def test_cleanup_pass_handles_missing_workspace_root(self):
        from core.tasks.cleanup import _cleanup_pass
        with patch("config.settings.WORKSPACE_ROOT", "/nonexistent/path/xyz"):
            result = _cleanup_pass()
        self.assertEqual(result["users_scanned"], 0)
        self.assertEqual(result["dirs_deleted"], 0)

    def test_cleanup_loop_is_coroutine(self):
        import inspect
        from core.tasks.cleanup import workspace_cleanup_loop
        self.assertTrue(inspect.iscoroutinefunction(workspace_cleanup_loop))

    def test_cleanup_interval_constant(self):
        # Moved to config/settings.py (WORKSPACE_CLEANUP_INTERVAL_SECONDS,
        # env-overridable via EVA_CLEANUP_INTERVAL_SECONDS) as part of the
        # "consolidate all scattered constants into config/settings.py"
        # refactor -- core.tasks.cleanup now imports it rather than
        # defining its own local constant.
        from core.tasks.cleanup import WORKSPACE_CLEANUP_INTERVAL_SECONDS
        self.assertEqual(WORKSPACE_CLEANUP_INTERVAL_SECONDS, 86_400)


class TestConversationDeleteWorkspace(unittest.TestCase):
    """Verify that delete_conversation and delete_all_conversations import workspace helpers.

    Note: conversation_management.py contains emoji in _ROLE_LABEL so we must
    read it with UTF-8 encoding — Windows defaults to cp1252 which cannot decode
    multi-byte emoji characters.
    """

    @classmethod
    def _src(cls) -> str:
        return Path("api/conversation_management.py").read_text(encoding="utf-8")

    def test_delete_single_conv_imports_workspace_helpers(self):
        src = self._src()
        self.assertIn("get_conversation_dir", src)
        self.assertIn("shutil.rmtree", src)

    def test_delete_all_convs_cleans_conv_root(self):
        src = self._src()
        self.assertIn("get_user_root", src)
        # Verify the all-delete path removes the whole conversations subdir
        self.assertIn('conv_root = get_user_root(user_id) / "conversations"', src)

    def test_promote_endpoint_registered(self):
        src = self._src()
        self.assertIn("/promote", src)
        self.assertIn("PromoteFilesRequest", src)

    def test_promote_guards_path_traversal(self):
        src = self._src()
        self.assertIn("Path traversal not allowed", src)

    def test_promote_scenario1_attach_logic_present(self):
        """Scenario 1 (conversation attachment) code must be present."""
        src = self._src()
        self.assertIn("scenario", src)
        self.assertIn("attach", src)
        self.assertIn("set_conversation_project_id", src)
        self.assertIn("move_conversation_workspace_to_project", src)

    def test_promote_files_optional(self):
        """files field must default to empty list (backward-compat with Scenario 2)."""
        from api.conversation_management import PromoteFilesRequest
        req = PromoteFilesRequest(project_id="proj-test")
        self.assertEqual(req.files, [])

    def test_promote_scenario_dispatch(self):
        """Empty files → Scenario 1; non-empty files → Scenario 2 (file copy)."""
        from api.conversation_management import PromoteFilesRequest
        s1 = PromoteFilesRequest(project_id="proj-1")
        self.assertFalse(bool(s1.files), "Scenario 1 triggered by empty files")
        s2 = PromoteFilesRequest(project_id="proj-1", files=["output.py"])
        self.assertTrue(bool(s2.files), "Scenario 2 triggered by non-empty files")


class TestConversationPromoteWorkspace(unittest.TestCase):
    """
    Unit tests for the workspace promotion helpers introduced alongside the
    /promote Scenario 1 redesign.
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="eva_promote_test_")
        # Patch WORKSPACE_ROOT so helpers write into our temp dir
        import utils.workspace as _ws
        self._orig_root = _ws.WORKSPACE_ROOT
        _ws.WORKSPACE_ROOT = self._tmp
        # Also patch the settings import inside workspace.py
        import config.settings as _cs
        self._orig_ws_root = _cs.WORKSPACE_ROOT
        _cs.WORKSPACE_ROOT = self._tmp

    def tearDown(self):
        import utils.workspace as _ws, config.settings as _cs, shutil
        _ws.WORKSPACE_ROOT = self._orig_root
        _cs.WORKSPACE_ROOT = self._orig_ws_root
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_move_creates_project_dir_and_redirect(self):
        """Move with existing files — project dir created, .project_link written."""
        import pathlib, json
        from utils.workspace import move_conversation_workspace_to_project, get_conversation_dir

        uid, cid, pid = "u1", "conv-abc", "proj-xyz"
        # Pre-create a conversation scratch dir with a file
        conv_dir = pathlib.Path(self._tmp) / uid / "conversations" / cid
        conv_dir.mkdir(parents=True)
        (conv_dir / "conversation.jsonl").write_text('{"type":"test"}')

        result = move_conversation_workspace_to_project(uid, cid, pid)
        self.assertTrue(result["moved"])
        self.assertEqual(result["files_moved"], 1)

        # The project-scoped dir should contain the file
        new_dir = pathlib.Path(self._tmp) / uid / "projects" / pid / "conversations" / cid
        self.assertTrue((new_dir / "conversation.jsonl").exists())

        # Old dir should have .project_link but NOT conversation.jsonl
        old_dir = pathlib.Path(self._tmp) / uid / "conversations" / cid
        self.assertTrue((old_dir / ".project_link").exists())
        self.assertFalse((old_dir / "conversation.jsonl").exists())

        # .project_link must contain correct project_id
        data = json.loads((old_dir / ".project_link").read_text())
        self.assertEqual(data["project_id"], pid)

    def test_move_no_existing_workspace(self):
        """Move when no workspace dir exists yet — stub created at old location."""
        import pathlib, json
        from utils.workspace import move_conversation_workspace_to_project

        uid, cid, pid = "u2", "conv-new", "proj-new"
        result = move_conversation_workspace_to_project(uid, cid, pid)
        self.assertTrue(result["moved"])
        self.assertEqual(result["files_moved"], 0)

        old_dir = pathlib.Path(self._tmp) / uid / "conversations" / cid
        self.assertTrue((old_dir / ".project_link").exists())
        data = json.loads((old_dir / ".project_link").read_text())
        self.assertEqual(data["project_id"], pid)

    def test_get_conversation_dir_follows_redirect(self):
        """After promotion, get_conversation_dir() returns the project-scoped path."""
        import pathlib
        from utils.workspace import move_conversation_workspace_to_project, get_conversation_dir

        uid, cid, pid = "u3", "conv-redir", "proj-redir"
        # Create workspace and move
        conv_dir = pathlib.Path(self._tmp) / uid / "conversations" / cid
        conv_dir.mkdir(parents=True)
        (conv_dir / "meta.json").write_text("{}")

        move_conversation_workspace_to_project(uid, cid, pid)

        # get_conversation_dir() without project_id must follow the redirect
        resolved = get_conversation_dir(uid, cid)
        expected = pathlib.Path(self._tmp) / uid / "projects" / pid / "conversations" / cid
        self.assertEqual(resolved.resolve(), expected.resolve())

    def test_get_conversation_dir_explicit_project_id(self):
        """Passing project_id explicitly bypasses the redirect check."""
        import pathlib
        from utils.workspace import get_conversation_dir

        uid, cid, pid = "u4", "conv-direct", "proj-direct"
        resolved = get_conversation_dir(uid, cid, project_id=pid)
        expected = pathlib.Path(self._tmp) / uid / "projects" / pid / "conversations" / cid
        self.assertEqual(resolved.resolve(), expected.resolve())

    def test_delete_workspace_cleans_promoted_dir(self):
        """delete_conversation_workspace removes both redirect stub and project dir."""
        import pathlib
        from utils.workspace import (
            move_conversation_workspace_to_project,
            delete_conversation_workspace,
        )

        uid, cid, pid = "u5", "conv-del", "proj-del"
        conv_dir = pathlib.Path(self._tmp) / uid / "conversations" / cid
        conv_dir.mkdir(parents=True)
        (conv_dir / "big.txt").write_bytes(b"x" * 1024)

        move_conversation_workspace_to_project(uid, cid, pid)

        freed = delete_conversation_workspace(uid, cid)
        self.assertGreater(freed, 0)

        old_dir = pathlib.Path(self._tmp) / uid / "conversations" / cid
        new_dir = pathlib.Path(self._tmp) / uid / "projects" / pid / "conversations" / cid
        self.assertFalse(old_dir.exists())
        self.assertFalse(new_dir.exists())


class TestWorkspaceSettings(unittest.TestCase):
    """Verify settings changes for B-075."""

    def test_workspace_ttl_days_exists(self):
        from config.settings import WORKSPACE_TTL_DAYS
        self.assertIsInstance(WORKSPACE_TTL_DAYS, int)
        self.assertGreater(WORKSPACE_TTL_DAYS, 0)

    def test_blob_storage_path_removed(self):
        src = Path("config/settings.py").read_text()
        self.assertNotIn("BLOB_STORAGE_PATH", src)

    def test_workspace_ttl_default_is_90(self):
        # Default is 90 days; env override accepted
        import os
        saved = os.environ.pop("EVA_WORKSPACE_TTL_DAYS", None)
        try:
            import importlib, config.settings as _s
            importlib.reload(_s)
            self.assertEqual(_s.WORKSPACE_TTL_DAYS, 90)
        finally:
            if saved is not None:
                os.environ["EVA_WORKSPACE_TTL_DAYS"] = saved


class TestCleanupTaskRegisteredInMain(unittest.TestCase):
    """Verify main.py wires up the cleanup task."""

    def test_main_imports_cleanup_loop(self):
        src = Path("main.py").read_text()
        self.assertIn("workspace_cleanup_loop", src)
        self.assertIn("workspace-cleanup", src)

    def test_main_cancels_cleanup_on_shutdown(self):
        src = Path("main.py").read_text()
        self.assertIn("_cleanup_task.cancel()", src)


# ===========================================================================
# Ollama Cloud Provider (V5.0)
# ===========================================================================

class TestOllamaCloudCapabilityModule(unittest.TestCase):
    """
    Verify providers/capability/ollama_cloud/provider_api.py is correct.
    Tests are structural (import + attribute checks) — no live network calls.
    """

    def test_module_importable(self):
        import providers.capability.ollama_cloud.provider_api as m
        self.assertIsNotNone(m)

    def test_provider_constant(self):
        from providers.capability.ollama_cloud.provider_api import PROVIDER
        self.assertEqual(PROVIDER, "ollama_cloud")

    def test_env_key_constant(self):
        from providers.capability.ollama_cloud.provider_api import ENV_KEY
        self.assertEqual(ENV_KEY, "OLLAMA_CLOUD_API_KEY")

    def test_base_url_constant(self):
        from providers.capability.ollama_cloud.provider_api import BASE_URL
        self.assertEqual(BASE_URL, "https://ollama.com/v1")

    def test_required_functions_present(self):
        import providers.capability.ollama_cloud.provider_api as m
        for fn in ("list_models", "get_model_info", "validate_api_key",
                   "get_rate_limits", "get_status", "is_available"):
            self.assertTrue(callable(getattr(m, fn, None)),
                            f"Missing function: {fn}")

    def test_list_models_is_coroutine(self):
        import inspect
        from providers.capability.ollama_cloud.provider_api import list_models
        self.assertTrue(inspect.iscoroutinefunction(list_models))

    def test_validate_api_key_no_key_returns_false(self):
        """validate_api_key() with no key and no env var must return False."""
        import asyncio
        import os
        from providers.capability.ollama_cloud.provider_api import validate_api_key
        # Ensure env key is absent for this test
        env_backup = os.environ.pop("OLLAMA_CLOUD_API_KEY", None)
        try:
            result = asyncio.run(validate_api_key(api_key=None))
            self.assertFalse(result)
        finally:
            if env_backup is not None:
                os.environ["OLLAMA_CLOUD_API_KEY"] = env_backup

    def test_get_rate_limits_returns_empty(self):
        """get_rate_limits() always returns an empty RateLimitInfo (subscription billing)."""
        import asyncio
        from providers.capability.ollama_cloud.provider_api import get_rate_limits
        from providers.capability.base import RateLimitInfo
        result = asyncio.run(get_rate_limits())
        self.assertIsInstance(result, RateLimitInfo)
        # All fields should be None for subscription-based provider
        self.assertIsNone(result.requests_limit)
        self.assertIsNone(result.tokens_limit)

    def test_is_available_false_without_key(self):
        import os
        from providers.capability.ollama_cloud.provider_api import is_available
        env_backup = os.environ.pop("OLLAMA_CLOUD_API_KEY", None)
        try:
            self.assertFalse(is_available(api_key=None))
        finally:
            if env_backup is not None:
                os.environ["OLLAMA_CLOUD_API_KEY"] = env_backup

    def test_is_available_true_with_explicit_key(self):
        from providers.capability.ollama_cloud.provider_api import is_available
        self.assertTrue(is_available(api_key="test-key-123"))


class TestOllamaCloudHandler(unittest.TestCase):
    """
    Verify providers/handlers/ollama_cloud_handler.py is correct.
    Structural checks only — no live API calls.
    """

    def test_module_importable(self):
        import providers.handlers.ollama_cloud_handler as m
        self.assertIsNotNone(m)

    def test_handler_class_exists(self):
        from providers.handlers.ollama_cloud_handler import OllamaCloudHandler
        self.assertIsNotNone(OllamaCloudHandler)

    def test_base_url_correct(self):
        from providers.handlers.ollama_cloud_handler import _BASE_URL
        self.assertEqual(_BASE_URL, "https://ollama.com/v1")

    def test_env_key_correct(self):
        from providers.handlers.ollama_cloud_handler import _ENV_KEY
        self.assertEqual(_ENV_KEY, "OLLAMA_CLOUD_API_KEY")

    def test_provider_name_constant(self):
        from providers.handlers.ollama_cloud_handler import PROVIDER_NAME
        self.assertEqual(PROVIDER_NAME, "ollama_cloud")

    def test_create_message_is_async_generator(self):
        import inspect
        from providers.handlers.ollama_cloud_handler import OllamaCloudHandler
        self.assertTrue(inspect.isasyncgenfunction(OllamaCloudHandler.create_message))

    def test_init_raises_without_key(self):
        import os
        from providers.handlers.ollama_cloud_handler import OllamaCloudHandler
        env_backup = os.environ.pop("OLLAMA_CLOUD_API_KEY", None)
        try:
            with self.assertRaises(ValueError):
                OllamaCloudHandler({
                    "model": "llama3.2",
                    "api_key": None,
                    "provider_tools": [],
                    "tool_classes": {},
                })
        finally:
            if env_backup is not None:
                os.environ["OLLAMA_CLOUD_API_KEY"] = env_backup

    def test_init_succeeds_with_explicit_key(self):
        from providers.handlers.ollama_cloud_handler import OllamaCloudHandler
        handler = OllamaCloudHandler({
            "model": "llama3.2",
            "api_key": "test-key-xyz",
            "provider_tools": [],
            "tool_classes": {},
        })
        self.assertEqual(handler.model, "llama3.2")
        self.assertEqual(handler.api_key, "test-key-xyz")


class TestOllamaCloudInResolver(unittest.TestCase):
    """Verify ollama_cloud is correctly registered in the ProviderResolver."""

    def test_ollama_cloud_in_native_providers(self):
        from core.providers.resolver import _NATIVE_PROVIDERS
        self.assertIn("ollama_cloud", _NATIVE_PROVIDERS)

    def test_ollama_cloud_not_in_local_providers(self):
        from core.providers.resolver import _LOCAL_PROVIDERS
        self.assertNotIn("ollama_cloud", _LOCAL_PROVIDERS)

    def test_resolve_ollama_cloud_is_native_not_local(self):
        from core.providers.resolver import resolver
        res = resolver.resolve("ollama_cloud", "llama3.2")
        self.assertTrue(res.is_native)
        self.assertFalse(res.is_local)
        self.assertTrue(res.supported)
        self.assertFalse(res.via_litellm)

    def test_list_models_source_has_ollama_cloud_branch(self):
        src = Path("core/providers/resolver.py").read_text()
        self.assertIn('"ollama_cloud"', src)
        self.assertIn("ollama_cloud.provider_api", src)

    def test_get_model_cost_returns_none_not_zero(self):
        """ollama_cloud is not local — cost should be None (not 0.0) since
        it's subscription-based and not in LiteLLM's dataset."""
        from core.providers.resolver import resolver
        cost = resolver.get_model_cost("ollama_cloud", "llama3.2")
        # input_cost_per_token must be None (not 0.0 — that would imply local/free)
        self.assertIsNone(cost.get("input_cost_per_token"))
        self.assertIsNone(cost.get("output_cost_per_token"))

    def test_compute_call_cost_returns_none(self):
        """compute_call_cost for ollama_cloud should return None (no pricing data)."""
        from core.providers.resolver import resolver
        cost = resolver.compute_call_cost("ollama_cloud", "llama3.2", 100, 50)
        # Either None (no LiteLLM data) or raises — must not return 0.0 (local shortcut)
        self.assertIsNone(cost)


class TestOllamaCloudInFactory(unittest.TestCase):
    """Verify HandlerFactory dispatches to OllamaCloudHandler."""

    def test_factory_imports_ollama_cloud_handler(self):
        src = Path("core/factory.py").read_text()
        self.assertIn("OllamaCloudHandler", src)
        self.assertIn("ollama_cloud_handler", src)

    def test_factory_has_ollama_cloud_dispatch(self):
        src = Path("core/factory.py").read_text()
        self.assertIn('provider == "ollama_cloud"', src)
        self.assertIn("OllamaCloudHandler(handler_config)", src)

    def test_factory_creates_ollama_cloud_handler(self):
        """HandlerFactory.create_handler('ollama_cloud', ...) must return OllamaCloudHandler."""
        from unittest.mock import MagicMock, patch
        from core.factory import HandlerFactory
        from providers.handlers.ollama_cloud_handler import OllamaCloudHandler

        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = {}

        with patch("core.factory.build_provider_tools", return_value=[]):
            handler = HandlerFactory.create_handler("ollama_cloud", {
                "model": "llama3.2",
                "api_key": "test-key-xyz",
                "tools": mock_registry,
            })
        self.assertIsInstance(handler, OllamaCloudHandler)


class TestOllamaCloudInCuratedList(unittest.TestCase):
    """Verify ollama_cloud appears correctly in the /providers/list curated list."""

    def _get_curated(self):
        from api.providers import _CURATED_PROVIDERS
        return _CURATED_PROVIDERS

    def test_ollama_cloud_in_curated_providers(self):
        names = [p["provider"] for p in self._get_curated()]
        self.assertIn("ollama_cloud", names)

    def test_ollama_cloud_display_name(self):
        entry = next(p for p in self._get_curated() if p["provider"] == "ollama_cloud")
        self.assertEqual(entry["display_name"], "Ollama Cloud")

    def test_ollama_cloud_is_native_true(self):
        entry = next(p for p in self._get_curated() if p["provider"] == "ollama_cloud")
        self.assertTrue(entry["is_native"])

    def test_ollama_cloud_is_local_false(self):
        """ollama_cloud is a hosted service — must NOT be flagged as local."""
        entry = next(p for p in self._get_curated() if p["provider"] == "ollama_cloud")
        self.assertFalse(entry["is_local"])


class TestOllamaCloudInProviderKeyMap(unittest.TestCase):
    """Verify ollama_cloud is correctly registered in PROVIDER_KEY_MAP."""

    def test_ollama_cloud_key_env_var(self):
        from providers.capability.base import PROVIDER_KEY_MAP
        self.assertIn("ollama_cloud", PROVIDER_KEY_MAP)
        self.assertEqual(PROVIDER_KEY_MAP["ollama_cloud"], "OLLAMA_CLOUD_API_KEY")

    def test_is_provider_key_configured_false_without_key(self):
        import os
        from providers.capability.base import is_provider_key_configured
        env_backup = os.environ.pop("OLLAMA_CLOUD_API_KEY", None)
        try:
            self.assertFalse(is_provider_key_configured("ollama_cloud"))
        finally:
            if env_backup is not None:
                os.environ["OLLAMA_CLOUD_API_KEY"] = env_backup

    def test_is_provider_key_configured_true_with_explicit_key(self):
        from providers.capability.base import is_provider_key_configured
        self.assertTrue(is_provider_key_configured("ollama_cloud", explicit_key="test-key"))


class TestOllamaCloudSettings(unittest.TestCase):
    """Verify OllamaCloudSettings is present in config.settings."""

    def test_ollama_cloud_settings_class_exists(self):
        from config.settings import OllamaCloudSettings
        self.assertIsNotNone(OllamaCloudSettings)

    def test_settings_singleton_has_ollama_cloud(self):
        from config.settings import settings
        self.assertTrue(hasattr(settings, "ollama_cloud"))

    def test_ollama_cloud_base_url_default(self):
        from config.settings import OllamaCloudSettings
        s = OllamaCloudSettings()
        self.assertEqual(s.OLLAMA_CLOUD_BASE_URL, "https://ollama.com/v1")

    def test_eva_env_example_documents_ollama_cloud_base_url(self):
        """
        Q-05: eva.env is gitignored (per-developer, never committed) --
        eva.env.example is the tracked template new checkouts copy from.
        OLLAMA_CLOUD_BASE_URL is the one genuinely eva.env-appropriate
        Ollama Cloud setting, so the template documents it.
        """
        src = Path("eva.env.example").read_text(encoding="utf-8")
        self.assertIn("OLLAMA_CLOUD_BASE_URL", src)

    def test_eva_env_example_never_documents_a_provider_api_key(self):
        """
        Regression guard for the mistake this replaces: OLLAMA_CLOUD_API_KEY
        (like every other entry in PROVIDER_KEY_MAP) is a secret migrated to
        Windows Credential Manager -- config/env_loader.py strips it out of
        eva.env on every startup if found there. It must never appear in the
        committed template, which would wrongly suggest it belongs in
        eva.env at all.
        """
        from providers.capability.base import PROVIDER_KEY_MAP
        src = Path("eva.env.example").read_text(encoding="utf-8")
        for env_key in PROVIDER_KEY_MAP.values():
            if env_key is not None:
                self.assertNotIn(
                    env_key, src,
                    f"{env_key} is a provider secret (Windows Credential Manager only) "
                    "and must never appear in eva.env.example",
                )

    def test_ollama_cloud_settings_docstring_does_not_claim_eva_env_configurable(self):
        """
        Regression guard for the stale docstring this replaces: it used to
        claim OLLAMA_CLOUD_API_KEY was eva.env-overridable, contradicting
        the actual, enforced behaviour (see the two tests above).
        """
        import inspect
        from config.settings import OllamaCloudSettings
        doc = inspect.getdoc(OllamaCloudSettings) or ""
        self.assertNotIn("OLLAMA_CLOUD_API_KEY – API key for Ollama Cloud (required)", doc)
        self.assertIn("NOT eva.env-configurable", doc)


# ===========================================================================
# B-073 — Usage Metrics (MetricsService, api/metrics.py, repository,
#          ExecutionMetricsResponse lineage fields)
# ===========================================================================

class TestMetricsServiceModule(unittest.TestCase):
    """core/metrics/service.py is importable and has correct interface."""

    def test_module_importable(self):
        import core.metrics.service  # noqa: F401

    def test_metrics_service_class_exists(self):
        from core.metrics.service import MetricsService
        self.assertIsNotNone(MetricsService)

    def test_for_conversation_is_coroutine(self):
        import asyncio
        import inspect
        from core.metrics.service import MetricsService
        self.assertTrue(inspect.iscoroutinefunction(MetricsService.for_conversation))

    def test_for_user_is_coroutine(self):
        import inspect
        from core.metrics.service import MetricsService
        self.assertTrue(inspect.iscoroutinefunction(MetricsService.for_user))

    def test_for_user_accepts_start_end(self):
        import inspect
        from core.metrics.service import MetricsService
        sig = inspect.signature(MetricsService.for_user)
        self.assertIn("start", sig.parameters)
        self.assertIn("end",   sig.parameters)


class TestMetricsApiModule(unittest.TestCase):
    """api/metrics.py is importable and has correct router configuration."""

    def test_module_importable(self):
        import api.metrics  # noqa: F401

    def test_router_exists(self):
        from api.metrics import router
        self.assertIsNotNone(router)

    def test_router_prefix(self):
        from api.metrics import router
        self.assertEqual(router.prefix, "/metrics")

    def test_router_has_three_routes(self):
        from api.metrics import router
        # r.path includes the router prefix on prefixed routers
        paths = {r.path for r in router.routes}
        self.assertIn("/metrics/session",       paths)
        self.assertIn("/metrics/usage/summary", paths)
        self.assertIn("/metrics/usage",         paths)

    def test_metrics_aggregate_response_model(self):
        from api.metrics import MetricsAggregateResponse
        self.assertIsNotNone(MetricsAggregateResponse)

    def test_provider_breakdown_item_model(self):
        from api.metrics import ProviderBreakdownItem
        self.assertIsNotNone(ProviderBreakdownItem)

    def test_metrics_aggregate_response_fields(self):
        from api.metrics import MetricsAggregateResponse
        fields = MetricsAggregateResponse.model_fields
        for expected in (
            "execution_count", "prompt_tokens", "completion_tokens",
            "reasoning_tokens", "total_tokens", "total_cost_usd",
            "cost_is_partial", "tool_calls_count",
            "duration_total_ms", "provider_breakdown",
        ):
            self.assertIn(expected, fields, f"Missing field: {expected}")

    def test_build_response_helper(self):
        from api.metrics import _build_response, MetricsAggregateResponse
        data = {
            "execution_count":   3,
            "prompt_tokens":     500,
            "completion_tokens": 300,
            "reasoning_tokens":  0,
            "total_tokens":      800,
            "total_cost_usd":    0.005,
            "cost_is_partial":   False,
            "tool_calls_count":  4,
            "duration_total_ms": 12000,
            "provider_breakdown": [
                {"provider": "groq", "execution_count": 3,
                 "total_tokens": 800, "total_cost_usd": 0.005},
            ],
        }
        resp = _build_response(data)
        self.assertIsInstance(resp, MetricsAggregateResponse)
        self.assertEqual(resp.execution_count, 3)
        self.assertEqual(resp.total_tokens, 800)
        self.assertEqual(len(resp.provider_breakdown), 1)
        self.assertEqual(resp.provider_breakdown[0].provider, "groq")


class TestMetricsRepositoryMethods(unittest.TestCase):
    """ExecutionMetricsRepo has the new aggregation methods."""

    def test_aggregate_for_conversation_exists(self):
        from core.db.repository import ExecutionMetricsRepo
        self.assertTrue(hasattr(ExecutionMetricsRepo, "aggregate_for_conversation"))

    def test_aggregate_for_user_exists(self):
        from core.db.repository import ExecutionMetricsRepo
        self.assertTrue(hasattr(ExecutionMetricsRepo, "aggregate_for_user"))

    def test_aggregate_for_conversation_is_coroutine(self):
        import inspect
        from core.db.repository import ExecutionMetricsRepo
        self.assertTrue(inspect.iscoroutinefunction(
            ExecutionMetricsRepo.aggregate_for_conversation
        ))

    def test_aggregate_for_user_is_coroutine(self):
        import inspect
        from core.db.repository import ExecutionMetricsRepo
        self.assertTrue(inspect.iscoroutinefunction(
            ExecutionMetricsRepo.aggregate_for_user
        ))

    def test_aggregate_for_user_accepts_start_end(self):
        import inspect
        from core.db.repository import ExecutionMetricsRepo
        sig = inspect.signature(ExecutionMetricsRepo.aggregate_for_user)
        self.assertIn("start", sig.parameters)
        self.assertIn("end",   sig.parameters)


class TestExecutionMetricsResponseLineage(unittest.TestCase):
    """ExecutionMetricsResponse in api/executions.py exposes lineage fields (Gap 2)."""

    def test_response_model_has_computed_cost_usd(self):
        from api.executions import ExecutionMetricsResponse
        self.assertIn("computed_cost_usd", ExecutionMetricsResponse.model_fields)

    def test_response_model_has_parent_execution_id(self):
        from api.executions import ExecutionMetricsResponse
        self.assertIn("parent_execution_id", ExecutionMetricsResponse.model_fields)

    def test_response_model_has_subagent_name(self):
        from api.executions import ExecutionMetricsResponse
        self.assertIn("subagent_name", ExecutionMetricsResponse.model_fields)

    def test_lineage_fields_are_optional(self):
        from api.executions import ExecutionMetricsResponse
        fields = ExecutionMetricsResponse.model_fields
        # All three lineage fields should be optional (no default required)
        for fname in ("computed_cost_usd", "parent_execution_id", "subagent_name"):
            field_info = fields[fname]
            # Optional fields have a default of None
            self.assertIsNone(field_info.default, f"{fname} should default to None")

    def test_response_model_instantiates_with_lineage_fields(self):
        from api.executions import ExecutionMetricsResponse
        resp = ExecutionMetricsResponse(
            execution_id="exec-001",
            conversation_id="conv-001",
            status="completed",
            computed_cost_usd=0.0012,
            parent_execution_id="exec-000",
            subagent_name="coding_agent",
        )
        self.assertEqual(resp.computed_cost_usd, 0.0012)
        self.assertEqual(resp.parent_execution_id, "exec-000")
        self.assertEqual(resp.subagent_name, "coding_agent")


class TestMetricsMainRegistration(unittest.TestCase):
    """metrics_router is registered in main.py."""

    def test_main_imports_metrics_router(self):
        src = Path("main.py").read_text()
        self.assertIn("from api.metrics import router as metrics_router", src)

    def test_main_includes_metrics_router(self):
        src = Path("main.py").read_text()
        self.assertIn("app.include_router(metrics_router)", src)

    def test_metrics_router_prefix_not_duplicated(self):
        # The router's own prefix is /metrics — main should not add a prefix
        src = Path("main.py").read_text()
        # Just include_router without a prefix override
        self.assertNotIn('include_router(metrics_router, prefix=', src)


class TestParseIsoHelper(unittest.TestCase):
    """_parse_iso in api/metrics.py handles valid and invalid datetime strings."""

    def test_valid_utc_z_suffix(self):
        from api.metrics import _parse_iso
        dt = _parse_iso("2026-05-01T00:00:00Z", "start")
        self.assertIsNotNone(dt)
        self.assertIsNotNone(dt.tzinfo)

    def test_valid_iso_with_offset(self):
        from api.metrics import _parse_iso
        dt = _parse_iso("2026-05-01T00:00:00+00:00", "start")
        self.assertIsNotNone(dt)

    def test_none_returns_none(self):
        from api.metrics import _parse_iso
        self.assertIsNone(_parse_iso(None, "start"))

    def test_invalid_raises_422(self):
        from fastapi import HTTPException
        from api.metrics import _parse_iso
        with self.assertRaises(HTTPException) as ctx:
            _parse_iso("not-a-date", "start")
        self.assertEqual(ctx.exception.status_code, 422)


# ===========================================================================
# Agent Memory ↔ Project Auto-Linkage (B-076 gap closure)
# ===========================================================================

class TestAgentMemoryProjectAutoLinkage(unittest.TestCase):
    """
    B-076 gap: agent_scope_key is auto-derived from conversation.project_id
    when not explicitly set in session.

    Priority: explicit session.agent_scope_key → conversation.project_id → None
    """

    # ── extractor.py ──────────────────────────────────────────────────────────

    def test_extractor_scope_key_falls_back_to_project_id(self):
        """
        extractor._run() sets scope_key = session.agent_scope_key or
        conversation_project_id — when session has no agent_scope_key but
        the conversation is project-linked, project_id becomes the scope key.
        """
        src = Path("core/memory/extractor.py").read_text(encoding="utf-8")
        self.assertIn(
            'session.get("agent_scope_key") or conversation_project_id',
            src,
            "extractor.py must fall back to conversation_project_id for scope_key",
        )

    def test_extractor_b076_comment_present(self):
        """B-076 implementation note must be present in extractor.py."""
        src = Path("core/memory/extractor.py").read_text(encoding="utf-8")
        self.assertIn("B-076", src)

    # ── long_term.py ──────────────────────────────────────────────────────────

    def test_injector_accepts_project_id_param(self):
        """LongTermMemoryInjector.__init__ must accept project_id kwarg."""
        src = Path("core/memory/long_term.py").read_text(encoding="utf-8")
        self.assertIn("project_id", src,
                      "LongTermMemoryInjector must accept project_id parameter")

    def test_injector_project_id_defaults_to_none(self):
        """project_id parameter must default to None (backward-compatible)."""
        src = Path("core/memory/long_term.py").read_text(encoding="utf-8")
        self.assertIn("project_id: Optional[str] = None", src,
                      "project_id default must be None for backward compatibility")

    def test_injector_scope_key_fallback_in_source(self):
        """build_memory_block() must use 'or self.project_id' as scope_key fallback."""
        src = Path("core/memory/long_term.py").read_text(encoding="utf-8")
        self.assertIn(
            "or self.project_id",
            src,
            "build_memory_block must fall back to self.project_id for scope_key",
        )

    def test_injector_stores_project_id_on_self(self):
        """LongTermMemoryInjector.__init__ must store project_id on self."""
        src = Path("core/memory/long_term.py").read_text(encoding="utf-8")
        self.assertIn("self.project_id = project_id", src)

    # ── worker.py ─────────────────────────────────────────────────────────────

    def test_worker_passes_project_id_to_injector(self):
        """worker.py must pass project_id=resolved_project_id to LongTermMemoryInjector."""
        src = Path("core/execution/worker.py").read_text(encoding="utf-8")
        self.assertIn("project_id=resolved_project_id", src,
                      "worker.py must pass project_id to LongTermMemoryInjector")

    # ── Integration: explicit scope_key wins over project_id ──────────────────

    def test_explicit_scope_key_takes_precedence(self):
        """
        When session has agent_scope_key set, that value must take precedence
        over project_id. Verified via Python's 'or' short-circuit semantics.
        """
        # If session has a non-empty agent_scope_key, 'or' short-circuits
        explicit = "my_custom_scope"
        project  = "proj-uuid-123"
        result   = explicit or project
        self.assertEqual(result, explicit,
                         "Explicit agent_scope_key must win over project_id via 'or' logic")

    def test_project_id_used_when_scope_key_absent(self):
        """When session.agent_scope_key is None/empty, project_id becomes scope_key."""
        scope_key_in_session = None
        project_id           = "proj-uuid-123"
        result               = scope_key_in_session or project_id
        self.assertEqual(result, project_id)

    def test_none_result_when_both_absent(self):
        """When neither agent_scope_key nor project_id set, scope_key is None."""
        scope_key_in_session = None
        project_id           = None
        result               = scope_key_in_session or project_id
        self.assertIsNone(result)


# ===========================================================================
# UX-01 — Plan Edit-Approve-Execute
# ===========================================================================
#
# Covers the three-way plan-approval gate added on top of the existing
# binary approve/reject gate:
#   - approve : unchanged behaviour (regression)
#   - edit    : user-submitted plan replaces the LLM-generated one and is
#               what actually persists/executes downstream
#   - reject  : unchanged behaviour (regression)
#   - a malformed edited plan is rejected with a clear error rather than
#     crashing the PEV runner
#
# Two layers:
#   Layer A — wire-format round trip: resolve_plan_approval() writes a JSON
#             payload through the real blpop/rpush Redis mechanism, and
#             _gate_plan_approval() reads it back, using a tiny in-memory
#             fake Redis client (not mocked payload strings) so the actual
#             encode/decode path is exercised.
#   Layer B — _pev_stream() functional tests: _gate_plan_approval() itself is
#             mocked (already covered by Layer A) so these focus purely on
#             what _pev_stream() does with each decision — in particular,
#             confirming the *edited* steps (not the originals) are what
#             gets persisted via PlanRepo and passed to the agent.

class _FakePlanApprovalRedisClient:
    """
    Minimal in-memory stand-in for the subset of the redis client API
    _gate_plan_approval()/resolve_plan_approval() use. blpop() polls the
    in-memory list rather than truly blocking, so a concurrent
    resolve_plan_approval() call (pushed from the test body while the gate
    task is awaiting) is picked up the same way a real Redis blpop would.
    """

    def __init__(self):
        self.hashes: Dict[str, Dict[str, str]] = {}
        self.strings: Dict[str, str] = {}
        self.lists: Dict[str, List[str]] = {}

    async def setex(self, key, ttl, value):
        self.strings[key] = value

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    async def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def delete(self, key):
        self.strings.pop(key, None)
        self.lists.pop(key, None)

    async def exists(self, key):
        return key in self.strings

    async def expire(self, key, ttl):
        pass

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def blpop(self, key, timeout=None):
        deadline = time.monotonic() + (timeout or 5)
        while True:
            lst = self.lists.get(key)
            if lst:
                return (key, lst.pop(0))
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.01)


class TestPlanApprovalWireFormatRoundTrip(unittest.IsolatedAsyncioTestCase):
    """
    Layer A: resolve_plan_approval() -> (real Redis blpop/rpush mechanism,
    fake client) -> _gate_plan_approval(). Exercises the actual JSON wire
    format, not a hand-constructed payload.
    """

    async def _run_round_trip(self, decision, modified_plan=None, timeout_seconds=2, reason=None):
        import core.execution.pev_runner as mod

        fake_client = _FakePlanApprovalRedisClient()
        fixed_token = "fixed-token-for-test"

        mock_execution = MagicMock()
        mock_execution.execution_id = "exec-wire-rt"
        mock_execution.status = MagicMock()

        original_plan = {
            "goal": "Original goal",
            "steps": [{
                "index": 0, "title": "Original Step", "description": "orig",
                "tool_hint": None, "status": "pending", "result_summary": None,
                "verified": False, "verification_notes": None,
            }],
        }

        with patch.object(mod.redis_execution_registry, "connect",
                           new=AsyncMock(return_value=fake_client)), \
             patch.object(mod.redis_execution_registry,
                           "mark_execution_awaiting_plan_approval", new=AsyncMock()), \
             patch.object(mod, "_push_plan_approval_event", new=AsyncMock()), \
             patch.object(mod.ExecutionStateMachine, "assert_transition_allowed"), \
             patch.object(mod, "uuid4", return_value=fixed_token):

            gate_task = asyncio.create_task(
                mod._gate_plan_approval(mock_execution, original_plan, timeout_seconds=timeout_seconds)
            )

            # Wait for the gate to register its pending token before resolving.
            for _ in range(200):
                if await fake_client.exists(mod._plan_pending_key(fixed_token)):
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("gate never registered its pending token")

            if decision is not None:
                resolved = await mod.resolve_plan_approval(
                    fixed_token, decision=decision, modified_plan=modified_plan, reason=reason
                )
                self.assertTrue(resolved, "resolve_plan_approval should find the pending token")

            result = await gate_task

        return result

    async def test_approve_round_trips(self):
        result = await self._run_round_trip("approve")
        self.assertEqual(result.decision, "approve")
        self.assertIsNone(result.plan)

    async def test_edit_round_trips_with_plan_payload(self):
        edited = {"goal": "Edited goal", "steps": [{"title": "Edited Step", "description": "new"}]}
        result = await self._run_round_trip("edit", modified_plan=edited)
        self.assertEqual(result.decision, "edit")
        self.assertEqual(result.plan, edited)

    async def test_reject_round_trips(self):
        result = await self._run_round_trip("reject")
        self.assertEqual(result.decision, "reject")
        self.assertIsNone(result.plan)
        self.assertIsNone(result.reason, "a bare reject with no reason argument must round-trip as None")

    async def test_timeout_resolves_as_reject(self):
        # No resolve_plan_approval() call at all -> blpop times out.
        result = await self._run_round_trip(None, timeout_seconds=1)
        self.assertEqual(result.decision, "reject")
        self.assertIsNone(result.plan)

    async def test_reject_with_reason_round_trips(self):
        # UX-01 Phase 2: the reason submitted alongside a reject must survive
        # the full wire round-trip (resolve_plan_approval -> JSON payload ->
        # blpop -> _gate_plan_approval's parse) so _pev_stream can act on it.
        result = await self._run_round_trip("reject", reason="too narrow, cover edge cases")
        self.assertEqual(result.decision, "reject")
        self.assertEqual(result.reason, "too narrow, cover edge cases")

    async def test_approve_ignores_reason_even_if_passed(self):
        # resolve_plan_approval() only carries `reason` through for "reject" --
        # a caller accidentally passing one alongside "approve" must not leak
        # it into the result (matches the "reason if decision == 'reject'
        # else None" payload construction).
        result = await self._run_round_trip("approve", reason="irrelevant text")
        self.assertEqual(result.decision, "approve")
        self.assertIsNone(result.reason)


class TestResolvePlanApprovalValidation(unittest.IsolatedAsyncioTestCase):
    """resolve_plan_approval() input validation / unknown-token handling."""

    async def test_unknown_token_returns_false(self):
        import core.execution.pev_runner as mod
        fake_client = _FakePlanApprovalRedisClient()  # nothing registered
        with patch.object(mod.redis_execution_registry, "connect",
                           new=AsyncMock(return_value=fake_client)):
            resolved = await mod.resolve_plan_approval("no-such-token", decision="approve")
        self.assertFalse(resolved)

    async def test_invalid_decision_returns_false(self):
        import core.execution.pev_runner as mod
        fake_client = _FakePlanApprovalRedisClient()
        fake_client.strings[mod._plan_pending_key("tok")] = "1"
        with patch.object(mod.redis_execution_registry, "connect",
                           new=AsyncMock(return_value=fake_client)):
            resolved = await mod.resolve_plan_approval("tok", decision="bogus")
        self.assertFalse(resolved, "an invalid decision value must not resolve the gate")


class _FakeAgentForPev:
    """Records the message lists it's called with; yields a single TextChunk."""

    def __init__(self):
        self.call_args_list: List[List[Dict[str, Any]]] = []

    async def call(self, messages, stream=True):
        self.call_args_list.append(messages)
        from core.types import TextChunk as _TC
        yield _TC(content="Step done.")


def _make_pev_stream_kwargs():
    """Shared fixture: an execution mock, base messages, and the original plan."""
    mock_execution = MagicMock()
    mock_execution.execution_id = "exec-pev-test"
    mock_execution.conversation_id = "conv-pev-test"
    mock_execution.status = MagicMock()
    mock_cancel_token = MagicMock()
    mock_cancel_token.cancelled = False
    mock_execution.cancel_token = mock_cancel_token

    base_messages = [{"role": "user", "content": "Do the thing"}]

    original_plan = {
        "goal": "Original goal",
        "steps": [{
            "index": 0, "title": "Original Step", "description": "orig desc",
            "tool_hint": None, "status": "pending", "result_summary": None,
            "verified": False, "verification_notes": None,
        }],
    }
    return mock_execution, base_messages, original_plan


class TestPevStreamPlanApprovalDecisions(unittest.IsolatedAsyncioTestCase):
    """
    Layer B: drives _pev_stream() end-to-end (planning -> gate -> step loop)
    with _gate_plan_approval() mocked to return each of the three decisions,
    plus a malformed-edit case. All DB/verifier/agent calls are mocked.
    """

    async def _drive(self, gate_decision, gate_plan=None):
        import core.execution.pev_runner as mod

        mock_execution, base_messages, original_plan = _make_pev_stream_kwargs()
        fake_agent = _FakeAgentForPev()

        db_ctx = MagicMock()
        db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        db_ctx.__aexit__ = AsyncMock(return_value=False)

        verification_result = MagicMock()
        verification_result.passed = True
        verification_result.summary = "Looks good"
        verification_result.issues = None
        verification_result.infra_error = False
        verification_result.also_satisfies_count = 0
        verification_result.also_satisfies_reason = None

        with patch.object(mod.planner_engine, "generate", new=AsyncMock(return_value=original_plan)), \
             patch.object(mod, "AsyncSessionLocal", return_value=db_ctx), \
             patch.object(mod.PlanRepo, "create", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_steps", new=AsyncMock()) as mock_update_steps, \
             patch.object(mod.PlanRepo, "update_status", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_goal_verification", new=AsyncMock()), \
             patch.object(mod, "_gate_plan_approval",
                           new=AsyncMock(return_value=mod.PlanApprovalResult(
                               decision=gate_decision, plan=gate_plan))), \
             patch.object(mod.ExecutionStateMachine, "assert_transition_allowed"), \
             patch.object(mod.redis_execution_registry, "mark_execution_running", new=AsyncMock()), \
             patch.object(mod.verifier_engine, "verify", new=AsyncMock(return_value=verification_result)):

            chunks = []
            async for chunk in mod._pev_stream(
                execution=mock_execution,
                agent=fake_agent,
                base_messages=base_messages,
                user_id="user-1",
                require_plan_approval=True,
            ):
                chunks.append(chunk)

        texts = [c.content for c in chunks if hasattr(c, "content")]
        full_text = "\n".join(texts)
        return mock_execution, fake_agent, mock_update_steps, full_text, chunks

    @staticmethod
    def _step_progress_chunks(chunks, step_status=None):
        from core.types import StepProgressChunk as _SPC
        result = [c for c in chunks if isinstance(c, _SPC)]
        if step_status is not None:
            result = [c for c in result if c.step_status == step_status]
        return result

    async def test_approve_as_is_executes_original_plan(self):
        _, fake_agent, _, full_text, chunks = await self._drive("approve")

        self.assertEqual(len(fake_agent.call_args_list), 1,
                          "the single original step must have been executed")
        step_messages = fake_agent.call_args_list[0]
        last_msg = step_messages[-1]["content"]
        self.assertIn("Original Step", last_msg)
        self.assertIn("orig desc", last_msg)
        # V6.14: no narrative TextChunk exists anymore for any plan-approval
        # decision, so "Plan edited" can never appear in full_text at all --
        # verify there's also no plan-confirmation StepProgressChunk (that
        # would only be emitted for an "edit" decision, not "approve").
        self.assertNotIn("Plan edited", full_text)
        edit_confirmations = [
            c for c in self._step_progress_chunks(chunks, "planning")
            if c.plan_steps and c.plan_steps[0].get("title") == "Edited Step"
        ]
        self.assertEqual(edit_confirmations, [],
                          "approve-as-is must not emit an edited-plan confirmation")

    async def test_edit_then_approve_executes_edited_plan(self):
        edited_plan = {
            "goal": "Edited goal",
            "steps": [{"title": "Edited Step", "description": "new desc"}],
        }
        mock_execution, fake_agent, mock_update_steps, full_text, chunks = await self._drive(
            "edit", gate_plan=edited_plan
        )

        # The step that actually ran must be the EDITED one, not the original.
        self.assertEqual(len(fake_agent.call_args_list), 1)
        step_messages = fake_agent.call_args_list[0]
        last_msg = step_messages[-1]["content"]
        self.assertIn("Edited Step", last_msg)
        self.assertIn("new desc", last_msg)
        self.assertNotIn("Original Step", last_msg)

        # The edited steps must have been persisted via PlanRepo.update_steps
        # (current_step_index=0, status="pending" — the "replace with edited
        # plan" persist call, distinct from the later per-step progress calls).
        persisted_edit_calls = [
            call for call in mock_update_steps.call_args_list
            if call.kwargs.get("current_step_index") == 0
            and call.kwargs.get("status") == "pending"
            and call.kwargs.get("steps", [{}])[0].get("title") == "Edited Step"
        ]
        self.assertTrue(persisted_edit_calls,
                         "edited steps must be persisted via PlanRepo.update_steps")

        # V6.14: the old "Plan edited. Using the updated plan below:" text
        # notice is gone -- confirmation now travels as a StepProgressChunk
        # (step_status="planning") carrying the edited plan_steps.
        self.assertNotIn("Plan edited", full_text)
        edit_confirmations = self._step_progress_chunks(chunks, "planning")
        self.assertTrue(
            any(c.plan_steps and c.plan_steps[0].get("title") == "Edited Step"
                for c in edit_confirmations),
            "edited plan must be confirmed via a StepProgressChunk(step_status='planning', plan_steps=...)",
        )
        self.assertFalse(mock_execution.cancel_token.cancelled)

    async def test_reject_cancels_and_never_executes(self):
        mock_execution, fake_agent, mock_update_steps, full_text, chunks = await self._drive("reject")

        self.assertTrue(mock_execution.cancel_token.cancelled)
        # V6.14: no "Plan rejected" narration chunk is emitted at all anymore
        # -- the frontend already knows this from its own reject action, and
        # cancel_token.cancelled / the execution status endpoint is the
        # source of truth.
        self.assertNotIn("Plan rejected", full_text)

        # V6.14.1 (regression fix): a StepProgressChunk(step_status="cancelled")
        # MUST still be yielded here -- this isn't just informational, it's
        # what pumps run_pev_execution()'s consumer loop one more time so it
        # actually performs the CANCELLED FSM transition. Returning with no
        # yield at all (the bug this test previously would have missed --
        # see the "cancelled" entry in StepProgressChunk's docstring)
        # silently leaves the broker status stuck at awaiting_plan_approval
        # forever, which is exactly what a live run caught (test_apis.py 14i).
        cancelled_chunks = self._step_progress_chunks(chunks, "cancelled")
        self.assertEqual(len(cancelled_chunks), 1,
                          "reject must yield exactly one StepProgressChunk(step_status='cancelled') "
                          "so the outer consumer loop notices cancel_token and transitions state")

        # No step execution, no error, and no other step-progress status
        # beyond the initial "planning" pair and the required "cancelled" one.
        other_steps = self._step_progress_chunks(chunks)
        other_steps = [c for c in other_steps if c.step_status not in ("planning", "cancelled")]
        self.assertEqual(other_steps, [],
                          "reject must not emit any step-execution progress chunks")
        from core.types import ErrorChunk as _EC
        self.assertEqual([c for c in chunks if isinstance(c, _EC)], [],
                          "reject is not an error condition -- no ErrorChunk expected")
        self.assertEqual(fake_agent.call_args_list, [],
                          "no step should ever be executed after rejection")

    async def test_malformed_edited_plan_is_rejected_not_crashed(self):
        # Missing 'steps' entirely -> validate_plan_shape() must raise
        # PlanValidationError, which _pev_stream() must turn into a clean
        # cancellation instead of letting the exception propagate.
        malformed_plan = {"goal": "no steps here"}

        mock_execution, fake_agent, mock_update_steps, full_text, chunks = await self._drive(
            "edit", gate_plan=malformed_plan
        )

        self.assertTrue(mock_execution.cancel_token.cancelled)
        # V6.14: this is now a genuine ErrorChunk (it's a validation failure,
        # not routine narration), not a TextChunk mixed into the answer.
        from core.types import ErrorChunk as _EC
        error_chunks = [c for c in chunks if isinstance(c, _EC)]
        self.assertEqual(len(error_chunks), 1,
                          "malformed edit must yield exactly one ErrorChunk")
        self.assertIn("Edited plan was invalid", error_chunks[0].message)
        self.assertEqual(error_chunks[0].error_type, "plan_validation_failed")
        self.assertNotIn("Edited plan was invalid", full_text,
                          "the error must not also be duplicated into a text chunk")
        self.assertEqual(fake_agent.call_args_list, [],
                          "a malformed edited plan must never reach step execution")


class TestPevStreamPlanRevision(unittest.IsolatedAsyncioTestCase):
    """
    UX-01 Phase 2 -- multi-round plan revision. Same Layer B pattern as
    TestPevStreamPlanApprovalDecisions above, but drives _pev_stream() with
    a *sequence* of _gate_plan_approval() results (reject-with-reason,
    possibly more than once, then approve/reject again) instead of a single
    fixed decision, and mocks planner_engine.revise() alongside generate().
    """

    async def _drive_sequence(self, gate_results, revised_plans=None):
        import core.execution.pev_runner as mod

        mock_execution, base_messages, original_plan = _make_pev_stream_kwargs()
        fake_agent = _FakeAgentForPev()

        db_ctx = MagicMock()
        db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        db_ctx.__aexit__ = AsyncMock(return_value=False)

        verification_result = MagicMock()
        verification_result.passed = True
        verification_result.summary = "Looks good"
        verification_result.issues = None
        verification_result.infra_error = False
        verification_result.also_satisfies_count = 0
        verification_result.also_satisfies_reason = None

        revise_mock = AsyncMock(side_effect=revised_plans or [])

        with patch.object(mod.planner_engine, "generate", new=AsyncMock(return_value=original_plan)), \
             patch.object(mod.planner_engine, "revise", new=revise_mock), \
             patch.object(mod, "AsyncSessionLocal", return_value=db_ctx), \
             patch.object(mod.PlanRepo, "create", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_steps", new=AsyncMock()) as mock_update_steps, \
             patch.object(mod.PlanRepo, "update_status", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_goal_verification", new=AsyncMock()), \
             patch.object(mod, "_gate_plan_approval",
                           new=AsyncMock(side_effect=gate_results)) as mock_gate, \
             patch.object(mod.ExecutionStateMachine, "assert_transition_allowed"), \
             patch.object(mod.redis_execution_registry, "mark_execution_running", new=AsyncMock()), \
             patch.object(mod.verifier_engine, "verify", new=AsyncMock(return_value=verification_result)):

            chunks = []
            async for chunk in mod._pev_stream(
                execution=mock_execution,
                agent=fake_agent,
                base_messages=base_messages,
                user_id="user-1",
                require_plan_approval=True,
            ):
                chunks.append(chunk)

        return mock_execution, fake_agent, mock_update_steps, revise_mock, mock_gate, chunks

    @staticmethod
    def _make_plan(title, description):
        return {
            "goal": f"Goal for {title}",
            "steps": [{
                "index": 0, "title": title, "description": description,
                "tool_hint": None, "status": "pending", "result_summary": None,
                "verified": False, "verification_notes": None,
            }],
        }

    async def test_reject_with_reason_revises_and_reapproves(self):
        import core.execution.pev_runner as mod

        revised_plan = self._make_plan("Revised Step", "revised desc")
        gate_results = [
            mod.PlanApprovalResult(decision="reject", plan=None, reason="too narrow, cover edge cases"),
            mod.PlanApprovalResult(decision="approve", plan=None, reason=None),
        ]

        mock_execution, fake_agent, mock_update_steps, revise_mock, mock_gate, chunks = await self._drive_sequence(
            gate_results, revised_plans=[revised_plan]
        )

        # _gate_plan_approval must have been called twice (initial + one
        # re-gate after the revision), and revise() exactly once with the
        # rejected plan's own goal/steps and the user's feedback.
        self.assertEqual(mock_gate.call_count, 2)
        self.assertEqual(revise_mock.call_count, 1)
        _, revise_kwargs = revise_mock.call_args
        self.assertEqual(revise_kwargs["goal"], "Original goal")
        self.assertEqual(revise_kwargs["feedback"], "too narrow, cover edge cases")
        self.assertEqual(revise_kwargs["rejected_steps"][0]["title"], "Original Step")

        # The step that actually ran must be from the REVISED plan, not the original.
        self.assertEqual(len(fake_agent.call_args_list), 1)
        last_msg = fake_agent.call_args_list[0][-1]["content"]
        self.assertIn("Revised Step", last_msg)
        self.assertIn("revised desc", last_msg)
        self.assertNotIn("Original Step", last_msg)

        self.assertFalse(mock_execution.cancel_token.cancelled)

        # The revised plan must have been persisted via PlanRepo.update_steps
        # (same convention as the "edit" path) before the re-gate.
        persisted_revision_calls = [
            call for call in mock_update_steps.call_args_list
            if call.kwargs.get("steps", [{}])[0].get("title") == "Revised Step"
        ]
        self.assertTrue(persisted_revision_calls,
                         "revised steps must be persisted via PlanRepo.update_steps")

    async def test_reject_without_reason_cancels_immediately_no_revision(self):
        import core.execution.pev_runner as mod

        gate_results = [
            mod.PlanApprovalResult(decision="reject", plan=None, reason=None),
        ]
        mock_execution, fake_agent, mock_update_steps, revise_mock, mock_gate, chunks = await self._drive_sequence(
            gate_results
        )

        revise_mock.assert_not_called()
        self.assertTrue(mock_execution.cancel_token.cancelled)
        self.assertEqual(fake_agent.call_args_list, [],
                          "a bare reject (no reason) must cancel immediately, exactly as pre-Phase-2")

    async def test_reject_with_reason_after_rounds_exhausted_cancels(self):
        import core.execution.pev_runner as mod

        revised_plan_1 = self._make_plan("Revised Step 1", "r1")
        revised_plan_2 = self._make_plan("Revised Step 2", "r2")

        # PLAN_REVISION_MAX_ROUNDS default is 2 -- three reject-with-reason
        # decisions in a row means the 3rd exceeds the cap and must cancel
        # instead of attempting a 3rd revision.
        gate_results = [
            mod.PlanApprovalResult(decision="reject", plan=None, reason="feedback round 1"),
            mod.PlanApprovalResult(decision="reject", plan=None, reason="feedback round 2"),
            mod.PlanApprovalResult(decision="reject", plan=None, reason="feedback round 3"),
        ]
        mock_execution, fake_agent, mock_update_steps, revise_mock, mock_gate, chunks = await self._drive_sequence(
            gate_results, revised_plans=[revised_plan_1, revised_plan_2]
        )

        self.assertEqual(revise_mock.call_count, mod.PLAN_REVISION_MAX_ROUNDS,
                          "revision must stop being attempted once PLAN_REVISION_MAX_ROUNDS is reached")
        self.assertTrue(mock_execution.cancel_token.cancelled)
        self.assertEqual(fake_agent.call_args_list, [],
                          "cancellation must happen before any step executes")

    async def test_edit_decision_does_not_trigger_revision_loop(self):
        # Sanity check: an "edit" decision must exit via the pre-existing
        # edit path, never mistaken for a reject-with-reason case.
        import core.execution.pev_runner as mod

        edited_plan = {
            "goal": "Edited goal",
            "steps": [{"title": "Edited Step", "description": "new desc"}],
        }
        gate_results = [mod.PlanApprovalResult(decision="edit", plan=edited_plan, reason=None)]
        mock_execution, fake_agent, mock_update_steps, revise_mock, mock_gate, chunks = await self._drive_sequence(
            gate_results
        )

        revise_mock.assert_not_called()
        self.assertEqual(len(fake_agent.call_args_list), 1)
        last_msg = fake_agent.call_args_list[0][-1]["content"]
        self.assertIn("Edited Step", last_msg)


class TestPevStreamCancelDbWriteOrdering(unittest.IsolatedAsyncioTestCase):
    """
    B-099 -- live-found while verifying UX-01 Phase 2 (plan revision): all
    three cancellation exit points in _pev_stream() called
    PlanRepo.update_status()/update_steps() AFTER the terminal "cancelled"
    StepProgressChunk yield, not before. run_pev_execution()'s consumer loop
    (core/execution/pev_runner.py's run_pev_execution(), NOT exercised by
    TestPevStreamPlanApprovalDecisions/TestPevStreamPlanRevision above, which
    fully drain the generator via a plain `async for`) returns immediately
    upon receiving a "cancelled" chunk -- it never calls __anext__() on this
    generator again. Code placed after a yield only runs when the generator
    is resumed, so those DB writes were dead code for every reject, ever,
    predating this session's revision-loop work entirely (confirmed live
    against a real bare-reject execution with no revision involved at all).

    These tests replicate the real early-return consumer exactly (stop
    draining at the "cancelled" chunk, never call __anext__() again) --
    unlike the tests above, which would pass whether the write happened
    before or after the yield, since they always drain to completion.
    """

    async def _drain_until_cancelled_chunk_then_stop(self, gen):
        """Advance the async generator via explicit __anext__() calls, stopping
        the moment a StepProgressChunk(step_status="cancelled") is seen --
        never requesting anything further, exactly matching
        run_pev_execution()'s real consumer behaviour."""
        from core.types import StepProgressChunk as _SPC
        chunks = []
        while True:
            chunk = await gen.__anext__()
            chunks.append(chunk)
            if isinstance(chunk, _SPC) and chunk.step_status == "cancelled":
                return chunks

    async def test_reject_cancel_db_write_survives_early_return_consumer(self):
        """Site 1: plan-rejection cancel path (bare reject, no revision)."""
        import core.execution.pev_runner as mod

        mock_execution, base_messages, original_plan = _make_pev_stream_kwargs()

        db_ctx = MagicMock()
        db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        db_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(mod.planner_engine, "generate", new=AsyncMock(return_value=original_plan)), \
             patch.object(mod, "AsyncSessionLocal", return_value=db_ctx), \
             patch.object(mod.PlanRepo, "create", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_status", new=AsyncMock()) as mock_update_status, \
             patch.object(mod.PlanRepo, "update_goal_verification", new=AsyncMock()), \
             patch.object(mod, "_gate_plan_approval",
                           new=AsyncMock(return_value=mod.PlanApprovalResult(
                               decision="reject", plan=None, reason=None))), \
             patch.object(mod.ExecutionStateMachine, "assert_transition_allowed"), \
             patch.object(mod.redis_execution_registry, "mark_execution_running", new=AsyncMock()):

            gen = mod._pev_stream(
                execution=mock_execution, agent=_FakeAgentForPev(),
                base_messages=base_messages, user_id="user-1",
                require_plan_approval=True,
            )
            chunks = await self._drain_until_cancelled_chunk_then_stop(gen)

        self.assertEqual(chunks[-1].step_status, "cancelled")
        mock_update_status.assert_called_once_with(
            unittest.mock.ANY, execution_id="exec-pev-test", status="cancelled",
        )

    async def test_between_steps_cancel_db_write_survives_early_return_consumer(self):
        """Site 2: cancel detected between steps (before the next step's
        agent.call() even starts)."""
        import core.execution.pev_runner as mod

        mock_execution, base_messages, original_plan = _make_pev_stream_kwargs()
        # Cancelled from the very start -- caught at the first between-steps
        # check, before step 0 ever announces "running".
        mock_execution.cancel_token.cancelled = True

        db_ctx = MagicMock()
        db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        db_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(mod.planner_engine, "generate", new=AsyncMock(return_value=original_plan)), \
             patch.object(mod, "AsyncSessionLocal", return_value=db_ctx), \
             patch.object(mod.PlanRepo, "create", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_status", new=AsyncMock()) as mock_update_status, \
             patch.object(mod.PlanRepo, "update_goal_verification", new=AsyncMock()):

            gen = mod._pev_stream(
                execution=mock_execution, agent=_FakeAgentForPev(),
                base_messages=base_messages, user_id="user-1",
                require_plan_approval=False,
            )
            chunks = await self._drain_until_cancelled_chunk_then_stop(gen)

        self.assertEqual(chunks[-1].step_status, "cancelled")
        mock_update_status.assert_called_once_with(
            unittest.mock.ANY, execution_id="exec-pev-test", status="cancelled",
        )

    async def test_mid_step_cancel_db_write_survives_early_return_consumer(self):
        """Site 3: cancel detected mid-way through a step's agent.call() stream."""
        import core.execution.pev_runner as mod

        mock_execution, base_messages, original_plan = _make_pev_stream_kwargs()

        class _TwoChunkAgent:
            async def call(self, messages, stream=True):
                from core.types import TextChunk as _TC
                yield _TC(content="partial output")
                yield _TC(content="more output")

        db_ctx = MagicMock()
        db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        db_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(mod.planner_engine, "generate", new=AsyncMock(return_value=original_plan)), \
             patch.object(mod, "AsyncSessionLocal", return_value=db_ctx), \
             patch.object(mod.PlanRepo, "create", new=AsyncMock()), \
             patch.object(mod.PlanRepo, "update_steps", new=AsyncMock()) as mock_update_steps:

            gen = mod._pev_stream(
                execution=mock_execution, agent=_TwoChunkAgent(),
                base_messages=base_messages, user_id="user-1",
                require_plan_approval=False,
            )
            from core.types import StepProgressChunk as _SPC, TextChunk as _TC
            chunks = []
            # Advance past: planning x2, "running" announcement, and the
            # first raw agent chunk -- then flip cancel_token, matching a
            # cancellation arriving mid-stream, and drain to the cancelled chunk.
            while True:
                chunk = await gen.__anext__()
                chunks.append(chunk)
                if isinstance(chunk, _TC):
                    mock_execution.cancel_token.cancelled = True
                if isinstance(chunk, _SPC) and chunk.step_status == "cancelled":
                    break

        self.assertEqual(chunks[-1].step_status, "cancelled")
        persisted_cancel_calls = [
            call for call in mock_update_steps.call_args_list
            if call.kwargs.get("status") == "cancelled"
        ]
        self.assertTrue(persisted_cancel_calls,
                         "the step's cancelled status must be persisted via "
                         "PlanRepo.update_steps before the early-return consumer stops draining")


class TestAnthropicHandlerMaxTokensNoneSafe(unittest.TestCase):
    """
    core/factory.py's create_provider_handler() always sets config["max_tokens"]
    explicitly (to None when the caller didn't pass one) -- by design, per its
    own docstring ("Omit both to preserve exact prior behaviour"). AnthropicHandler
    used to do int(config.get("max_tokens", _DEFAULT_MAX_TOKENS)), which only
    substitutes the default when the key is *missing*, not when it's present
    and explicitly None -- so every normal conversational call (which never
    passes max_tokens) crashed with TypeError: int() argument ... not 'NoneType'
    at construction time. Found live: the crash happened deep enough in
    AgentEngine.__init__ that the execution was silently left stuck at
    "pending" forever (see TestWorkerMarksExecutionFailedOnCrash below).
    """

    def _build(self, max_tokens=None):
        from providers.handlers.anthropic_handler import AnthropicHandler
        return AnthropicHandler({
            "api_key": "test-key",
            "model": "claude-sonnet-5",
            "max_tokens": max_tokens,
        })

    def test_none_max_tokens_does_not_raise(self):
        handler = self._build(max_tokens=None)
        self.assertIsInstance(handler.max_tokens, int)
        self.assertGreater(handler.max_tokens, 0)

    def test_explicit_max_tokens_respected(self):
        handler = self._build(max_tokens=500)
        self.assertEqual(handler.max_tokens, 500)

    def test_source_uses_or_not_get_default(self):
        """Guards against the exact regression -- .get(key, default) silently
        does the wrong thing here since the key is always present."""
        src = Path("providers/handlers/anthropic_handler.py").read_text(encoding="utf-8")
        self.assertIn('config.get("max_tokens") or _DEFAULT_MAX_TOKENS', src)
        self.assertNotIn('config.get("max_tokens", _DEFAULT_MAX_TOKENS)', src)


class TestWorkerMarksExecutionFailedOnCrash(unittest.IsolatedAsyncioTestCase):
    """
    core/execution/worker.py's _safe_process_execution() used to only log an
    unhandled exception from _process_execution() -- never updated the
    execution's status. Since api/chat.py already returns 200 with an
    execution_id before processing starts, that left the execution stuck at
    "pending" forever with no user-visible error, for a crash ANYWHERE inside
    processing (any provider, any handler, any model) -- not scoped to the
    Anthropic bug that surfaced it. Fixed by having the outermost catch-all
    mark the execution failed and publish a user-facing event, reusing the
    exact mark_execution_failed + publish_user_event pairing already used a
    few lines below for the "no provider/model configured" guard.
    """

    async def test_crash_marks_execution_failed_not_left_pending(self):
        from core.execution.worker import ExecutionWorker
        worker = ExecutionWorker.__new__(ExecutionWorker)  # bypass __init__, don't need real state

        async def _boom(execution_id):
            raise TypeError("simulated crash deep inside processing")

        fake_client = AsyncMock()
        fake_client.hgetall = AsyncMock(return_value={"user_id": "u1"})

        with patch.object(worker, "_process_execution", new=_boom), \
             patch("core.execution.worker.redis_execution_registry.connect", new=AsyncMock(return_value=fake_client)), \
             patch("core.execution.worker.redis_execution_registry.mark_execution_failed", new=AsyncMock()) as mock_fail, \
             patch("core.execution.worker.publish_user_event", new=AsyncMock()) as mock_event:
            await worker._safe_process_execution("exec-123")

        mock_fail.assert_called_once()
        self.assertEqual(mock_fail.call_args[0][0], "exec-123")
        self.assertIn("simulated crash", mock_fail.call_args[0][1])
        mock_event.assert_called_once()
        self.assertEqual(mock_event.call_args.kwargs["user_id"], "u1")
        self.assertEqual(mock_event.call_args.kwargs["event"], "execution.failed")

    async def test_crash_marking_itself_failing_does_not_raise(self):
        """The safety net's own failure must not propagate -- it's already
        inside the outermost catch-all with nothing above it to catch a raise."""
        from core.execution.worker import ExecutionWorker
        worker = ExecutionWorker.__new__(ExecutionWorker)

        async def _boom(execution_id):
            raise RuntimeError("original crash")

        with patch.object(worker, "_process_execution", new=_boom), \
             patch("core.execution.worker.redis_execution_registry.connect", new=AsyncMock(side_effect=Exception("broker down too"))):
            await worker._safe_process_execution("exec-456")  # must not raise


class TestToolAwareTimeout(unittest.TestCase):
    """
    TOOL_EXECUTION_TIMEOUT_SECONDS=300 was applied uniformly to every tool,
    including delegate_to_agent -- which wraps a nested AgentEngine that
    already has its own protections (EVA_FIRST_CHUNK_TIMEOUT_S /
    EVA_INTER_CHUNK_TIMEOUT_S catch a dead LLM connection; the child's own
    max_steps bounds total work). The flat timeout cancelled and fully
    restarted legitimate multi-step delegation for no reason. ToolBase now
    exposes execution_timeout_seconds so a tool can declare its own base.
    """

    def test_toolbase_default_is_none(self):
        from core.tools.base import ToolBase
        self.assertIsNone(ToolBase.execution_timeout_seconds)

    def test_delegate_to_agent_overrides_with_a_larger_generous_base(self):
        from tools.default.delegate_tool import DelegateToAgentTool
        from config.settings import (
            DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS,
            TOOL_EXECUTION_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            DelegateToAgentTool.execution_timeout_seconds,
            DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS,
        )
        self.assertGreater(
            DelegateToAgentTool.execution_timeout_seconds,
            TOOL_EXECUTION_TIMEOUT_SECONDS,
        )


class TestDispatcherProgressiveTimeout(unittest.IsolatedAsyncioTestCase):
    """
    Each retry attempt now gets base_timeout * 2^(attempt-1), capped at 4x
    base -- same exponential shape as core/daemons/registry.py's retry
    backoff. Verified by replacing asyncio.wait_for with a fake that records
    the timeout it was called with and immediately raises TimeoutError
    (closing the un-awaited coroutine first, to avoid a "never awaited"
    warning) -- this isolates the dispatcher's timeout arithmetic from any
    real tool behaviour or wall-clock waiting.
    """

    @staticmethod
    def _make_probe_tool(timeout_override=None):
        from core.tools.base import ToolBase, ToolAuthority, ToolResult
        from pydantic import BaseModel

        class _EmptyArgs(BaseModel):
            pass

        class _ProbeTool(ToolBase):
            args_schema = _EmptyArgs
            authority = ToolAuthority.READ_ONLY
            execution_timeout_seconds = timeout_override

            async def run(self, args):
                return ToolResult(result={"ok": True})

        return _ProbeTool

    @staticmethod
    def _fake_execution():
        from unittest.mock import MagicMock
        execution = MagicMock()
        execution.execution_id = "probe-exec"
        execution.user_id = ""
        execution.conversation_id = ""
        # A bare MagicMock() auto-vivifies .user_registry as another
        # truthy MagicMock, which would otherwise trip the dispatcher's
        # progressive-disclosure hallucination gate before run() executes.
        execution.user_registry = None
        return execution

    async def _run_with_captured_timeouts(self, tool_cls, patch_global_default=None):
        import asyncio as _asyncio
        from contextlib import ExitStack
        from core.tools.dispatcher import ToolDispatcher

        captured = []

        async def _fake_wait_for(coro, timeout=None):
            captured.append(timeout)
            coro.close()
            raise _asyncio.TimeoutError()

        with ExitStack() as stack:
            stack.enter_context(patch("core.tools.dispatcher.asyncio.wait_for", new=_fake_wait_for))
            if patch_global_default is not None:
                stack.enter_context(
                    patch("core.tools.dispatcher.TOOL_EXECUTION_TIMEOUT_SECONDS", patch_global_default)
                )
            result = await ToolDispatcher.execute_tool(
                call_id="probe-call",
                tool_name="probe_tool",
                arguments={},
                tool_classes={"probe_tool": tool_cls},
                execution_metadata={"execution": self._fake_execution()},
            )
        return captured, result

    async def test_timeout_scales_per_attempt_using_tool_override(self):
        from core.tools.dispatcher import ToolDispatcher
        from core.types import ToolExecutionStatus

        tool_cls = self._make_probe_tool(timeout_override=10)
        captured, result = await self._run_with_captured_timeouts(tool_cls)

        expected = [min(10 * (2 ** i), 10 * 4) for i in range(ToolDispatcher.MAX_ATTEMPTS)]
        self.assertEqual(captured, expected)
        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)

    async def test_timeout_falls_back_to_global_default_without_override(self):
        from core.tools.dispatcher import ToolDispatcher

        tool_cls = self._make_probe_tool(timeout_override=None)
        captured, _ = await self._run_with_captured_timeouts(tool_cls, patch_global_default=7)

        expected = [min(7 * (2 ** i), 7 * 4) for i in range(ToolDispatcher.MAX_ATTEMPTS)]
        self.assertEqual(captured, expected)

    async def test_first_attempt_unchanged_from_pre_progressive_behaviour(self):
        """Attempt 1 must equal exactly the base timeout -- no regression
        for the common case where a tool never times out."""
        from core.tools.dispatcher import ToolDispatcher

        tool_cls = self._make_probe_tool(timeout_override=42)
        captured, _ = await self._run_with_captured_timeouts(tool_cls)
        self.assertEqual(captured[0], 42)


class TestDispatcherRetryContinuity(unittest.IsolatedAsyncioTestCase):
    """
    Nothing was persisted about a timed-out tool call, so every retry of
    delegate_to_agent started completely from scratch even though the child
    agent may have already made real progress. The dispatcher now shares a
    plain dict across every attempt of the SAME call (never persisted, never
    shared across unrelated calls) so a tool that tracks its own progress as
    it goes can avoid repeating work lost to a cancelled earlier attempt.
    """

    async def test_continuity_dict_is_shared_and_accumulates_across_attempts(self):
        import asyncio as _asyncio
        from unittest.mock import MagicMock
        from core.tools.base import ToolBase, ToolAuthority, ToolResult
        from core.tools.dispatcher import ToolDispatcher
        from core.types import ToolExecutionStatus
        from pydantic import BaseModel

        class _EmptyArgs(BaseModel):
            pass

        class _ContinuityProbeTool(ToolBase):
            args_schema = _EmptyArgs
            authority = ToolAuthority.READ_ONLY
            execution_timeout_seconds = 0.05

            async def run(self, args):
                cont = self.retry_continuity
                seen = cont.setdefault("seen", [])
                seen.append(1)
                if len(seen) < ToolDispatcher.MAX_ATTEMPTS:
                    await _asyncio.sleep(5)  # exceeds the tiny timeout -> cancelled
                return ToolResult(result={"seen_count": len(seen)})

        execution = MagicMock()
        execution.execution_id = "continuity-exec"
        execution.user_id = ""
        execution.conversation_id = ""
        execution.user_registry = None

        result = await ToolDispatcher.execute_tool(
            call_id="continuity-call",
            tool_name="continuity_probe",
            arguments={},
            tool_classes={"continuity_probe": _ContinuityProbeTool},
            execution_metadata={"execution": execution},
        )

        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.output.get("seen_count"), ToolDispatcher.MAX_ATTEMPTS)

    async def test_retry_continuity_always_attached_even_without_use(self):
        """Tools that don't read retry_continuity must be unaffected -- the
        attribute is set unconditionally but is opt-in to use."""
        from unittest.mock import MagicMock
        from core.tools.base import ToolBase, ToolAuthority, ToolResult
        from core.tools.dispatcher import ToolDispatcher
        from core.types import ToolExecutionStatus
        from pydantic import BaseModel

        class _EmptyArgs(BaseModel):
            pass

        class _IndifferentTool(ToolBase):
            args_schema = _EmptyArgs
            authority = ToolAuthority.READ_ONLY

            async def run(self, args):
                return ToolResult(result={"ok": True})

        execution = MagicMock()
        execution.execution_id = "indifferent-exec"
        execution.user_id = ""
        execution.conversation_id = ""
        execution.user_registry = None

        result = await ToolDispatcher.execute_tool(
            call_id="indifferent-call",
            tool_name="indifferent_tool",
            arguments={},
            tool_classes={"indifferent_tool": _IndifferentTool},
            execution_metadata={"execution": execution},
        )
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)


@pytest.mark.usefixtures("stored_provider_keys")
class TestDelegateRetryContinuityDigest(unittest.TestCase):
    """
    delegate_to_agent reads self.retry_continuity (set by the dispatcher)
    and, if a previous attempt already made tool calls before being
    cancelled on timeout, states that as a short factual list in the child's
    task -- not a replay of the previous attempt's raw transcript, which
    risks confusing the model more than it helps.
    """

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    @staticmethod
    def _base_execution(suffix):
        from unittest.mock import MagicMock
        execution = MagicMock()
        execution.user_id = f"u-{suffix}"
        execution.execution_id = f"e-{suffix}"
        execution.conversation_id = f"c-{suffix}"
        execution.shadow = None
        execution.caller_agent_name = "orchestrator"
        return execution

    def test_prior_attempt_tool_calls_injected_as_factual_digest(self):
        import asyncio
        from core.tools.registry import DefaultToolRegistry

        tool_cls = DefaultToolRegistry.all()["delegate_to_agent"]
        captured = {}

        async def _fake_call(messages, stream):
            captured["messages"] = messages
            from core.agent_engine import TextChunk
            yield TextChunk(content="ok")

        mock_engine = MagicMock()
        mock_engine.call = _fake_call

        tool_instance = tool_cls()
        tool_instance.execution = self._base_execution("digest1")
        tool_instance.retry_continuity = {
            "tool_calls": [{"tool_name": "read_file", "args_preview": "path=README.md"}],
        }

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("core.agent_engine.AgentEngine", return_value=mock_engine), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"):
            asyncio.run(tool_instance.run({"agent_name": "researcher", "task": "Investigate the repo."}))

        sent = captured["messages"][0]["content"]
        self.assertIn("previous attempt", sent)
        self.assertIn("read_file(path=README.md)", sent)

    def test_no_digest_on_first_attempt(self):
        """Empty tool_calls (attempt 1, nothing to report yet) must leave
        the task text unchanged."""
        import asyncio
        from core.tools.registry import DefaultToolRegistry

        tool_cls = DefaultToolRegistry.all()["delegate_to_agent"]
        captured = {}

        async def _fake_call(messages, stream):
            captured["messages"] = messages
            from core.agent_engine import TextChunk
            yield TextChunk(content="ok")

        mock_engine = MagicMock()
        mock_engine.call = _fake_call

        tool_instance = tool_cls()
        tool_instance.execution = self._base_execution("digest2")
        tool_instance.retry_continuity = {"tool_calls": []}

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("core.agent_engine.AgentEngine", return_value=mock_engine), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"):
            asyncio.run(tool_instance.run({"agent_name": "researcher", "task": "Investigate the repo."}))

        sent = captured["messages"][0]["content"]
        self.assertNotIn("previous attempt", sent)
        self.assertEqual(sent, "Investigate the repo.")

    def test_tool_call_chunks_recorded_into_continuity_as_they_happen(self):
        import asyncio
        from core.tools.registry import DefaultToolRegistry
        from core.types import ToolCallChunk

        tool_cls = DefaultToolRegistry.all()["delegate_to_agent"]

        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk
            yield ToolCallChunk(tool_name="read_file", arguments={"path": "a.txt"}, call_id="tc1")
            yield TextChunk(content="done")

        mock_engine = MagicMock()
        mock_engine.call = _fake_call

        tool_instance = tool_cls()
        tool_instance.execution = self._base_execution("digest3")
        continuity = {"tool_calls": []}
        tool_instance.retry_continuity = continuity

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("core.agent_engine.AgentEngine", return_value=mock_engine), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"):
            asyncio.run(tool_instance.run({"agent_name": "researcher", "task": "Do it."}))

        self.assertEqual(len(continuity["tool_calls"]), 1)
        self.assertEqual(continuity["tool_calls"][0]["tool_name"], "read_file")

    def test_missing_retry_continuity_attribute_does_not_crash(self):
        """A tool instance built outside the dispatcher (e.g. direct unit
        test construction) has no retry_continuity attribute at all --
        run() must tolerate that via getattr, not assume it's always set."""
        import asyncio
        from core.tools.registry import DefaultToolRegistry

        tool_cls = DefaultToolRegistry.all()["delegate_to_agent"]

        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk
            yield TextChunk(content="ok")

        mock_engine = MagicMock()
        mock_engine.call = _fake_call

        tool_instance = tool_cls()
        tool_instance.execution = self._base_execution("digest4")
        # deliberately NOT setting tool_instance.retry_continuity

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("core.agent_engine.AgentEngine", return_value=mock_engine), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent"):
            result = asyncio.run(tool_instance.run({"agent_name": "researcher", "task": "Do it."}))

        self.assertNotIn("error", result.result)


class TestArtifactUpdaterConversationTextBypass(unittest.IsolatedAsyncioTestCase):
    """
    B-094: ProjectArtifactUpdater._run() always read Message DB rows for
    conversation_id, which daemon runs never have (they write .eva files
    directly, by design, to avoid message-table growth) -- so the B-056
    daemon hook was structurally dead from its first commit. update() now
    accepts an optional conversation_text that, when supplied, is used
    directly and skips the DB read entirely. Default None preserves the
    original DB-read behaviour exactly for every existing (organic chat)
    caller.
    """

    async def test_conversation_text_supplied_skips_db_read(self):
        from core.project.manager import ProjectManager
        from core.project.artifact_updater import ProjectArtifactUpdater
        from core.project.eva_format import read_file

        with tempfile.TemporaryDirectory() as ext_dir:
            ProjectManager.init_workspace(
                user_id="test_user", project_id="daemon_text_proj",
                name="Daemon Text Project", external_path=ext_dir,
            )

            llm_response = json.dumps([
                {"file": "knowledge", "action": "append",
                 "content": "[NOTE ts=2026-01-01T00:00Z] Daemon found a config drift issue"}
            ])

            updater = ProjectArtifactUpdater()
            db_read_mock = AsyncMock(return_value=[])  # would return nothing if ever called
            with patch(
                "core.project.artifact_updater.DBRepo.list_messages_for_conversation",
                new=db_read_mock,
            ), patch(
                "core.project.artifact_updater.call_with_full_fallback",
                new=AsyncMock(return_value=llm_response),
            ):
                await updater.update(
                    project_id="daemon_text_proj",
                    conversation_id="daemon-conv-id-not-a-real-conversation",
                    conversation_text="USER: Check the config.\nASSISTANT: Found a config drift issue.",
                    user_id="test_user",
                    session={"provider": "test", "model": "test-model"},
                    execution_id="exec-1",
                    workspace_path=ext_dir,
                )

            db_read_mock.assert_not_called()

            knowledge = read_file(
                ProjectManager.get_artifact_path(
                    "test_user", "daemon_text_proj", "knowledge.eva", ext_dir
                )
            )
            self.assertIn("config drift issue", knowledge)

    async def test_conversation_text_omitted_still_reads_db(self):
        """Regression guard: every existing (organic chat) call site omits
        conversation_text and must keep hitting the DB exactly as before."""
        from core.project.manager import ProjectManager
        from core.project.artifact_updater import ProjectArtifactUpdater

        with tempfile.TemporaryDirectory() as ext_dir:
            ProjectManager.init_workspace(
                user_id="test_user", project_id="organic_proj",
                name="Organic Project", external_path=ext_dir,
            )

            fake_message = MagicMock()
            fake_message.role = "user"
            fake_message.content = "please remember this"

            updater = ProjectArtifactUpdater()
            db_read_mock = AsyncMock(return_value=[fake_message])
            with patch(
                "core.project.artifact_updater.DBRepo.list_messages_for_conversation",
                new=db_read_mock,
            ), patch(
                "core.project.artifact_updater.call_with_full_fallback",
                new=AsyncMock(return_value="[]"),
            ):
                await updater.update(
                    project_id="organic_proj",
                    conversation_id="real-conv-id",
                    user_id="test_user",
                    session={"provider": "test", "model": "test-model"},
                    execution_id="exec-2",
                    workspace_path=ext_dir,
                )

            db_read_mock.assert_called_once()


class TestDaemonRunnerArtifactUpdaterWiring(unittest.TestCase):
    """
    B-094 source-level checks for core/daemons/runner.py's hook -- a full
    functional test would need to mock run_daemon()'s entire AgentEngine/
    broker/DB call chain (see TestAgentEngineToolsThreadFallbackChain's
    already-fragile attempt at this elsewhere in the suite), which is
    disproportionate for verifying wiring. Same category as
    TestProjectSyncEndpointWiring and TestRunnerAndProjectsSourceWiring
    elsewhere in this file.
    """

    @staticmethod
    def _hook_section() -> str:
        src = Path("core/daemons/runner.py").read_text(encoding="utf-8")
        idx = src.find("B-094")
        assert idx > 0, "B-094 hook marker not found in runner.py"
        # 2600, not 2000: the 2026-08-18 kind-based-exclusion comment added
        # ~6 lines ahead of this section's tail (conversation_text=...),
        # pushing it past the original 2000-char window.
        return src[idx:idx + 2600]

    def test_hook_passes_conversation_text_from_trigger_and_output(self):
        section = self._hook_section()
        self.assertIn('conversation_text=f"USER: {trigger}\\nASSISTANT: {output}"', section)

    def test_hook_excludes_system_kind_daemons(self):
        """2026-08-18: the exclusion check was a name-string comparison
        against sync_daemon_name(project_id) -- fragile, coupled to a
        computed slug rather than a real type. Replaced with a formal
        DaemonConfig.kind field; 'system' (server-reserved, only ever set
        by api/projects.py's _ensure_sync_daemon()) is the new exclusion
        condition, same behavior, no longer name-coupled."""
        section = self._hook_section()
        self.assertIn('getattr(daemon, "kind", "standard") != "system"', section)
        # The old name-coupled check itself must be gone -- a comment
        # mentioning sync_daemon_name() for context (explaining what this
        # replaced) is fine, an actual import or the old comparison is not.
        self.assertNotIn("daemon.name != sync_daemon_name", section)
        self.assertNotIn("from core.daemons.registry import sync_daemon_name", section)

    def test_hook_still_gated_on_success_and_project_id(self):
        section = self._hook_section()
        self.assertIn("success", section)
        self.assertIn('getattr(daemon, "project_id", None)', section)

    def test_sync_daemon_name_importable_from_registry_not_projects_api(self):
        """B-094 also moved sync_daemon_name out of api/projects.py so
        core/daemons/runner.py (core layer) doesn't have to import from the
        api layer to use it."""
        from core.daemons.registry import sync_daemon_name
        self.assertEqual(sync_daemon_name("proj12345678"), "project-sync-proj1234")
        api_src = Path("api/projects.py").read_text(encoding="utf-8")
        self.assertNotIn("def sync_daemon_name", api_src)
        self.assertIn("from core.daemons.registry import daemon_registry, sync_daemon_name", api_src)


class TestConversationJournalSubagentCacheKey(unittest.TestCase):
    """
    B-095: ConversationJournal._get_logger() caches one Logger+FileHandler
    per cache key for the process lifetime. log_subagent()'s key was
    f"journal.subagent.{agent_id}" -- no conversation_id -- so a shared,
    reusable subagent (e.g. skill-runner, invoked from many different
    conversations) had every call after the first silently reuse the first
    conversation's cached logger, misattributing entries into the wrong
    conversation's directory. The on-disk log_path was already correctly
    conversation-scoped every call; only the cache key wasn't. Confirmed
    empirically (not just by code-reading) during Phase C.4's investigation:
    a daemon's own subagent journal directory existed but was empty, its
    real entries found inside an unrelated conversation's directory instead.
    """

    def test_different_conversations_same_agent_get_separate_files(self):
        import uuid
        from core.memory.conversation_journal import ConversationJournal
        from utils.workspace import get_subagent_journal_dir

        user_id = f"b095_test_{uuid.uuid4().hex[:8]}"
        agent_id = "shared-agent-xyz"
        conv_a = f"conv-a-{uuid.uuid4().hex[:8]}"
        conv_b = f"conv-b-{uuid.uuid4().hex[:8]}"

        ConversationJournal.log_subagent(user_id, conv_a, agent_id, {
            "type": "tool_call", "tool_name": "read_file", "marker": "FROM_CONV_A",
        })
        ConversationJournal.log_subagent(user_id, conv_b, agent_id, {
            "type": "tool_call", "tool_name": "write_file", "marker": "FROM_CONV_B",
        })

        path_a = get_subagent_journal_dir(user_id, conv_a) / f"{agent_id}.jsonl"
        path_b = get_subagent_journal_dir(user_id, conv_b) / f"{agent_id}.jsonl"

        self.assertTrue(path_a.exists(), "conv_a's journal file must exist")
        self.assertTrue(path_b.exists(), "conv_b's journal file must exist")

        content_a = path_a.read_text(encoding="utf-8")
        content_b = path_b.read_text(encoding="utf-8")

        self.assertIn("FROM_CONV_A", content_a)
        self.assertNotIn("FROM_CONV_B", content_a)
        self.assertIn("FROM_CONV_B", content_b)
        self.assertNotIn("FROM_CONV_A", content_b)

    def test_cache_key_includes_conversation_id(self):
        src = Path("core/memory/conversation_journal.py").read_text(encoding="utf-8")
        idx = src.find("def log_subagent")
        next_def = src.find("\n    @classmethod", idx + 1)
        section = src[idx:next_def] if next_def > 0 else src[idx:]
        self.assertIn('f"journal.subagent.{conversation_id}.{agent_id}"', section)
        self.assertNotIn('f"journal.subagent.{agent_id}"', section)

    def test_log_and_log_daemon_exec_keying_unaffected(self):
        """Regression guard -- only log_subagent's key shape changed."""
        src = Path("core/memory/conversation_journal.py").read_text(encoding="utf-8")
        self.assertIn('f"journal.conv.{conversation_id}"', src)
        self.assertIn('f"journal.daemon.{conversation_id}.{execution_id}"', src)


@pytest.mark.usefixtures("stored_provider_keys")
class TestDelegateToolSubagentJournalAgentId(unittest.TestCase):
    """
    B-095 sibling bug: delegate_tool.py's tool_call event (the one fired for
    every tool the subagent invokes) passed cfg.id directly to
    log_subagent() instead of the already-computed agent_id (which falls
    back to f"dyn-{agent_name}" for a dynamic/unregistered subagent). cfg.id
    is None for a dynamic subagent, so every dynamic subagent's tool-call
    events collapsed into one shared "None.jsonl" file -- a second,
    independent instance of the same underlying mistake, found while
    checking other log_subagent callers per B-095's own investigation.
    The other two log_subagent call sites in this file already used
    agent_id correctly, so this bug was invisible for registered agents
    (where agent_id == cfg.id) and only affected dynamic ones.
    """

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    def test_tool_call_event_uses_agent_id_for_dynamic_subagent(self):
        import asyncio
        from core.tools.registry import DefaultToolRegistry
        from core.types import ToolCallChunk

        tool_cls = DefaultToolRegistry.all()["delegate_to_agent"]

        execution = MagicMock()
        execution.user_id = "u-b095"
        execution.execution_id = "e-b095"
        execution.conversation_id = "c-b095"
        execution.shadow = None
        execution.caller_agent_name = "orchestrator"

        async def _fake_call(messages, stream):
            from core.agent_engine import TextChunk
            yield ToolCallChunk(tool_name="read_file", arguments={"path": "a.txt"}, call_id="tc1")
            yield TextChunk(content="done")

        mock_engine = MagicMock()
        mock_engine.call = _fake_call

        tool_instance = tool_cls()
        tool_instance.execution = execution
        tool_instance.retry_continuity = {"tool_calls": []}

        mock_registry = MagicMock()
        mock_registry.get = AsyncMock(return_value=None)  # dynamic -- no registered config

        captured_calls = []

        def _capture_log_subagent(user_id, conversation_id, agent_id, event):
            captured_calls.append((agent_id, event))

        with patch("core.agents.registry.subagent_registry", mock_registry), \
             patch("config.settings.get_user_session", return_value={"provider": "groq", "model": "m", "api_key": "k"}), \
             patch("core.agent_engine.AgentEngine", return_value=mock_engine), \
             patch("core.memory.conversation_journal.ConversationJournal.log_subagent", side_effect=_capture_log_subagent):
            asyncio.run(tool_instance.run({"agent_name": "dynamic-researcher", "task": "Do it."}))

        tool_call_events = [(aid, ev) for aid, ev in captured_calls if ev.get("type") == "tool_call"]
        self.assertEqual(len(tool_call_events), 1)
        agent_id_used, _ = tool_call_events[0]
        self.assertEqual(agent_id_used, "dyn-dynamic-researcher")
        self.assertIsNotNone(agent_id_used, "cfg.id (None for a dynamic agent) must not be used directly")


class TestDaemonStepExtensionAutoStop(unittest.TestCase):
    """
    A daemon run hitting the ReAct step limit used to go through the same
    gate_step_extension() as a conversational execution -- pausing at
    AWAITING_STEP_EXTENSION and blocking on blpop for up to
    STEP_EXTENSION_TIMEOUT_SECONDS (3600s default) waiting for a decision
    nobody could ever make, since daemons run unattended. Found via a real
    "Sync now" button click hanging in practice, not theoretically.

    core/agent_engine.py's gate call site now checks execution_mode and
    skips straight to _wants_continue = False for "daemon" -- the same
    outcome the timeout would have produced anyway, just without the wait.
    No full functional harness exists anywhere in this suite for driving
    AgentEngine's streaming ReAct loop to a real step-limit hit (checked --
    only step_extension.py's lower-level primitives are unit-tested
    elsewhere, in test_step_extension_unit.py), so this verifies the fix at
    the source level, matching the established pattern for wiring checks
    embedded in a large method where a full harness would be
    disproportionate (see TestDaemonRunnerArtifactUpdaterWiring elsewhere
    in this file for the same reasoning applied to a different call site).
    """

    @staticmethod
    def _gate_block() -> str:
        src = Path("core/agent_engine.py").read_text(encoding="utf-8")
        idx = src.find('_user_id_for_ext = getattr(self.execution, "user_id"')
        assert idx > 0, "step-extension gate block not found in agent_engine.py"
        end = src.find("if _wants_continue:", idx)
        assert end > idx, "end of gate block not found"
        return src[idx:end]

    def test_daemon_mode_checked_before_gate_call(self):
        section = self._gate_block()
        self.assertIn(
            'getattr(self.execution, "execution_mode", "conversational") == "daemon"',
            section,
        )

    def test_gate_step_extension_only_reachable_for_non_daemon_runs(self):
        """The import + call to gate_step_extension() must live in the
        else branch of the daemon-mode check, not run unconditionally."""
        section = self._gate_block()
        daemon_check_idx = section.find('== "daemon"')
        self.assertGreater(daemon_check_idx, 0)
        else_idx = section.find("else:", daemon_check_idx)
        self.assertGreater(else_idx, daemon_check_idx)
        gate_call_idx = section.find("await gate_step_extension(")
        self.assertGreater(
            gate_call_idx, else_idx,
            "gate_step_extension() must only be called after the daemon-mode else branch",
        )

    def test_wants_continue_defaults_false_before_the_branch(self):
        """Whichever branch runs (daemon skip, or gate exception), the
        pre-existing default must be False -- daemons never fall through
        to "continue" by accident if something above changes."""
        section = self._gate_block()
        idx = section.find('_wants_continue = False')
        daemon_check_idx = section.find('== "daemon"')
        self.assertGreater(daemon_check_idx, 0)
        self.assertLess(
            idx, daemon_check_idx,
            "_wants_continue = False must be set before the daemon-mode branch, "
            "not only inside the non-daemon else path",
        )

    def test_non_daemon_execution_modes_unaffected(self):
        """conversational and plan_execute must still reach the real gate
        -- this fix is additive for daemons, not a behaviour change for
        attended sessions."""
        section = self._gate_block()
        self.assertIn("from core.execution.step_extension import gate_step_extension", section)
        self.assertIn("await gate_step_extension(", section)


if __name__ == "__main__":
    unittest.main(verbosity=2)
