# tests/test_environment_wiring_unit.py
"""
The real system-prompt assembly must actually include the Environment block.

Found live (2026-09-22, right after a restart): every execution logged
"Environment context injection failed (non-fatal) -- skipping" with
`NameError: name 'execution' is not defined`. A change of mine referenced `execution` inside
`ExecutionWorker._assemble_system_prompt`, where it is not a variable (the caller owns it). The
surrounding `try` swallowed the error, so the WHOLE Environment block -- date, time, OS, shell,
the user's timezone and location -- was silently missing from every prompt, for about 60
executions, and the unit tests passed because they only exercised `_build_environment_context`
in isolation, never the wiring.

These tests call the real `_assemble_system_prompt` (only the database session factory is
stubbed) and fail if the block is missing or if that warning is logged.

Run with:
    pytest tests/test_environment_wiring_unit.py -v
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.tools.dynamic_registry import DynamicToolRegistry
from core.tools.user_registry import UserToolRegistry


async def _assemble(**overrides):
    from core.execution import worker as worker_module
    from core.execution.worker import ExecutionWorker

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=False)

    kwargs = dict(
        execution_id="e1", conversation_id="conv-wiring", user_id="wiring-user",
        execution_mode="plan_execute", resolved_project_id=None, project_status=None,
        explicit_tools=[], session={"skills_mode": "off"}, preamble_is_progressive=True,
        user_tools=UserToolRegistry(default_tools=False, enabled_tools=[]),
        dynamic_registry=DynamicToolRegistry(), initial_message="hello",
    )
    kwargs.update(overrides)

    records = []

    class _Grab(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Grab(level=logging.WARNING)
    log = logging.getLogger(worker_module.__name__)
    log.addHandler(handler)
    try:
        with patch.object(worker_module, "AsyncSessionLocal", return_value=cm):
            prompt = await ExecutionWorker.__new__(ExecutionWorker)._assemble_system_prompt(**kwargs)
    finally:
        log.removeHandler(handler)
    return prompt, records


class TestEnvironmentBlockReachesThePrompt(unittest.IsolatedAsyncioTestCase):

    async def test_the_block_is_in_the_assembled_prompt(self):
        prompt, _ = await _assemble()
        self.assertIn("Environment", prompt)
        for expected in ("**Date:**", "**Time:**", "**OS:**", "**Shell:**"):
            self.assertIn(expected, prompt)

    async def test_the_injection_did_not_fail(self):
        _, records = await _assemble()
        failures = [r for r in records if "Environment context injection failed" in r.getMessage()]
        self.assertEqual(failures, [], failures and failures[0].exc_info)

    async def test_the_conversation_working_folder_line_is_present(self):
        prompt, _ = await _assemble(working_folder_kind="conversation")
        line = next(l for l in prompt.splitlines() if "Working folder" in l)
        self.assertIn("this conversation's folder", line)
        self.assertNotIn("eva_workspace", prompt.split("Working folder")[1].split("\n")[0])

    async def test_a_project_is_named_as_such(self):
        prompt, _ = await _assemble(working_folder_kind="project")
        self.assertIn("the active project's folder", prompt)

    async def test_without_a_kind_the_block_is_still_injected(self):
        prompt, records = await _assemble()
        self.assertIn("**Date:**", prompt)
        self.assertNotIn("Working folder", prompt)
        self.assertEqual([r for r in records if "Environment context" in r.getMessage()], [])


if __name__ == "__main__":
    unittest.main()
