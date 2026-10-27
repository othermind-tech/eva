# tests/test_no_undefined_names_unit.py
"""
No function in the modules changed by the V6.50 work may reference a name that does not exist.

Why: a `try/except Exception` that logs "(non-fatal)" turns a NameError into a silently missing
feature. Live (2026-09-22) the whole Environment block vanished from every prompt for ~60
executions because a function referenced `execution`, which is not a variable in that scope; nothing
failed, nothing was tested. Python only raises NameError when the line runs, so a static check over
every function catches this class before it ships.

Uses `symtable` (stdlib): for each function scope, every implicitly-global, referenced name must exist
in the module's namespace or in builtins.

Run with:
    pytest tests/test_no_undefined_names_unit.py -v
"""

from __future__ import annotations

import builtins
import importlib
import symtable
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent

# Modules touched by this work (plus their neighbours that share the same patterns).
MODULES = [
    "core.execution.worker", "core.execution.worker_context", "core.execution.worker_finalize",
    "core.execution.pev_runner", "core.execution.planner", "core.execution.verifier",
    "core.execution.step_record", "core.execution.step_extension",
    "core.shell.executor", "core.tools.dispatcher", "core.tools.tool_adapter", "core.tools.user_registry",
    "core.mcp.runtime", "core.defaults.seeder", "core.auth.hydration", "core.skills.registry",
    "core.memory.engine", "api.mcp", "api.conversation_management", "prompts.planner", "prompts.verifier",
    "tools.default.process_tools", "tools.default.file_tools", "tools.default.search_tools",
    "tools.default.shell_tool", "tools.default.git_tool", "tools.default.describe_tool",
    "tools.default.daemon_tools", "tools.default.delegate_tool", "tools.default.parallel_delegate_tool",
    "tools.default.coordinate_tool", "tools.default.messaging_tools", "tools.default.subagent_config_tools",
    "tools.default.wakeup_tools",
]

_ALLOWED = {"__class__", "__file__", "__name__", "__doc__", "__builtins__", "__package__", "__spec__"}


def undefined_names(source: str, filename: str, module_names) -> list:
    """[(qualified scope, name), ...] for names a function references but nothing defines."""
    module_names = set(module_names) | set(dir(builtins)) | _ALLOWED
    problems = []

    def walk(table, path):
        for child in table.get_children():
            here = path + [child.get_name()]
            if child.get_type() == "function":
                for sym in child.get_symbols():
                    if sym.is_referenced() and sym.is_global() and sym.get_name() not in module_names:
                        problems.append((".".join(here), sym.get_name()))
            walk(child, here)

    walk(symtable.symtable(source, filename, "exec"), [])
    return problems


class TestTheCheckerItself(unittest.TestCase):

    def test_it_reports_a_name_that_exists_nowhere(self):
        src = "class W:\n    def f(self, execution_id):\n        return getattr(execution, 'x')\n"
        self.assertEqual(undefined_names(src, "x.py", set()), [("W.f", "execution")])

    def test_it_accepts_parameters_locals_module_names_and_builtins(self):
        src = (
            "import os\nGLOBAL = 1\n"
            "def f(param):\n    local = param + GLOBAL\n    return len(os.sep) + local\n"
        )
        self.assertEqual(undefined_names(src, "x.py", {"os", "GLOBAL", "f"}), [])

    def test_it_accepts_names_imported_inside_the_function(self):
        src = "def f():\n    from os import path\n    return path\n"
        self.assertEqual(undefined_names(src, "x.py", set()), [])

    def test_it_follows_nested_functions(self):
        src = "def outer():\n    def inner():\n        return missing_name\n    return inner\n"
        self.assertEqual(undefined_names(src, "x.py", set()), [("outer.inner", "missing_name")])


class TestNoUndefinedNames(unittest.TestCase):

    def test_every_changed_module_has_no_undefined_names(self):
        report = {}
        for name in MODULES:
            module = importlib.import_module(name)
            path = Path(module.__file__)
            problems = undefined_names(path.read_text(encoding="utf-8"), str(path), vars(module))
            if problems:
                report[name] = problems
        self.assertEqual(report, {}, f"undefined names (each is a NameError waiting for its line to run): {report}")


if __name__ == "__main__":
    unittest.main()
