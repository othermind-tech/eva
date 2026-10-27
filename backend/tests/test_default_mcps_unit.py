# tests/test_default_mcps_unit.py
"""
Unit tests for the default MCP seeder (M-01) and the Playwright browser-
automation default (M-02), plus the MCP result-normalization hardening that
keeps an image block (a screenshot) from flooding the model's context.

Covers:
  - The shipped default_mcps/playwright.yaml: valid, disabled-by-default,
    pinned, screenshots-to-file, parses identically with and without PyYAML.
  - Placeholder resolution ({workspace}, {browser_channel}).
  - seed_default_mcps(): inserts once, is idempotent, never overwrites a
    user's own config, reports missing required executables as
    pending_install, rejects invalid definitions, and never connects to (or
    syncs) the server it seeds.
  - POST /mcp/seed-defaults route function and seed_user_defaults() wiring.
  - core/mcp/runtime.py::_content_block_to_text and MCPRuntime.execute().

Run with:
    cd <project_root>
    pytest tests/test_default_mcps_unit.py -v
"""

from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


def _make_engine_and_session():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, Session


class _DbHarnessMixin:
    """
    In-memory SQLite, patched into core.db.session.AsyncSessionLocal. Works
    for the seeder and MCPRuntime because both import AsyncSessionLocal
    locally (or receive a session) rather than binding it at module import.
    """

    async def asyncSetUp(self):
        self.engine, self.Session = _make_engine_and_session()
        async with self.engine.begin() as conn:
            from core.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

        import core.db.session as _dbs
        self._orig_session_local = _dbs.AsyncSessionLocal
        _dbs.AsyncSessionLocal = self.Session

    async def asyncTearDown(self):
        import core.db.session as _dbs
        _dbs.AsyncSessionLocal = self._orig_session_local
        await self.engine.dispose()

    async def _configs(self, user_id):
        from core.db.repository import MCPRepo
        async with self.Session() as db:
            return await MCPRepo.list_configs(db, user_id)


# ===========================================================================
# The shipped playwright.yaml
# ===========================================================================

class TestShippedPlaywrightDefinition(unittest.TestCase):

    def _definition(self):
        from core.defaults.seeder import list_default_mcps
        matches = [m for m in list_default_mcps() if m.get("name") == "playwright"]
        self.assertEqual(len(matches), 1, "default_mcps/playwright.yaml must define exactly one 'playwright'")
        return matches[0]

    def test_definition_is_valid(self):
        from core.defaults.seeder import _validate_mcp_definition
        self.assertIsNone(_validate_mcp_definition(self._definition()))

    def test_disabled_by_default_and_requires_npx(self):
        d = self._definition()
        self.assertIs(d["enabled"], False, "must ship disabled -- ~25 tool schemas + a visible browser window are opt-in")
        self.assertEqual(d["requires"], ["npx"])
        self.assertEqual(d["transport"], "stdio")
        self.assertEqual(d["command"], "npx")

    def test_version_pinned_and_safe_flags_present(self):
        args = self._definition()["args"]
        self.assertTrue(any(a.startswith("@playwright/mcp@") and a != "@playwright/mcp@latest" for a in args),
                        "package version must be pinned, not @latest")
        self.assertIn("--isolated", args, "must not touch the user's real browser profile")
        i = args.index("--image-responses")
        self.assertEqual(args[i + 1], "omit", "screenshots must never be inlined as base64")

    def test_file_paths_in_tool_results_are_absolute(self):
        # Relative paths resolve against Playwright's cwd, not the conversation
        # directory EVA's file tools use -- the agent could not open them.
        args = self._definition()["args"]
        self.assertEqual(args[args.index("--file-paths") + 1], "absolute")

    def test_files_are_directed_into_the_user_workspace(self):
        d = self._definition()
        self.assertEqual(d["cwd"], "{workspace}")
        args = d["args"]
        self.assertEqual(args[args.index("--output-dir") + 1], "{workspace}/_browser_output")

    def test_parses_identically_without_pyyaml(self):
        from core.defaults.seeder import DEFAULT_MCPS_DIR, _simple_yaml_parse
        import yaml
        text = (DEFAULT_MCPS_DIR / "playwright.yaml").read_text(encoding="utf-8")
        via_pyyaml = yaml.safe_load(text)
        via_fallback = _simple_yaml_parse(text)
        for key in ("name", "transport", "command", "args", "cwd", "enabled", "requires"):
            self.assertEqual(via_fallback.get(key), via_pyyaml.get(key), f"fallback parser diverges on {key!r}")


# ===========================================================================
# Placeholders
# ===========================================================================

class TestPlaceholders(unittest.TestCase):

    def test_resolves_nested_values_and_leaves_unknown_braces_alone(self):
        from core.defaults.seeder import _resolve_placeholders
        out = _resolve_placeholders(
            ["--dir", "{workspace}/x", {"K": "{browser_channel}"}, 5, "{unknown}"],
            {"workspace": "W", "browser_channel": "msedge"},
        )
        self.assertEqual(out, ["--dir", "W/x", {"K": "msedge"}, 5, "{unknown}"])

    def test_browser_channel_is_platform_aware(self):
        from core import defaults  # noqa: F401
        import core.defaults.seeder as seeder
        with patch.object(seeder.sys, "platform", "win32"):
            self.assertEqual(seeder._mcp_placeholders("u1")["browser_channel"], "msedge")
        with patch.object(seeder.sys, "platform", "darwin"):
            self.assertEqual(seeder._mcp_placeholders("u1")["browser_channel"], "chrome")
        with patch.object(seeder.sys, "platform", "linux"):
            self.assertEqual(seeder._mcp_placeholders("u1")["browser_channel"], "chrome")


# ===========================================================================
# Seeding
# ===========================================================================

class TestSeedDefaultMcps(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    USER = "seed-mcp-user"

    async def test_seeds_playwright_disabled_with_resolved_placeholders(self):
        from core.defaults.seeder import seed_default_mcps
        from utils.workspace import get_user_workspace

        result = await seed_default_mcps(self.USER)
        self.assertEqual(result["mcps_seeded"], 1)
        self.assertEqual(result["errors"], [])

        cfgs = await self._configs(self.USER)
        self.assertEqual([c.name for c in cfgs], ["playwright"])
        cfg = cfgs[0]
        self.assertIs(cfg.enabled, False)
        self.assertEqual(cfg.transport, "stdio")
        self.assertEqual(cfg.command, "npx")
        self.assertIsNone(cfg.project_id, "default MCPs are global, not project-scoped")

        workspace = get_user_workspace(self.USER)
        self.assertEqual(cfg.cwd, workspace)
        self.assertIn(f"{workspace}/_browser_output", cfg.args)
        joined = " ".join(cfg.args)
        self.assertNotIn("{", joined, "every placeholder must be resolved before it is stored")
        self.assertIn(cfg.args[cfg.args.index("--browser") + 1], ("msedge", "chrome"))

    async def test_second_seed_is_a_noop_and_preserves_user_edits(self):
        from core.defaults.seeder import seed_default_mcps
        from core.db.repository import MCPRepo

        await seed_default_mcps(self.USER)
        async with self.Session() as db:
            cfg = (await MCPRepo.list_configs(db, self.USER))[0]
            await MCPRepo.update_config(db, cfg, {"enabled": True, "args": ["-y", "custom", "--headless"]})

        again = await seed_default_mcps(self.USER)
        self.assertEqual(again["mcps_seeded"], 0)
        self.assertEqual(again["mcps_skipped"], 1)

        cfgs = await self._configs(self.USER)
        self.assertEqual(len(cfgs), 1)
        self.assertIs(cfgs[0].enabled, True)
        self.assertEqual(cfgs[0].args, ["-y", "custom", "--headless"])

    async def test_existing_config_with_same_name_is_never_overwritten(self):
        from core.defaults.seeder import seed_default_mcps
        from core.db.repository import MCPRepo

        async with self.Session() as db:
            await MCPRepo.create_config(db, self.USER, {
                "name": "playwright", "transport": "http", "url": "http://example.test/mcp", "enabled": True,
            })
        result = await seed_default_mcps(self.USER)
        self.assertEqual(result["mcps_seeded"], 0)
        self.assertEqual(result["mcps_skipped"], 1)
        cfg = (await self._configs(self.USER))[0]
        self.assertEqual(cfg.transport, "http")
        self.assertEqual(cfg.url, "http://example.test/mcp")

    async def test_seeding_is_per_user(self):
        from core.defaults.seeder import seed_default_mcps
        await seed_default_mcps("user-a")
        result_b = await seed_default_mcps("user-b")
        self.assertEqual(result_b["mcps_seeded"], 1)
        self.assertEqual(len(await self._configs("user-a")), 1)
        self.assertEqual(len(await self._configs("user-b")), 1)

    async def test_missing_required_executable_is_reported_but_still_seeded(self):
        from core.defaults.seeder import seed_default_mcps
        with patch("core.defaults.seeder.shutil.which", return_value=None):
            result = await seed_default_mcps(self.USER)
        self.assertEqual(result["mcps_seeded"], 1)
        self.assertEqual(result["mcps_pending_install"], [{"name": "playwright", "missing": ["npx"]}])
        self.assertEqual(len(await self._configs(self.USER)), 1)

    async def test_present_executable_is_not_reported_pending(self):
        from core.defaults.seeder import seed_default_mcps
        with patch("core.defaults.seeder.shutil.which", return_value="/usr/bin/npx"):
            result = await seed_default_mcps(self.USER)
        self.assertEqual(result["mcps_pending_install"], [])

    async def test_seeding_never_connects_to_or_syncs_the_server(self):
        from core.defaults.seeder import seed_default_mcps
        with patch("core.mcp.manager.MCPManager.sync_tools_for_config", new_callable=AsyncMock) as sync, \
             patch("core.mcp.connection.MCPConnection.__aenter__", new_callable=AsyncMock) as connect:
            await seed_default_mcps(self.USER)
        sync.assert_not_called()
        connect.assert_not_called()

    async def test_invalid_definitions_are_reported_and_skipped(self):
        from core.defaults import seeder
        cases = {
            "no_name.yaml": "transport: stdio\ncommand: npx\n",
            "bad_transport.yaml": "name: a\ntransport: carrier-pigeon\n",
            "stdio_no_command.yaml": "name: b\ntransport: stdio\n",
            "http_no_url.yaml": "name: c\ntransport: http\n",
            "reserved_delimiter.yaml": "name: has__delimiter\ntransport: stdio\ncommand: npx\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for fname, body in cases.items():
                (Path(tmp) / fname).write_text(body, encoding="utf-8")
            with patch.object(seeder, "DEFAULT_MCPS_DIR", Path(tmp)):
                result = await seeder.seed_default_mcps(self.USER)
        self.assertEqual(result["mcps_seeded"], 0)
        self.assertEqual(len(result["errors"]), len(cases))
        self.assertEqual(await self._configs(self.USER), [])

    async def test_missing_directory_is_a_silent_noop(self):
        from core.defaults import seeder
        with patch.object(seeder, "DEFAULT_MCPS_DIR", Path("Z:/definitely/not/here")):
            result = await seeder.seed_default_mcps(self.USER)
        self.assertEqual(result["mcps_seeded"], 0)
        self.assertEqual(result["errors"], [])

    async def test_seed_user_defaults_also_seeds_mcps(self):
        from core.defaults import seeder
        with patch.object(seeder, "_seed_skills"), \
             patch.object(seeder, "_seed_agents", new_callable=AsyncMock):
            result = await seeder.seed_user_defaults(self.USER)
        self.assertEqual(result["mcps_seeded"], 1)
        self.assertEqual([c.name for c in await self._configs(self.USER)], ["playwright"])

    async def test_seed_endpoint_returns_summary(self):
        from api.mcp import seed_default_mcp_configs, SeedDefaultMCPsResponse
        with patch("core.defaults.seeder.shutil.which", return_value=None):
            body = await seed_default_mcp_configs(auth={"user_id": self.USER})
        parsed = SeedDefaultMCPsResponse(**body)
        self.assertEqual(parsed.status, "ok")
        self.assertEqual(parsed.mcps_seeded, 1)
        self.assertEqual(parsed.mcps_pending_install[0].name, "playwright")

        again = SeedDefaultMCPsResponse(**await seed_default_mcp_configs(auth={"user_id": self.USER}))
        self.assertEqual((again.mcps_seeded, again.mcps_skipped), (0, 1))


# ===========================================================================
# MCP result normalization
# ===========================================================================

class TestContentBlockToText(unittest.TestCase):

    def test_text_block_passes_through_unchanged(self):
        from mcp.types import TextContent
        from core.mcp.runtime import _content_block_to_text
        self.assertEqual(_content_block_to_text(TextContent(type="text", text="hello")), "hello")

    def test_empty_text_block_stays_empty(self):
        from mcp.types import TextContent
        from core.mcp.runtime import _content_block_to_text
        self.assertEqual(_content_block_to_text(TextContent(type="text", text="")), "")

    def test_image_block_becomes_placeholder_without_the_payload(self):
        from mcp.types import ImageContent
        from core.mcp.runtime import _content_block_to_text
        payload = base64.b64encode(b"\x89PNG" + b"x" * 40_000).decode()
        out = _content_block_to_text(ImageContent(type="image", data=payload, mimeType="image/png"))
        self.assertIn("image omitted", out)
        self.assertIn("image/png", out)
        self.assertNotIn(payload[:60], out)
        self.assertLess(len(out), 120)

    def test_other_block_types_fall_back_to_str(self):
        from core.mcp.runtime import _content_block_to_text

        class Odd:
            def __str__(self):
                return "odd-block"
        self.assertEqual(_content_block_to_text(Odd()), "odd-block")


class TestMcpRuntimeExecuteNormalization(_DbHarnessMixin, unittest.IsolatedAsyncioTestCase):

    async def test_execute_replaces_image_blocks_and_keeps_text(self):
        from types import SimpleNamespace
        from mcp.types import TextContent, ImageContent
        from core.db.models import MCPToolMeta
        from core.db.repository import MCPRepo
        from core.mcp.runtime import MCPRuntime

        async with self.Session() as db:
            cfg = await MCPRepo.create_config(db, "u1", {"name": "shots", "transport": "stdio", "command": "x"})
            db.add(MCPToolMeta(config_id=cfg.id, name="snap", description="d", input_schema={}))
            await db.commit()

            big = base64.b64encode(b"z" * 30_000).decode()
            raw = SimpleNamespace(content=[
                TextContent(type="text", text="### Result\nok"),
                ImageContent(type="image", data=big, mimeType="image/png"),
            ])
            with patch("core.mcp.runtime.mcp_session_pool.call_tool", new=AsyncMock(return_value=raw)):
                result = await MCPRuntime.execute(
                    db=db, user_id="u1", tool_name="shots__snap", arguments={},
                )

        out = result.result
        self.assertEqual(out[0], "### Result\nok")
        self.assertIn("image omitted", out[1])
        self.assertNotIn(big[:60], str(out))


if __name__ == "__main__":
    unittest.main()
