# tests/test_environment_working_folder_unit.py
"""
The Environment block tells the model which folder relative paths land in (kind only -- never the
absolute path, which carries the OS user name and the user's id).

Found live (2026-09-21, browser debug loop, `nemotron-3-ultra`): asked to work on `todo_probe/` "in
your workspace", the agent tried the user's root folder, path-style globs and shell `dir` for about
fourteen calls -- minutes, on a slow model -- before finding a folder that a relative path would have
reached at once. Nothing in the prompt said where relative paths land, so the model had to guess.

Run with:
    pytest tests/test_environment_working_folder_unit.py -v
"""

from __future__ import annotations

import unittest


class TestEnvironmentWorkingFolder(unittest.TestCase):

    def _block(self, **kw):
        from core.execution.worker_context import _build_environment_context
        return _build_environment_context({}, **kw)

    def test_the_kind_of_folder_and_what_resolves_against_it_are_stated(self):
        line = next(l for l in self._block(working_folder_kind="conversation").splitlines() if "Working folder" in l)
        self.assertIn("this conversation's folder", line)
        self.assertIn("relative paths", line)
        self.assertIn("cwd", line)

    def test_a_project_is_named_as_such(self):
        line = next(l for l in self._block(working_folder_kind="project").splitlines() if "Working folder" in l)
        self.assertIn("the active project's folder", line)

    def test_no_absolute_path_or_user_name_reaches_the_prompt(self):
        import getpass
        for kind in ("conversation", "project"):
            block = self._block(working_folder_kind=kind)
            self.assertNotIn("eva_workspace", block)
            self.assertNotIn("Users", block)
            self.assertNotIn(getpass.getuser(), block)

    def test_an_unknown_kind_adds_nothing(self):
        self.assertNotIn("Working folder", self._block(working_folder_kind="something-else"))

    def test_without_a_folder_the_block_is_as_before(self):
        block = self._block()
        self.assertNotIn("Working folder", block)
        for expected in ("Date:", "Time:", "OS:", "Shell:", "Python:"):
            self.assertIn(expected, block)

    def test_callers_using_the_old_signature_still_work(self):
        from core.execution.worker_context import _build_environment_context
        self.assertTrue(_build_environment_context({"user_location": "Mumbai"}))

    def test_optional_fields_still_follow_the_folder(self):
        from core.execution.worker_context import _build_environment_context
        block = _build_environment_context({"user_location": "Mumbai, India"}, working_folder_kind="project")
        self.assertIn("Working folder", block)
        self.assertIn("Mumbai, India", block)


if __name__ == "__main__":
    unittest.main()
