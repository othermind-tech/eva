# core/shell/safety.py
"""
ShellSafety — pre-execution command safety analysis for EVA.

Equivalent to Claude Code's BashTool security stack, adapted for EVA:

  commandSemantics.ts          → CommandClassifier._classify()
  destructiveCommandWarning.ts → _check_destructive_deny_list()
  pathValidation.ts            → _check_path_traversal()
  sedValidation.ts             → _check_sed_safety()
  shouldUseSandbox.ts          → (skipped — requires container infrastructure)
  Read-only mode enforcement   → _check_readonly_mode()
  Interactive input detection  → _check_interactive_tty()
  Fork bomb detection          → _check_fork_bomb()
  Network command flagging     → _check_network_commands()
  Background process detection → _check_background_process()

Usage
-----
    from core.shell.safety import ShellSafety, SafetyAction

    result = ShellSafety.check(
        cmd=["rm", "-rf", "/tmp/work"],
        workspace_root="/home/user/my-project",
        tool_approval_mode="auto",
        shell_readonly=False,
    )

    if result.action == SafetyAction.BLOCK:
        return error(result.reason)
    elif result.action == SafetyAction.WARN:
        # proceed, but surface result.warning to the caller

Design
------
Checks are ordered from most to least severe. Early returns on BLOCK so
expensive path resolution is only done when necessary.

Two tiers of blocking:
  ABSOLUTE_BLOCK  — command is always refused, regardless of tool_approval_mode
                    (fork bombs, catastrophic destructive patterns, TTY commands)
  APPROVAL_BLOCK  — command is refused when tool_approval_mode="auto";
                    allowed when tool_approval_mode="always_ask" (user already
                    approved the HitL gate) but surfaced as a warning
"""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Enums / dataclasses
# ---------------------------------------------------------------------------

class CommandClass(str, Enum):
    READ_ONLY   = "read_only"    # no filesystem or network mutation
    READ_WRITE  = "read_write"   # writes files, installs packages, git ops
    DESTRUCTIVE = "destructive"  # deletes, overwrites, formats, resets
    BLOCKED     = "blocked"      # always refused


class SafetyAction(str, Enum):
    ALLOW = "allow"   # safe to proceed
    WARN  = "warn"    # proceed, but include warning in result
    BLOCK = "block"   # refuse execution


@dataclass
class SafetyResult:
    action:         SafetyAction
    classification: CommandClass
    reason:         str = ""
    warning:        str = ""

    @property
    def blocked(self) -> bool:
        return self.action == SafetyAction.BLOCK


# ---------------------------------------------------------------------------
# Read-only command set
# ---------------------------------------------------------------------------

# Commands whose primary purpose is reading / inspecting, not writing.
# These are the only commands allowed when shell_readonly=True.
_READ_ONLY_CMDS: frozenset[str] = frozenset({
    # File inspection
    "cat", "head", "tail", "less", "more", "bat", "file", "stat",
    "wc", "diff", "cmp", "xxd", "hexdump", "strings",
    # Directory listing
    "ls", "dir", "tree", "find", "locate", "fd",
    # Search
    "grep", "rg", "awk", "sed",   # sed without -i is read-only
    "sort", "uniq", "cut", "tr", "jq", "yq",
    # Text output
    "echo", "printf", "tee",      # tee writes, flagged separately
    "pwd", "which", "whereis", "type", "command",
    # System info
    "ps", "top", "htop", "df", "du", "free", "uptime",
    "uname", "hostname", "id", "whoami", "env", "printenv",
    # Git read operations
    "git",   # git itself is read-only only for safe subcommands — see below
    # Archiving (read-only mode: extraction only)
    "tar", "unzip", "zip",
    # Misc
    "date", "cal", "bc", "python3", "python", "node",  # scripts can write — see classify
})

# Git subcommands that are read-only
_GIT_READ_ONLY_SUBCMDS: frozenset[str] = frozenset({
    "status", "log", "diff", "show", "branch", "tag",
    "remote", "fetch", "ls-files", "ls-tree", "rev-parse",
    "describe", "shortlog", "stash list", "config --list",
    "config --get",
})

# Commands that write / modify state
_READ_WRITE_CMDS: frozenset[str] = frozenset({
    "cp", "mv", "mkdir", "touch", "ln", "chmod", "chown", "chgrp",
    "install", "pip", "pip3", "npm", "yarn", "pnpm", "cargo",
    "apt", "apt-get", "yum", "dnf", "brew", "apk", "snap",
    "make", "cmake", "ninja", "gradle", "mvn",
    "git",   # mutating subcommands handled in classify
    "tee", "truncate",
    "sed",   # sed -i is read_write/destructive — handled in sed check
    "patch", "rsync", "scp",
})

# Commands that delete / destroy / overwrite irreversibly
_DESTRUCTIVE_CMDS: frozenset[str] = frozenset({
    "rm", "del", "rmdir", "unlink",
    "dd", "mkfs", "mke2fs", "mkswap", "parted", "fdisk", "gdisk",
    "shred", "wipe", "wipefs", "srm",
    "format",   # Windows
    "git",      # git reset --hard, git clean -f handled in classify
    "kill", "killall", "pkill",
})


# ---------------------------------------------------------------------------
# Fork bomb patterns
# ---------------------------------------------------------------------------

_FORK_BOMB_PATTERNS: list[re.Pattern] = [
    re.compile(r":\(\)\s*\{.*\|.*&.*\}", re.DOTALL),   # bash fork bomb
    re.compile(r":()\s*\{.*\|.*&.*\}", re.DOTALL),
    re.compile(r"while\s+true.*do.*&", re.DOTALL | re.IGNORECASE),
    re.compile(r"fork\s*\(\s*\)"),                       # C fork() in shell
]


# ---------------------------------------------------------------------------
# Catastrophic destructive deny-list  (ABSOLUTE_BLOCK — always refused)
# ---------------------------------------------------------------------------

@dataclass
class _DenyEntry:
    pattern:     re.Pattern
    reason:      str

_ABSOLUTE_DENY_LIST: list[_DenyEntry] = [
    # Recursive deletion of filesystem root
    _DenyEntry(
        re.compile(r"\brm\b.*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r).*\s/[\s\"']?$", re.IGNORECASE),
        "rm -rf / targets the filesystem root — refused unconditionally",
    ),
    _DenyEntry(
        re.compile(r"\brm\b.*--no-preserve-root", re.IGNORECASE),
        "rm --no-preserve-root refused unconditionally",
    ),
    # Raw disk write
    _DenyEntry(
        re.compile(r"\bdd\b.*of=/dev/(sd|hd|nvme|vd|xvd|sda|sdb|sdc)", re.IGNORECASE),
        "dd writing to a raw block device refused unconditionally",
    ),
    _DenyEntry(
        re.compile(r"\bdd\b.*if=/dev/zero.*of=/dev/", re.IGNORECASE),
        "dd zeroing a block device refused unconditionally",
    ),
    # Filesystem format
    _DenyEntry(
        re.compile(r"\b(mkfs|mke2fs|mkswap|mkdosfs|mkntfs)\b", re.IGNORECASE),
        "filesystem format commands refused unconditionally",
    ),
    _DenyEntry(
        re.compile(r"\b(parted|fdisk|gdisk)\b.*\s(--script|-s)\b", re.IGNORECASE),
        "non-interactive partition editing refused unconditionally",
    ),
    # Disk shred / wipe
    _DenyEntry(
        re.compile(r"\b(shred|wipe|wipefs|srm)\b.*(/dev/|--all-devices)", re.IGNORECASE),
        "disk wipe commands targeting block devices refused unconditionally",
    ),
    # Redirect to /dev/sda or similar
    _DenyEntry(
        re.compile(r">\s*/dev/(sd|hd|nvme|vd)", re.IGNORECASE),
        "redirect to raw block device refused unconditionally",
    ),
    # chmod / chown on root
    _DenyEntry(
        re.compile(r"\bchmod\b.*-R\b.*777.*\s/[\s\"']?$", re.IGNORECASE),
        "chmod -R 777 / refused unconditionally",
    ),
    _DenyEntry(
        re.compile(r"\bchown\b.*-R\b.*root\s*/[\s\"']?$", re.IGNORECASE),
        "chown -R root / refused unconditionally",
    ),
    # Redirect to critical system paths
    _DenyEntry(
        re.compile(r">\s*/(etc|boot|sys|proc)/(passwd|shadow|sudoers|fstab|grub)", re.IGNORECASE),
        "redirect to critical system file refused unconditionally",
    ),
]


# ---------------------------------------------------------------------------
# Interactive TTY commands  (ABSOLUTE_BLOCK — they hang with no TTY)
# ---------------------------------------------------------------------------

_INTERACTIVE_CMDS: frozenset[str] = frozenset({
    # Editors
    "vim", "vi", "nano", "emacs", "pico", "joe", "micro",
    "code",   # VS Code (opens GUI)
    # Interactive shells / REPLs with no args
    # (python with -c or script.py is fine — checked via arg count)
    "bash", "sh", "zsh", "fish", "ksh", "tcsh", "csh",
    "ipython", "bpython",
    # Interactive DB clients (no args)
    "mysql", "psql", "sqlite3", "mongo", "mongosh", "redis-cli",
    # Interactive monitors
    "top", "htop", "btop", "atop", "iotop",
    "less", "more",   # interactive pagers
    # SSH (interactive without -c command)
    "ssh",
    # Other TTY-dependent tools
    "screen", "tmux",
})

# These commands are only interactive when called with no positional arguments
_INTERACTIVE_WHEN_NO_ARGS: frozenset[str] = frozenset({
    "python", "python3", "python2",
    "node", "nodejs",
    "irb", "pry",   # Ruby
    "ghci",         # Haskell
    "erl",          # Erlang
    "lua",
    "julia",
    "bash", "sh", "zsh", "fish",
    "mysql", "psql", "sqlite3", "mongo", "mongosh",
})


# ---------------------------------------------------------------------------
# Network commands  (APPROVAL_BLOCK when tool_approval_mode=auto)
# ---------------------------------------------------------------------------

_NETWORK_CMDS: frozenset[str] = frozenset({
    "curl", "wget", "httpie", "http",
    "nc", "netcat", "ncat",
    "ssh", "scp", "sftp", "ftp",
    "rsync",   # rsync over SSH is network; local rsync is read_write
    "ping", "traceroute", "tracert", "nslookup", "dig", "host",
    "telnet",
})

# These network commands are always read-only and lower risk
_NETWORK_READ_ONLY: frozenset[str] = frozenset({
    "ping", "traceroute", "tracert", "nslookup", "dig", "host",
})


# ---------------------------------------------------------------------------
# ShellSafety — main class
# ---------------------------------------------------------------------------

def _platform_is_windows() -> bool:
    """Wrapped so tests can patch this one function instead of the global platform.system()."""
    return platform.system() == "Windows"


# Matches /path/... but not /dev/null, /usr/bin/*, /lib/*, /proc/ (S-14: see
# ShellSafety._check_path_traversal's docstring for the two Windows fixes layered on top).
_POSIX_PATH_PATTERN = re.compile(
    r"""(?<!\w)(/(?!(?:dev/null|dev/random|dev/urandom|proc/|sys/|usr/(?:bin|lib|local|share)|bin/|lib[0-9]?/|etc/(?:hosts|resolv|localtime|timezone|hostname|timezone)[^/])\b)[^\s"']+)"""
)

# Windows absolute paths: a drive letter (C:\..., C:/...) or a UNC share (\\server\share\...).
# Case-insensitive (Windows filesystem paths are). C:\Windows\, C:\Program Files\ (and the (x86)
# variant) and C:\ProgramData\ are excluded the same way the POSIX pattern excludes /usr/bin,
# /bin, /lib -- public, OS/software-wide locations, not personal ones, so a reference into them
# is not a sandbox escape worth blocking. (S-14 fix 2 -- these never matched the POSIX-only
# pattern above; fix 2b, found by this test suite -- see TestCommonInstallLocationsStillWork --
# any command line built from sys.executable on a system-wide Python install, e.g.
# `[sys.executable, "-c", ...]`, was blocked, because "C:\Program Files\..." has a space and the
# tokenizer below (`[^\s"']*`) stops at it. A negative LOOKAHEAD checks past that stopping point
# in the full string -- confirmed empirically -- so `Program(?:[\\/ ]|$)` excludes the match
# entirely whether or not the eventual captured token gets space-truncated to bare "...Program";
# it does NOT exclude a real, different directory that merely starts the same way, e.g.
# C:\Programmatic\..., because that lookahead alternative requires "Program" to be followed by a
# separator, a space or the end of the string -- "Programmatic" fails all three.
_WINDOWS_PATH_PATTERN = re.compile(
    r"""(?<!\w)([A-Za-z]:[\\/](?!Windows[\\/]|Program(?:[\\/ ]|$)|ProgramData[\\/])[^\s"']*|\\\\[^\s"']+)""",
    re.IGNORECASE,
)

# A single `/token` or `/token:value` with no further `/` in `token` -- e.g. /c, /s, /b, /n,
# /MIR, /max:5 -- is how Windows CLI tools (cmd, dir, xcopy, robocopy, ping, tracert, ...) write
# options. It is never a real filesystem path there (Windows paths use `C:\` or `\\`, not a bare
# leading `/`), so on Windows it must not be treated as one. On POSIX this heuristic is not
# applied at all: POSIX tools use `-`/`--` for options, so a bare `/token` there is far more
# likely to be a genuine short absolute path (e.g. `/tmp`), and no legitimate POSIX command line
# produces `/c`-style single-letter switches to confuse it with.
_WINDOWS_SWITCH_PATTERN = re.compile(r"""^/([A-Za-z][A-Za-z0-9]{0,15})(?::[^\s"'/]*)?$""")


def _is_windows_cli_switch(token: str) -> bool:
    """True if `token` (already matched as a `/`-leading path candidate) looks like a Windows
    CLI option rather than a path -- see _WINDOWS_SWITCH_PATTERN above for the exact shape."""
    return bool(_WINDOWS_SWITCH_PATTERN.match(token))


class ShellSafety:
    """
    Static safety checker for shell commands.

    All public methods are class/static methods — no instantiation needed.
    """

    @classmethod
    def check(
        cls,
        cmd: List[str],
        workspace_root: str,
        tool_approval_mode: str = "auto",
        shell_readonly: bool = False,
    ) -> SafetyResult:
        """
        Analyse a command and return a SafetyResult.

        Parameters
        ----------
        cmd               : Command as a list of strings (same as subprocess.run).
        workspace_root    : Absolute path of the active workspace boundary.
                            Commands that resolve paths outside this root require
                            explicit approval.  Should be project_workspace when
                            a project is active, else ~/eva_workspace/{user_id}/.
        tool_approval_mode: "auto" (default) | "always_ask" | "per_conversation".
                            When "always_ask", the HitL gate has already fired and
                            the user approved — approval-level blocks become warns.
        shell_readonly    : If True, all non-read-only commands are blocked.
        """
        if not cmd:
            return SafetyResult(SafetyAction.ALLOW, CommandClass.READ_ONLY)

        cmd_str = " ".join(str(c) for c in cmd)
        binary  = Path(cmd[0]).name.lower()   # strip path prefix (e.g. /usr/bin/rm → rm)
        args    = cmd[1:]

        user_approved = tool_approval_mode in ("always_ask", "per_conversation")

        # ── 1. Fork bomb (ABSOLUTE BLOCK) ───────────────────────────────────
        result = cls._check_fork_bomb(cmd_str)
        if result:
            return result

        # ── 2. Interactive TTY commands (ABSOLUTE BLOCK) ────────────────────
        result = cls._check_interactive_tty(binary, args)
        if result:
            return result

        # ── 3. Catastrophic deny-list (ABSOLUTE BLOCK) ──────────────────────
        result = cls._check_destructive_deny_list(cmd_str)
        if result:
            return result

        # ── 4. Classify command ──────────────────────────────────────────────
        classification = cls._classify(binary, args)

        # ── 5. Read-only mode enforcement ───────────────────────────────────
        if shell_readonly and classification != CommandClass.READ_ONLY:
            return SafetyResult(
                SafetyAction.BLOCK,
                classification,
                reason=(
                    f"Shell is in read-only mode. "
                    f"'{binary}' is classified as '{classification.value}' and is not allowed. "
                    "Set shell_readonly=false in your session to run mutating commands."
                ),
            )

        # ── 6. sed -i safety ────────────────────────────────────────────────
        if binary == "sed":
            result = cls._check_sed_safety(args, workspace_root, user_approved)
            if result:
                return result

        # ── 7. Path traversal ────────────────────────────────────────────────
        result = cls._check_path_traversal(cmd_str, workspace_root, user_approved)
        if result:
            return result

        # ── 8. Network commands ──────────────────────────────────────────────
        result = cls._check_network_commands(binary, cmd_str, user_approved)
        if result:
            return result

        # ── 9. Background process detection ─────────────────────────────────
        result = cls._check_background_process(cmd_str, binary, args)
        if result:
            return result

        # ── All checks passed ────────────────────────────────────────────────
        return SafetyResult(SafetyAction.ALLOW, classification)

    # ------------------------------------------------------------------
    # Check implementations
    # ------------------------------------------------------------------

    @staticmethod
    def _check_fork_bomb(cmd_str: str) -> Optional[SafetyResult]:
        for pattern in _FORK_BOMB_PATTERNS:
            if pattern.search(cmd_str):
                return SafetyResult(
                    SafetyAction.BLOCK,
                    CommandClass.BLOCKED,
                    reason="Fork bomb pattern detected — refused unconditionally.",
                )
        return None

    @staticmethod
    def _check_interactive_tty(binary: str, args: List[str]) -> Optional[SafetyResult]:
        # Commands that are always interactive (no-arg check doesn't apply)
        always_interactive = _INTERACTIVE_CMDS - _INTERACTIVE_WHEN_NO_ARGS
        if binary in always_interactive:
            # ssh is allowed when given a command (-c equivalent: ssh host cmd)
            if binary == "ssh" and len(args) >= 2:
                # ssh user@host command — non-interactive
                # Simple heuristic: if args contain a non-option after host, allow
                non_option_args = [a for a in args if not a.startswith("-")]
                if len(non_option_args) >= 2:
                    return None   # has both host and command
            return SafetyResult(
                SafetyAction.BLOCK,
                CommandClass.BLOCKED,
                reason=(
                    f"'{binary}' requires an interactive terminal (TTY) and cannot run "
                    "inside EVA's shell tool. Use a non-interactive form (e.g. "
                    f"'{binary} --help', or pass a specific command/script as an argument)."
                ),
            )

        # Commands interactive only when invoked with no positional arguments
        if binary in _INTERACTIVE_WHEN_NO_ARGS:
            positional = [a for a in args if not a.startswith("-")]
            if not positional:
                return SafetyResult(
                    SafetyAction.BLOCK,
                    CommandClass.BLOCKED,
                    reason=(
                        f"'{binary}' with no arguments opens an interactive REPL — it will "
                        "hang with no TTY. Pass a script, -c 'code', or a filename."
                    ),
                )
        return None

    @staticmethod
    def _check_destructive_deny_list(cmd_str: str) -> Optional[SafetyResult]:
        for entry in _ABSOLUTE_DENY_LIST:
            if entry.pattern.search(cmd_str):
                return SafetyResult(
                    SafetyAction.BLOCK,
                    CommandClass.BLOCKED,
                    reason=entry.reason,
                )
        return None

    @staticmethod
    def _classify(binary: str, args: List[str]) -> CommandClass:
        """Classify command as read_only, read_write, or destructive."""
        # git subcommand classification
        if binary == "git" and args:
            subcmd = args[0].lower()
            if subcmd in _GIT_READ_ONLY_SUBCMDS:
                return CommandClass.READ_ONLY
            if subcmd in ("reset", "clean", "checkout", "restore"):
                # destructive variants
                arg_str = " ".join(args)
                if "--hard" in arg_str or "-f" in arg_str or "--force" in arg_str:
                    return CommandClass.DESTRUCTIVE
            if subcmd in ("push", "rebase", "merge", "commit", "add", "pull",
                          "clone", "init", "checkout", "switch", "stash",
                          "tag", "submodule", "worktree"):
                return CommandClass.READ_WRITE
            return CommandClass.READ_WRITE

        if binary in _DESTRUCTIVE_CMDS:
            return CommandClass.DESTRUCTIVE

        if binary in _READ_WRITE_CMDS:
            # sed without -i is read-only
            if binary == "sed" and not any(a.startswith("-i") or a == "--in-place" for a in args):
                return CommandClass.READ_ONLY
            return CommandClass.READ_WRITE

        if binary in _READ_ONLY_CMDS:
            return CommandClass.READ_ONLY

        # Unknown commands: conservative classification as read_write
        return CommandClass.READ_WRITE

    @staticmethod
    def _check_sed_safety(
        args: List[str],
        workspace_root: str,
        user_approved: bool,
    ) -> Optional[SafetyResult]:
        """Validate sed -i (in-place edit) patterns."""
        in_place = any(a.startswith("-i") or a == "--in-place" for a in args)
        if not in_place:
            return None

        # Check if any file args point outside workspace
        ws = Path(workspace_root).resolve()
        for arg in args:
            if arg.startswith("/") or arg.startswith("~"):
                target = Path(arg).expanduser().resolve()
                try:
                    target.relative_to(ws)
                except ValueError:
                    if user_approved:
                        return SafetyResult(
                            SafetyAction.WARN,
                            CommandClass.READ_WRITE,
                            warning=(
                                f"[SAFETY WARNING] sed -i targeting path outside workspace: {arg}. "
                                "Proceed with caution."
                            ),
                        )
                    return SafetyResult(
                        SafetyAction.BLOCK,
                        CommandClass.READ_WRITE,
                        reason=(
                            f"sed -i targeting '{arg}' which is outside the active workspace "
                            f"({workspace_root}). Enable always_ask approval mode to allow."
                        ),
                    )
        return None

    @staticmethod
    def _check_path_traversal(
        cmd_str: str,
        workspace_root: str,
        user_approved: bool,
    ) -> Optional[SafetyResult]:
        """
        Detect absolute paths in the command that escape the workspace boundary.

        Heuristic: extract tokens that look like absolute paths, resolve them,
        and check they are under workspace_root.

        Skips /usr/, /bin/, /lib/, /etc/hostname (POSIX) and C:\\Windows\\ (Windows)
        — legitimate binary locations or innocuous read targets.

        S-14 (2026-09-21/22, found live): the POSIX-only pattern below had two
        failures on Windows, EVA's primary platform.
          1. FALSE POSITIVE: `/`-prefixed CLI switches (`cmd /c`, `dir /s /b`,
             `ping /n 1`, `robocopy /MIR`) matched the "looks like an absolute
             path" pattern and were blocked as "outside the workspace" — with a
             remedy ("approve the command") that makes no sense for a token that
             was never a path. Live, an agent lost ~10 calls to this hunting for
             a folder. Fixed by _is_windows_cli_switch(): on Windows only, a
             short single-segment `/token[:value]` with no further `/` is
             treated as an option flag, not a path candidate, and skipped.
          2. FALSE NEGATIVE: the pattern anchors on a literal `/`, so a genuine
             Windows absolute path (`C:\\Windows\\win.ini`,
             `C:\\Users\\<user>\\.ssh\\id_rsa`) never matched at all -- the one
             check meant to contain the agent inside its sandbox provided zero
             containment on Windows. Fixed by also matching drive-letter
             (`X:\\...` / `X:/...`) and UNC (`\\\\server\\share\\...`) paths and
             running them through the same workspace-containment test.
          3. FAIL-OPEN ON RESOLUTION FAILURE (found testing fix 2): a candidate
             whose Path.resolve() raised anything other than ValueError -- e.g.
             a UNC path to an unreachable host raises FileNotFoundError, not
             ValueError, because resolve() hits the network for UNC paths --
             was silently treated as safe. Now any resolution failure is
             treated as "outside the workspace" (fail closed), the same as a
             ValueError from relative_to().
        All three apply the SAME allow/warn/block logic already used for the
        original POSIX pattern; nothing about that logic changed.
        """
        ws = Path(workspace_root).resolve()
        is_windows = _platform_is_windows()

        suspicious: List[str] = []
        seen: set = set()

        def _add(raw: str) -> None:
            if raw in seen:
                return
            try:
                resolved = Path(raw).resolve()
                resolved.relative_to(ws)
            except Exception:
                # Anything that stops us proving `raw` resolves to somewhere INSIDE the
                # workspace -- it doesn't (ValueError from relative_to), or resolution itself
                # failed for any other reason -- is treated as outside it. Fail closed: a path
                # this check cannot vouch for is not passed through silently.
                #
                # S-14 fix 3 (found while testing fix 2): the old code only did this for
                # ValueError and silently ALLOWED anything else through -- fail-OPEN in a
                # security check. That let a real attack path slip past fix 2's own detection:
                # Path.resolve() on an unreachable UNC path (\\server\share\...) hits the
                # network and raises FileNotFoundError ("[WinError 53] The network path was
                # not found"), which landed in the old bare `except Exception: pass` and was
                # silently waved through.
                seen.add(raw)
                suspicious.append(raw)

        # ── POSIX-style candidates: /path/... ───────────────────────────────
        for match in _POSIX_PATH_PATTERN.finditer(cmd_str):
            raw = match.group(1)
            if is_windows and _is_windows_cli_switch(raw):
                continue   # S-14 fix 1: a `/c`, `/s`, `/MIR`-style switch, not a path
            _add(raw)

        # ── Windows-style candidates: C:\... / C:/... / \\server\share\... ──
        for match in _WINDOWS_PATH_PATTERN.finditer(cmd_str):
            _add(match.group(1))   # S-14 fix 2: these never matched before at all

        if not suspicious:
            return None

        paths_str = ", ".join(suspicious[:3])
        if user_approved:
            return SafetyResult(
                SafetyAction.WARN,
                CommandClass.READ_WRITE,
                warning=(
                    f"[SAFETY WARNING] Command references path(s) outside the active workspace "
                    f"({workspace_root}): {paths_str}. Approved by user."
                ),
            )
        return SafetyResult(
            SafetyAction.BLOCK,
            CommandClass.READ_WRITE,
            reason=(
                f"Command references path(s) outside the active workspace "
                f"({workspace_root}): {paths_str}. "
                "Set tool_approval_mode=always_ask and approve the command to proceed."
            ),
        )

    @staticmethod
    def _check_network_commands(
        binary: str,
        cmd_str: str,
        user_approved: bool,
    ) -> Optional[SafetyResult]:
        """Flag outbound network commands."""
        if binary not in _NETWORK_CMDS:
            return None

        if binary in _NETWORK_READ_ONLY:
            # Diagnostic-only network commands — allow, just classify
            return None

        # rsync to/from local path only (no : in destination)
        if binary == "rsync":
            if ":" not in cmd_str:
                return None   # local rsync — no network

        if user_approved:
            return SafetyResult(
                SafetyAction.WARN,
                CommandClass.READ_WRITE,
                warning=(
                    f"[SAFETY WARNING] '{binary}' makes outbound network connections. "
                    "Approved by user."
                ),
            )
        return SafetyResult(
            SafetyAction.BLOCK,
            CommandClass.READ_WRITE,
            reason=(
                f"'{binary}' makes outbound network connections and requires explicit approval. "
                "Set tool_approval_mode=always_ask and approve the command to allow network access."
            ),
        )

    @staticmethod
    def _check_background_process(
        cmd_str: str,
        binary: str,
        args: List[str],
    ) -> Optional[SafetyResult]:
        """
        Detect & (background process) usage in bash -c scripts.

        Background processes started via the shell tool are detached from EVA's
        tracking — their lifetime, output, and exit code are invisible. For
        long-running background tasks, trigger_daemon() should be used instead.
        """
        is_bash_c = binary in ("bash", "sh", "/bin/bash", "/bin/sh") and len(args) >= 2 and args[0] == "-c"
        if not is_bash_c:
            return None

        script = " ".join(args[1:])
        bg_pattern = re.compile(r"&\s*$|&\s*>|nohup\s+", re.MULTILINE)
        if bg_pattern.search(script):
            return SafetyResult(
                SafetyAction.WARN,
                CommandClass.READ_WRITE,
                warning=(
                    "[SAFETY WARNING] Command starts a background process (&). "
                    "Background processes are detached from EVA's tracking — their output "
                    "and exit code will not be visible. "
                    "For long-running tasks, use trigger_daemon() instead."
                ),
            )
        return None

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @classmethod
    def classify(cls, cmd: List[str]) -> CommandClass:
        """Classify a command without running any block checks. Useful for display."""
        if not cmd:
            return CommandClass.READ_ONLY
        binary = Path(cmd[0]).name.lower()
        return cls._classify(binary, cmd[1:])
