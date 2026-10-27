# tests/test_shell_safety_windows_paths_unit.py
"""
S-14: ShellSafety's path-traversal check was POSIX-only, with two opposite failures on
Windows -- EVA's primary platform -- plus a third, more general bug found while fixing the
second (see gap report S-14, and core/shell/safety.py::_check_path_traversal's docstring).

  1. FALSE POSITIVE -- a Windows CLI switch (`cmd /c`, `dir /s /b`, `ping /n 1`,
     `robocopy /MIR`) was matched as an absolute path and BLOCKED as "outside the
     workspace", with a remedy ("approve the command") that makes no sense for a token
     that was never a path. Live, an agent lost ~10 calls hunting for a folder because of
     this. Demonstrated on the real check before the fix:
         block  cmd /c dir /s /b todo_probe
         block  ping /n 1 127.0.0.1
     Fixed: on Windows only, a short single-segment `/token[:value]` with no further `/`
     is a CLI option, not a path, and is skipped.

  2. FALSE NEGATIVE -- the old pattern only recognised a leading `/`, so a genuine
     Windows absolute path never matched at all: `type C:\\Users\\<user>\\.ssh\\id_rsa`
     ran with ZERO warning in auto mode. The one check meant to sandbox the agent
     provided no containment on Windows. Fixed: also match drive-letter (`X:\\...`,
     `X:/...`) and UNC (`\\\\server\\share\\...`) paths, excluding `C:\\Windows\\...`
     the same way the POSIX pattern excludes `/usr/bin`, `/bin`, `/lib`.

  3. FAIL-OPEN ON RESOLUTION FAILURE -- found while testing fix 2: a UNC path to an
     unreachable host makes `Path.resolve()` raise `FileNotFoundError` (Windows hits the
     network to resolve a UNC path even in non-strict mode), not `ValueError`, and the
     old code's `except Exception: pass` silently let anything but `ValueError` through --
     fail-OPEN in a security check. Fixed: any resolution failure now means "outside the
     workspace" (fail closed).

Run with:
    pytest tests/test_shell_safety_windows_paths_unit.py -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.shell.safety import (
    SafetyAction,
    ShellSafety,
    _is_windows_cli_switch,
)


def _tmp_workspace() -> str:
    return tempfile.mkdtemp(prefix="s14-ws-")


# ─────────────────────────────────────────────────────────────────────────────
# 1. _is_windows_cli_switch -- pure function, no filesystem, no platform dependency
# ─────────────────────────────────────────────────────────────────────────────

class TestIsWindowsCliSwitch(unittest.TestCase):

    def test_single_letter_switches_are_recognised(self):
        for tok in ("/c", "/s", "/b", "/n", "/e", "/y", "/q"):
            self.assertTrue(_is_windows_cli_switch(tok), tok)

    def test_multi_letter_switches_are_recognised(self):
        for tok in ("/MIR", "/COPYALL", "/RESTARTABLE"):
            self.assertTrue(_is_windows_cli_switch(tok), tok)

    def test_keyed_switches_with_a_colon_value_are_recognised(self):
        for tok in ("/max:5", "/LEV:3", "/FO:CSV"):
            self.assertTrue(_is_windows_cli_switch(tok), tok)

    def test_a_multi_segment_path_is_never_a_switch(self):
        for tok in ("/etc/passwd", "/a/b", "/usr/local/bin/x"):
            self.assertFalse(_is_windows_cli_switch(tok), tok)

    def test_a_switch_shaped_token_starting_with_a_digit_is_not_a_switch(self):
        # Windows switches always start with a letter; a leading digit is treated
        # conservatively as a real path candidate instead.
        self.assertFalse(_is_windows_cli_switch("/1abc"))

    def test_an_implausibly_long_token_is_not_treated_as_a_switch(self):
        self.assertFalse(_is_windows_cli_switch("/" + "x" * 20))

    def test_documented_tradeoff_a_bare_short_word_is_treated_as_a_switch(self):
        # /tmp has no further '/', so on Windows it is treated as a switch, not a path --
        # accepted because Windows has no native /tmp, and this heuristic only ever runs
        # when is_windows is True (see the integration tests below).
        self.assertTrue(_is_windows_cli_switch("/tmp"))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Fix 1 -- the switch exemption applies only on Windows
# ─────────────────────────────────────────────────────────────────────────────

class TestSwitchExemptionIsWindowsOnly(unittest.TestCase):

    def _check(self, cmd, is_windows):
        ws = _tmp_workspace()
        with patch("core.shell.safety._platform_is_windows", return_value=is_windows):
            return ShellSafety.check(cmd=cmd, workspace_root=ws, tool_approval_mode="auto")

    def test_cmd_c_dir_s_b_is_allowed_on_windows(self):
        r = self._check(["cmd", "/c", "dir", "/s", "/b", "todo_probe"], is_windows=True)
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_ping_n_switch_is_allowed_on_windows(self):
        r = self._check(["ping", "/n", "1", "127.0.0.1"], is_windows=True)
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_robocopy_mir_switch_is_allowed_on_windows(self):
        r = self._check(["robocopy", "src", "dst", "/MIR"], is_windows=True)
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_the_same_switches_are_not_exempted_off_windows(self):
        # On this real host, /c resolves to C:\c -- not under any tempdir workspace -- so
        # without the Windows-only exemption it is (correctly) still flagged.
        r = self._check(["cmd", "/c", "dir"], is_windows=False)
        self.assertNotEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_the_blocking_remedy_text_is_gone_for_a_real_path_still_says_approve(self):
        # The misleading-for-switches remedy is fine for an ACTUAL outside-workspace path --
        # confirm it's untouched for that case.
        r = self._check(["cmd", "/c", "dir"], is_windows=False)
        if r.action == SafetyAction.BLOCK:
            self.assertIn("always_ask", r.reason)

    def test_ordinary_commands_with_no_switches_are_unaffected(self):
        for is_windows in (True, False):
            r = self._check(["python", "-m", "http.server", "8123"], is_windows=is_windows)
            self.assertEqual(r.action, SafetyAction.ALLOW, (is_windows, r.reason))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Fix 2 -- real Windows absolute paths are now caught
# ─────────────────────────────────────────────────────────────────────────────

class TestWindowsAbsolutePathsAreCaught(unittest.TestCase):

    def _check(self, cmd, ws=None):
        ws = ws or _tmp_workspace()
        return ShellSafety.check(cmd=cmd, workspace_root=ws, tool_approval_mode="auto"), ws

    def test_a_drive_letter_path_outside_the_workspace_is_blocked(self):
        r, ws = self._check(["type", "C:\\Users\\someone\\.ssh\\id_rsa"])
        self.assertEqual(r.action, SafetyAction.BLOCK, r.reason)
        self.assertIn("id_rsa", r.reason)

    def test_forward_slash_drive_paths_are_caught_too(self):
        r, ws = self._check(["type", "C:/Users/someone/.ssh/id_rsa"])
        self.assertEqual(r.action, SafetyAction.BLOCK)

    def test_c_windows_system_dir_stays_allowed(self):
        r, ws = self._check(["type", "C:\\Windows\\win.ini"])
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_c_windows_is_excluded_case_insensitively(self):
        r, ws = self._check(["type", "c:\\windows\\System32\\notepad.exe"])
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_a_lookalike_directory_that_only_starts_with_program_is_still_caught(self):
        # Must NOT be swept up by the Program Files exclusion just because it starts the same way.
        r, ws = self._check(["type", "C:\\Programmatic\\secret.txt"])
        self.assertEqual(r.action, SafetyAction.BLOCK, r.reason)

    def test_a_drive_letter_path_inside_the_workspace_is_allowed(self):
        ws = _tmp_workspace()
        inside = os.path.join(ws, "todo_probe", "index.html")
        r, _ = self._check(["type", inside], ws=ws)
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_approved_mode_warns_instead_of_blocking(self):
        ws = _tmp_workspace()
        r = ShellSafety.check(
            cmd=["type", "C:\\Users\\someone\\.ssh\\id_rsa"],
            workspace_root=ws, tool_approval_mode="always_ask",
        )
        self.assertEqual(r.action, SafetyAction.WARN, r.reason)

    def test_a_real_unc_path_to_an_unreachable_host_is_blocked_not_silently_allowed(self):
        # End-to-end, no mocking: this is the exact scenario that exposed fix 3 (Windows'
        # Path.resolve() hits the network for a UNC path and raises FileNotFoundError).
        ws = _tmp_workspace()
        r = ShellSafety.check(cmd=["type", "\\\\nosuchhost-s14-test\\share\\x"],
                              workspace_root=ws, tool_approval_mode="auto")
        self.assertEqual(r.action, SafetyAction.BLOCK, r.reason)

    def test_an_embedded_path_survives_even_if_the_outer_token_looks_like_a_switch(self):
        # /log:C:\... is classified as a keyed switch by fix 1 and skipped by the POSIX
        # scan -- but the Windows-path scan runs independently over the same command
        # string and still finds and checks the embedded C:\... value on its own.
        r, ws = self._check(["robocopy", "src", "dst", "/log:C:\\Users\\someone\\.ssh\\id_rsa"])
        self.assertEqual(r.action, SafetyAction.BLOCK, r.reason)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Fix 3 -- fail closed on ANY resolution failure, deterministically (no real network)
# ─────────────────────────────────────────────────────────────────────────────

class TestFailsClosedOnResolutionFailure(unittest.TestCase):

    def test_a_non_valueerror_resolution_failure_still_gets_flagged(self):
        """
        Proves fix 3 in isolation: simulate Path("<candidate>").resolve() raising an
        arbitrary OSError (standing in for the real UNC/WinError-53 case) and confirm the
        candidate is still treated as outside the workspace, not silently waved through.
        Deterministic and host/network-independent -- unlike the real UNC test above,
        this cannot be flaky.
        """
        ws = _tmp_workspace()
        trigger = "C:\\some\\path\\that\\will\\explode"
        real_path = Path

        def fake_path(s, *a, **kw):
            if str(s) == trigger:
                fake = MagicMock()
                fake.resolve.side_effect = OSError("simulated: e.g. an unreachable UNC host")
                return fake
            return real_path(s, *a, **kw)

        with patch("core.shell.safety.Path", side_effect=fake_path):
            r = ShellSafety.check(cmd=["type", trigger], workspace_root=ws, tool_approval_mode="auto")
        self.assertEqual(r.action, SafetyAction.BLOCK, r.reason)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Fix 2b -- common Windows software-install locations (which routinely contain a
#    space, e.g. "C:\Program Files\...") must not be blocked, exactly as /usr/bin and
#    /usr/local already aren't on POSIX. Found by this test suite itself: any command
#    built from sys.executable on a system-wide Python install was blocked outright.
# ─────────────────────────────────────────────────────────────────────────────

class TestCommonInstallLocationsStillWork(unittest.TestCase):

    def _check(self, cmd):
        ws = _tmp_workspace()
        return ShellSafety.check(cmd=cmd, workspace_root=ws, tool_approval_mode="auto")

    def test_program_files_with_a_space_is_allowed(self):
        r = self._check(["C:\\Program Files\\Python310\\python.exe", "-c", "print(1)"])
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_program_files_x86_is_allowed(self):
        r = self._check(["C:\\Program Files (x86)\\SomeApp\\tool.exe", "--version"])
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_programdata_is_allowed(self):
        r = self._check(["type", "C:\\ProgramData\\config.ini"])
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)

    def test_a_real_python_interpreter_invocation_is_allowed_exactly_as_produced_by_sys_executable(self):
        # The exact shape that broke: [sys.executable, "-c", ...], reproduced literally.
        # Only meaningful when the interpreter lives in a machine-wide location
        # (this fix allow-lists Program Files / ProgramData / Windows). A venv,
        # conda env or per-user install is a personal path that ShellSafety is
        # SUPPOSED to block, so asserting ALLOW there would test the wrong thing;
        # the literal "C:\\Program Files\\..." cases above cover the rule itself.
        import os as _os
        import sys as _sys
        machine_wide = [
            _os.environ.get(v, "").lower()
            for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemRoot")
        ]
        exe = _sys.executable.lower()
        if not any(root and exe.startswith(root) for root in machine_wide):
            self.skipTest(
                f"interpreter ({_sys.executable}) is not in a machine-wide install "
                "location; ShellSafety blocks personal paths by design"
            )
        r = self._check([_sys.executable, "-c", "import time; time.sleep(0)"])
        self.assertEqual(r.action, SafetyAction.ALLOW, r.reason)


if __name__ == "__main__":
    unittest.main()
