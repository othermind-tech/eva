# tests/test_list_files_dirs_unit.py
"""
list_files lists subfolders by default.

Found live (2026-09-21, browser agent loop): an agent asked to work on `todo_probe/` called
list_files on the conversation folder, got only `["conversation.jsonl"]`, and spent about ten calls
looking for a folder that was right there -- the tool hid directories unless `include_dirs=true`
was passed, and its description never said so. (An older regression test already built a `src/`
folder and asserted only that README.md was listed.) Folders now show up, marked with a trailing
"/"; `include_dirs=false` restores files-only.

Run with:
    pytest tests/test_list_files_dirs_unit.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock


async def _list(directory, **args):
    from tools.default.file_tools import ListFilesTool
    tool = ListFilesTool()
    tool.execution = MagicMock(user_id="u", conversation_id=None, project_workspace=directory)
    result = await tool.run({"path": ".", **args})
    return result.result


class TestListFilesShowsFolders(unittest.IsolatedAsyncioTestCase):

    def _make_tree(self):
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "conversation.jsonl").write_text("{}", encoding="utf-8")
        (Path(tmp) / "todo_probe").mkdir()
        (Path(tmp) / "todo_probe" / "index.html").write_text("<html>", encoding="utf-8")
        return tmp

    async def test_default_lists_files_and_subfolders(self):
        result = await _list(self._make_tree())
        self.assertEqual(result["entries"], ["conversation.jsonl", "todo_probe/"])

    async def test_include_dirs_false_gives_files_only(self):
        result = await _list(self._make_tree(), include_dirs=False)
        self.assertEqual(result["entries"], ["conversation.jsonl"])

    async def test_it_is_still_not_recursive(self):
        result = await _list(self._make_tree())
        self.assertNotIn("index.html", " ".join(result["entries"]))

    async def test_extension_filter_still_filters_files(self):
        result = await _list(self._make_tree(), extensions=["jsonl"])
        self.assertIn("conversation.jsonl", result["entries"])
        self.assertNotIn("index.html", result["entries"])

    def test_the_argument_model_default_matches_the_runtime_default(self):
        from tools.default.file_tools import ListFilesArgs
        self.assertTrue(ListFilesArgs().include_dirs)

    def test_the_description_no_longer_tells_models_to_opt_in(self):
        from core.tools.registry import DefaultToolRegistry
        from core.tools.loader import load_all_tools
        load_all_tools()
        desc = " ".join(DefaultToolRegistry.all()["list_files"].description.split())
        self.assertNotIn("NOT listed", desc)
        self.assertIn("subfolders", desc)
        self.assertIn("include_dirs=false", desc)


if __name__ == "__main__":
    unittest.main()
