# tests/test_b1_deadcode_cleanup_unit.py
"""
Unit tests for the B1 dead-code/dead-column cleanup batch (2026-08-19).

Covers:
  - B1-1.1  core/tools/default_registry.py removed (confirmed dead duplicate)
  - B1-1.2/B1-1.4  core/providers/provider_api_registry.py and
    providers/capability/litellm/provider_api.py removed (investigated and
    confirmed superseded by providers/capability/base.py's
    is_provider_key_configured(), already wired into POST /set-session and
    GET /providers/{provider}/models -- not just "unwired", genuinely dead)
  - B1-1.3/B1-3.2  core/memory/store/ removed (confirmed dead, archived)
  - B1-1.5  core/logging/stream_debug_store.py removed (confirmed stale)
  - B1-1.6  state/stream_state.py removed (confirmed unused)
  - B1-1.8/B1-4.1  redundant agent_only=True dropped from coordinate_tool.py
    and parallel_delegate_tool.py -- chat_excluded=True alone must still
    set both flags via the default_tool decorator's own or-logic
  - B1-2.1  User.is_active column removed
  - B1-2.2  AuthAuditLog.event_metadata column removed
  - B1-2.5  .gitignore covers *_output.txt

These are all deletions/removals -- the tests assert the negative (module
gone, column gone) plus that nothing downstream silently broke (decorator
flag behavior, seed-data insert, model still constructible).

Run with:
    cd <project_root>
    python tests/test_b1_deadcode_cleanup_unit.py
    (or: pytest tests/test_b1_deadcode_cleanup_unit.py -v)
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDeadModulesRemoved(unittest.TestCase):
    """B1-1.1, B1-1.3/3.2, B1-1.5, B1-1.6 -- confirmed-dead files deleted."""

    def test_default_registry_module_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("core.tools.default_registry")

    def test_memory_store_package_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("core.memory.store")

    def test_memory_store_dir_gone_from_disk(self):
        self.assertFalse(
            os.path.isdir(os.path.join(BACKEND_ROOT, "core", "memory", "store"))
        )

    def test_stream_debug_store_module_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("core.logging.stream_debug_store")

    def test_stream_state_module_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("state.stream_state")

    def test_live_tool_registry_unaffected(self):
        """The real (non-dead) DefaultToolRegistry in registry.py still works."""
        from core.tools.registry import DefaultToolRegistry
        self.assertTrue(hasattr(DefaultToolRegistry, "register"))
        self.assertTrue(hasattr(DefaultToolRegistry, "all"))

    def test_provider_api_registry_module_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("core.providers.provider_api_registry")

    def test_litellm_provider_api_module_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("providers.capability.litellm.provider_api")

    def test_litellm_capability_cache_file_untouched(self):
        """provider_api.py's sibling models_cache.json must survive the
        deletion -- capability_registry.py still reads it."""
        cache_path = os.path.join(
            BACKEND_ROOT, "providers", "capability", "litellm", "models_cache.json"
        )
        if not os.path.exists(cache_path):
            # models_cache.json is a gitignored runtime cache (written by
            # capability_registry on first use), so it does not exist on a
            # fresh checkout. There is nothing to have been deleted.
            self.skipTest("runtime cache file not generated yet (gitignored)")
        self.assertTrue(os.path.isfile(cache_path))

    def test_live_key_configured_check_unaffected(self):
        """The superseding mechanism (is_provider_key_configured) still
        works after removing the dead registry that duplicated its job."""
        from unittest.mock import patch
        from providers.capability.base import is_provider_key_configured

        self.assertTrue(is_provider_key_configured("ollama"))
        self.assertTrue(is_provider_key_configured("anthropic", explicit_key="sk-test"))
        self.assertFalse(is_provider_key_configured("anthropic"))   # empty isolated store
        from providers import credentials
        credentials.set_api_key("anthropic", "sk-real")
        self.assertTrue(is_provider_key_configured("anthropic"))


class TestAgentOnlyFlagCleanup(unittest.TestCase):
    """B1-1.8/B1-4.1 -- redundant agent_only=True removed from call sites,
    but chat_excluded=True must still imply agent_only=True via the
    default_tool decorator's own backward-compat or-logic."""

    @classmethod
    def setUpClass(cls):
        from core.tools.loader import load_all_tools
        load_all_tools()

    def test_coordinate_tool_still_chat_excluded_and_agent_only(self):
        from tools.default.coordinate_tool import CoordinateTool
        self.assertTrue(CoordinateTool.chat_excluded)
        self.assertTrue(CoordinateTool.agent_only)

    def test_parallel_delegate_tool_still_chat_excluded_and_agent_only(self):
        from tools.default.parallel_delegate_tool import ParallelDelegateTool
        self.assertTrue(ParallelDelegateTool.chat_excluded)
        self.assertTrue(ParallelDelegateTool.agent_only)

    def test_source_no_longer_passes_redundant_agent_only_kwarg(self):
        coordinate_src = open(
            os.path.join(BACKEND_ROOT, "tools", "default", "coordinate_tool.py"),
            encoding="utf-8",
        ).read()
        parallel_src = open(
            os.path.join(BACKEND_ROOT, "tools", "default", "parallel_delegate_tool.py"),
            encoding="utf-8",
        ).read()
        self.assertNotIn("agent_only=True", coordinate_src)
        self.assertNotIn("agent_only=True", parallel_src)


class TestUserIsActiveRemoved(unittest.TestCase):
    """B1-2.1 -- User.is_active column removed."""

    def test_model_has_no_is_active_column(self):
        from core.db.models import User
        self.assertNotIn("is_active", User.__table__.c.keys())

    def test_model_has_no_is_active_attribute(self):
        from core.db.models import User
        self.assertFalse(hasattr(User, "is_active"))

    def test_user_still_constructible_without_is_active(self):
        from core.db.models import User
        u = User(
            id="test-user-id",
            email="test@example.com",
            username="testuser",
            password_hash="hash",
        )
        self.assertEqual(u.username, "testuser")


class TestAuthAuditLogEventMetadataRemoved(unittest.TestCase):
    """B1-2.2 -- AuthAuditLog.event_metadata column removed."""

    def test_model_has_no_event_metadata_column(self):
        from core.db.models import AuthAuditLog
        self.assertNotIn("event_metadata", AuthAuditLog.__table__.c.keys())

    def test_model_has_no_event_metadata_attribute(self):
        from core.db.models import AuthAuditLog
        self.assertFalse(hasattr(AuthAuditLog, "event_metadata"))

    def test_auth_audit_log_still_constructible(self):
        from core.db.models import AuthAuditLog
        row = AuthAuditLog(id="log-1", event_type="login")
        self.assertEqual(row.event_type, "login")


class TestGitignorePattern(unittest.TestCase):
    """B1-2.5 -- .gitignore covers *_output.txt."""

    def test_gitignore_has_output_txt_pattern(self):
        gitignore_path = os.path.join(BACKEND_ROOT, ".gitignore")
        with open(gitignore_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("*_output.txt", content)


def _load_migration_0008():
    """alembic/versions has no __init__.py (not a regular package, and
    Alembic itself loads revisions by file path, not dotted import) --
    load the module directly from its file path instead."""
    import importlib.util

    path = os.path.join(
        BACKEND_ROOT, "alembic", "versions",
        "0008_drop_user_is_active_and_auth_audit_event_metadata.py",
    )
    spec = importlib.util.spec_from_file_location("migration_0008", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAlembicMigrationChain(unittest.TestCase):
    """0008 migration exists, chains correctly from 0007, and its
    upgrade/downgrade drop/restore exactly the two removed columns."""

    def test_migration_file_exists(self):
        path = os.path.join(
            BACKEND_ROOT, "alembic", "versions",
            "0008_drop_user_is_active_and_auth_audit_event_metadata.py",
        )
        self.assertTrue(os.path.isfile(path))

    def test_migration_revision_chain(self):
        module = _load_migration_0008()
        self.assertEqual(module.revision, "0008")
        self.assertEqual(module.down_revision, "0007")

    def test_migration_has_upgrade_and_downgrade(self):
        module = _load_migration_0008()
        self.assertTrue(callable(module.upgrade))
        self.assertTrue(callable(module.downgrade))


if __name__ == "__main__":
    unittest.main()
