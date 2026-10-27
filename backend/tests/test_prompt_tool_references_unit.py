# tests/test_prompt_tool_references_unit.py
"""
Guards against instructions that name tools or behaviour that do not exist.

Every case below was found by a live run where a model followed guidance EVA
itself gave it:
  - the skills block told models to read skills with a "file_read" tool (the
    tool is read_file); several skills listed file_read / file_write /
    shell_command / list_directory / get_file_info / file_edit in their
    allowed-tools header;
  - trigger_daemon told the model to "poll GET /executions/{id}/status" -- a
    REST call it cannot make (http_get blocks localhost);
  - the shell tool told the model to use trigger_daemon() for dev servers.

Covers:
  - a denylist of names that have been wrong before, scanned across tool
    descriptions and argument descriptions, prompts/, skills/ and the default
    agents' tool lists;
  - every skill's allowed-tools entry and every default agent's tool list names
    a real tool (MCP-style "x__y" names are checked against the pinned
    Playwright list where they are playwright__*);
  - no tool-facing text tells a model to call an EVA REST endpoint;
  - the tool named by the skills block exists;
  - skills/playwright-browser-testing/SKILL.md: every playwright__* tool it
    mentions exists on the pinned server, every worked JSON example uses real
    parameter names, and the pinned version has not moved without the skill
    being re-checked.

Run with:
    pytest tests/test_prompt_tool_references_unit.py -v
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND / "tests"))

from fixtures.playwright_mcp_tools import PARAMS, PINNED_VERSION, TOOL_NAMES  # noqa: E402

# Names that have been wrong before. None of these is a real EVA tool.
WRONG_TOOL_NAMES = ("file_read", "file_write", "file_edit", "shell_command",
                    "list_directory", "get_file_info")
_WRONG_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(WRONG_TOOL_NAMES) + r")(?![A-Za-z0-9_])")
_REST_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[A-Za-z{]")
_PW_PREFIX = "playwright__"


def _real_tools() -> dict:
    from core.tools.loader import load_all_tools
    from core.tools.registry import DefaultToolRegistry
    load_all_tools()
    return DefaultToolRegistry.all()


def _tool_texts() -> dict:
    """{tool name: every string a model is shown about it} -- description, summary, argument docs."""
    out = {}
    for name, cls in _real_tools().items():
        parts = [getattr(cls, "description", "") or "", getattr(cls, "summary", "") or ""]
        schema = getattr(cls, "args_schema", None)
        if schema is not None:
            for prop in schema.model_json_schema().get("properties", {}).values():
                parts.append(prop.get("description", "") or "")
        out[name] = "\n".join(parts)
    return out


def _registry_from_copy():
    """
    A SkillRegistry over a COPY of skills/ without *.skill bundles.

    Never load the real skills/ directory in a test: SkillLoader auto-extracts every
    *.skill bundle over its folder, which silently rewrites tracked files.
    """
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(_BACKEND / "skills", tmp / "skills", ignore=shutil.ignore_patterns("*.skill"))
    from core.skills.registry import SkillRegistry
    reg = SkillRegistry(tmp / "skills")
    reg.load()
    return reg, tmp


def _skill_files():
    return sorted((_BACKEND / "skills").glob("*/SKILL.md"))


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", text.replace("\r\n", "\n"), re.S)
    return yaml.safe_load(m.group(1)) if m else {}


class TestNoKnownWrongToolNames(unittest.TestCase):

    def test_tool_descriptions_and_argument_docs(self):
        for name, text in _tool_texts().items():
            self.assertIsNone(_WRONG_RE.search(text), f"tool '{name}' mentions a non-existent tool: {_WRONG_RE.search(text)}")

    def test_prompts(self):
        for path in sorted((_BACKEND / "prompts").glob("*.py")):
            m = _WRONG_RE.search(path.read_text(encoding="utf-8"))
            self.assertIsNone(m, f"{path.name} mentions a non-existent tool: {m}")

    def test_skill_files(self):
        for path in sorted((_BACKEND / "skills").rglob("*.md")):
            m = _WRONG_RE.search(path.read_text(encoding="utf-8"))
            self.assertIsNone(m, f"{path.relative_to(_BACKEND)} mentions a non-existent tool: {m}")

    def test_skills_block_text(self):
        reg, tmp = _registry_from_copy()
        try:
            block = reg.build_available_skills_block("browser")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertTrue(block)
        self.assertIsNone(_WRONG_RE.search(block))

    def test_conversation_summary_hint_names_a_real_tool(self):
        src = (_BACKEND / "api" / "conversation_management.py").read_text(encoding="utf-8")
        self.assertIsNone(_WRONG_RE.search(src), "conversation_management.py names a non-existent tool")


class TestToolListsAreReal(unittest.TestCase):

    def _valid(self, name: str) -> bool:
        if name in _real_tools():
            return True
        if name.startswith(_PW_PREFIX):
            return name[len(_PW_PREFIX):] in TOOL_NAMES
        return False

    def test_every_skill_allowed_tool_exists(self):
        for path in _skill_files():
            for tool in _frontmatter(path).get("allowed-tools") or []:
                self.assertTrue(self._valid(tool), f"{path.parent.name}: allowed-tools lists unknown tool {tool!r}")

    def test_every_default_agent_tool_exists(self):
        real = _real_tools()
        for path in sorted((_BACKEND / "default_agents").glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for tool in data.get("tools") or []:
                self.assertIn(tool, real, f"{path.name}: tools lists unknown tool {tool!r}")

    def test_skills_block_tells_the_model_to_use_a_real_tool(self):
        reg, tmp = _registry_from_copy()
        try:
            block = reg.build_available_skills_block("browser")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        m = re.search(r"using the (\w+) tool", block)
        self.assertIsNotNone(m, "skills block no longer names the tool used to read a skill")
        self.assertIn(m.group(1), _real_tools())


class TestNoRestInstructionsForModels(unittest.TestCase):
    """A model has no way to call EVA's own REST API (http_get blocks localhost)."""

    def test_tool_text_never_tells_the_model_to_call_an_endpoint(self):
        for name, text in _tool_texts().items():
            m = _REST_RE.search(text)
            self.assertIsNone(m, f"tool '{name}' tells the model to call a REST endpoint: {m.group(0) if m else ''}")


    def test_tool_source_strings_never_tell_the_model_to_call_an_endpoint(self):
        """Also covers error messages a tool returns at runtime, not just its description."""
        offenders = []
        for path in sorted((_BACKEND / "tools" / "default").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = getattr(node, "body", [])
                    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                        docstrings.add(id(body[0].value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    m = _REST_RE.search(node.value)
                    if m:
                        offenders.append(f"{path.name}:{node.lineno}: {node.value[:80]!r}")
        self.assertEqual(offenders, [], "tool strings tell the model to call an EVA REST endpoint")


class TestSkillBundlesMatchTheirFolders(unittest.TestCase):
    """
    SkillLoader auto-extracts a skills/<name>.skill ZIP over skills/<name>/ every time it
    loads that directory, so a bundle that disagrees with its folder silently undoes edits
    to the folder. Keep them identical.
    """

    def test_every_bundle_equals_its_folder(self):
        bundles = sorted((_BACKEND / "skills").glob("*.skill"))
        self.assertTrue(bundles, "no .skill bundles found -- delete this test if bundles were retired")
        for bundle in bundles:
            folder = bundle.with_suffix("")
            with zipfile.ZipFile(bundle) as z:
                for name in z.namelist():
                    if name.endswith("/"):
                        continue
                    target = folder / name
                    self.assertTrue(target.exists(), f"{bundle.name}: {name} missing from {folder.name}/")
                    self.assertEqual(z.read(name).replace(b"\r\n", b"\n"), target.read_bytes().replace(b"\r\n", b"\n"),
                                     f"{bundle.name}: {name} differs from {folder.name}/{name} -- rebuild the bundle")


class TestShellAndBackgroundProcessGuidanceIsConsistent(unittest.TestCase):

    def test_shell_points_at_a_real_tool_for_servers(self):
        tools = _real_tools()
        self.assertIn("background_process", tools)
        self.assertIn("background_process", tools["shell"].description)
        self.assertNotIn("trigger_daemon", tools["shell"].description)

    def test_trigger_daemon_points_at_tools_that_exist(self):
        tools = _real_tools()
        desc = tools["trigger_daemon"].description
        for referenced in ("get_daemon_config", "schedule_wakeup"):
            self.assertIn(referenced, desc)
            self.assertIn(referenced, tools)

    def test_file_tools_describe_where_relative_paths_really_resolve(self):
        # Relative paths resolve against the project or conversation folder
        # (utils.workspace.resolve_path_for_user), not the shared workspace/ folder.
        for name in ("read_file", "write_file", "list_files", "find_files", "search_files", "stat_file", "delete_file"):
            desc = _real_tools()[name].description
            self.assertNotIn("user workspace", desc, name)
            self.assertIn("conversation's folder", desc, name)


class TestPlaywrightSkill(unittest.TestCase):

    PATH = _BACKEND / "skills" / "playwright-browser-testing" / "SKILL.md"

    def setUp(self):
        self.text = self.PATH.read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_skill_loads_with_a_keyword_trigger(self):
        from core.skills.trigger import KeywordTrigger
        reg, tmp = _registry_from_copy()
        try:
            meta = reg.get_skill("playwright-browser-testing")
            self.assertIsNotNone(meta)
            self.assertIsInstance(meta.trigger, KeywordTrigger)
            self.assertIn("playwright-browser-testing", reg.build_available_skills_block("open it in the browser"))
            self.assertIn("playwright-browser-testing", reg.build_available_skills_block("the page is blank"))
            self.assertNotIn("playwright-browser-testing", reg.build_available_skills_block("rename this variable"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_every_playwright_tool_it_mentions_exists_on_the_pinned_server(self):
        mentioned = set(re.findall(r"playwright__(browser_[a-z_]+)", self.text))
        self.assertGreaterEqual(len(mentioned), 10)
        self.assertEqual(mentioned - TOOL_NAMES, set(), "skill names a Playwright tool the pinned server does not have")

    def test_worked_json_examples_use_real_parameter_names(self):
        checked = 0
        pattern = re.compile(r"`playwright__(browser_[a-z_]+)`\s+(?:with\s+)?`(\{[^`]*\})`")
        for m in pattern.finditer(self.text):
            tool, raw = m.group(1), re.sub(r":\s*N\b", ": 1", m.group(2))
            args = json.loads(raw)
            self.assertIn(tool, PARAMS, f"add {tool} to fixtures/playwright_mcp_tools.PARAMS")
            self.assertEqual(set(args) - PARAMS[tool], set(), f"{tool} example uses unknown parameter(s): {raw}")
            checked += 1
        self.assertGreaterEqual(checked, 6, "the example pattern stopped matching -- the test would pass vacuously")

    def test_background_process_advice_matches_the_real_tool(self):
        from tools.default.process_tools import BackgroundProcessArgs
        self.assertIn("wait_seconds", BackgroundProcessArgs.model_fields)
        self.assertIn("wait_seconds", self.text)
        for action in ("start", "tail", "stop"):
            self.assertIn(f'action="{action}"', self.text)

    def test_pinned_version_has_not_moved_without_recapturing(self):
        from core.defaults.seeder import list_default_mcps
        pw = next(m for m in list_default_mcps() if m["name"] == "playwright")
        self.assertIn(f"@playwright/mcp@{PINNED_VERSION}", pw["args"],
                      "default_mcps/playwright.yaml moved to a new @playwright/mcp version: re-capture "
                      "tests/fixtures/playwright_mcp_tools.py, re-read the skill, then update PINNED_VERSION")

    def test_it_warns_against_the_two_console_parameters_that_misled_models(self):
        self.assertIn("Do not pass `filename`", self.text)
        self.assertIn("Do not pass `all`", self.text)
        self.assertIn("there is no `ref` parameter", self.text)


if __name__ == "__main__":
    unittest.main()
