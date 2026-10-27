# core/execution/step_record.py
"""
What a plan step is told about the steps before it, what the verifier is told about the tools a
step ran, and (A-16) what the verifier is ALSO told about the steps before it -- the same
prior-steps block the step itself received, reused rather than duplicated -- so a step that made
no tool calls because an earlier step already did the work isn't judged as if nothing happened
(gaps A-13, A-14, A-16).

The problem this solves
-----------------------
In plan/execute mode every step used to be given only the original request plus its own step text.
Nothing from earlier steps reached later ones, so a later step redid work an earlier step had done,
and the closing "summarise what you did" step had nothing to summarise and invented details
(live, 2026-09-21: an answer blaming a `consoel.log` typo that did not exist). The verifier had the
same blindness in the other direction: it judged a step from its narration alone, so a step could
claim "file edited" without any edit having run.

What is recorded -- deliberately small
--------------------------------------
* per tool call, ONE line: the tool, what it acted on, and ok / ERROR (with the first characters of
  the error) -- never full arguments and never results (page snapshots, file contents and edit
  bodies are large and are not what a later step or a verifier needs);
* the step's own text, trimmed;
* the verifier's verdict for the step.

Pure functions and small classes only: no I/O, no LLM calls, nothing here can fail a run. Everything
is size-capped by the PEV_* settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config.settings import (
    PEV_PRIOR_STEPS_MAX_CHARS,
    PEV_STEP_RECORD_ERROR_CHARS,
    PEV_STEP_RECORD_MAX_TOOL_LINES,
    PEV_STEP_RECORD_TARGET_CHARS,
    PEV_STEP_RECORD_TEXT_CHARS,
)

# Bookkeeping calls that say nothing about the work itself.
_META_TOOLS = frozenset({"describe_tool"})

# Which argument says "what this call acted on", in order of preference. Anything else falls back
# to the first short string/number argument.
_TARGET_KEYS = ("path", "file_path", "url", "command", "cmd", "process_id", "query", "pattern", "name", "cwd")

# A line shorter than this is what an older step is cut down to when the block is over budget.
_ONE_LINE_TEXT_CHARS = 160


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())  # collapse whitespace/newlines to one line
    return text if len(text) <= limit else text[: max(limit - 1, 1)].rstrip() + "…"


def _target_of(arguments: Any) -> str:
    """One short phrase for what a call acted on, e.g. 'start python -m http.server 8123'."""
    if not isinstance(arguments, dict):
        return ""
    parts: List[str] = []
    action = arguments.get("action")
    if isinstance(action, str) and action:
        parts.append(action)
    chosen: Optional[str] = None
    for key in _TARGET_KEYS:
        value = arguments.get(key)
        if value in (None, "", [], {}):
            continue
        chosen = " ".join(str(v) for v in value) if isinstance(value, list) else str(value)
        break
    if chosen is None:
        for key, value in arguments.items():
            if key == "action":
                continue
            if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value):
                chosen = str(value)
                break
    if chosen:
        parts.append(chosen)
    return _clip(" ".join(parts), PEV_STEP_RECORD_TARGET_CHARS)


def _error_message(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("message", "error", "detail"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key]
        return str(value)
    return str(value)


def outcome_of(payload: Any) -> str:
    """
    'ok', 'ERROR: <start of the message>' or 'exit <code>' for one tool result payload.

    Tool results reach us in a few shapes: the executor's own wrapper
    ({"ok": bool, "result": ..., "error": ...}), tools that report failure inside a successful
    wrapper ({"ok": true, "result": {"error": "..."}}), shell results with an exit code, and MCP
    results that are a list of text blocks ("### Error ..." for a rejected call).
    """
    if not isinstance(payload, dict):
        return "ok"
    limit = PEV_STEP_RECORD_ERROR_CHARS
    if payload.get("ok") is False or payload.get("error"):
        return "ERROR: " + _clip(_error_message(payload.get("error", "call failed")), limit)
    inner = payload.get("result", payload)
    if isinstance(inner, dict):
        if inner.get("error"):
            return "ERROR: " + _clip(_error_message(inner["error"]), limit)
        if inner.get("blocked"):
            return "ERROR: blocked"
        code = inner.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return f"exit {code}"
    elif isinstance(inner, list):
        for block in inner:
            if isinstance(block, str) and block.lstrip().startswith("### Error"):
                return "ERROR: " + _clip(block.lstrip()[len("### Error"):], limit)
    return "ok"


class StepToolLedger:
    """Collects the tool calls of ONE step from the chunks streaming through the runner."""

    def __init__(self) -> None:
        self._order: List[str] = []
        self._calls: Dict[str, Dict[str, Any]] = {}

    def add_call(self, tool_name: str, arguments: Any, call_id: str) -> None:
        if tool_name in _META_TOOLS:
            return
        key = call_id or f"anon-{len(self._order)}"
        if key not in self._calls:
            self._order.append(key)
        self._calls[key] = {"tool": tool_name, "target": _target_of(arguments), "outcome": None}

    def add_result(self, tool_name: str, payload: Any, call_id: str) -> None:
        if tool_name in _META_TOOLS:
            return
        key = call_id or ""
        entry = self._calls.get(key)
        if entry is None:  # a result whose call chunk we never saw
            key = key or f"anon-{len(self._order)}"
            entry = self._calls[key] = {"tool": tool_name, "target": "", "outcome": None}
            self._order.append(key)
        entry["outcome"] = outcome_of(payload)

    def _lines(self) -> List[str]:
        raw = []
        for key in self._order:
            call = self._calls[key]
            target = f" {call['target']}" if call["target"] else ""
            raw.append(f"{call['tool']}{target} -> {call['outcome'] or 'no result (interrupted)'}")
        # Collapse consecutive repeats: "x -> ok" three times becomes "x -> ok (x3)".
        lines: List[str] = []
        repeats = 0
        for line in raw:
            if lines and lines[-1] == line:
                repeats += 1
                continue
            if repeats:
                lines[-1] = f"{lines[-1]} (x{repeats + 1})"
                repeats = 0
            lines.append(line)
        if repeats:
            lines[-1] = f"{lines[-1]} (x{repeats + 1})"
        return lines

    @property
    def call_count(self) -> int:
        return len(self._order)

    def lines(self) -> List[str]:
        """The most recent PEV_STEP_RECORD_MAX_TOOL_LINES lines, plus a marker if older ones were cut."""
        lines = self._lines()
        cap = PEV_STEP_RECORD_MAX_TOOL_LINES
        if len(lines) <= cap:
            return lines
        return [f"({len(lines) - cap} earlier tool calls not shown)"] + lines[-cap:]

    def render(self) -> str:
        """The block the verifier sees: one line per call, or a statement that none ran."""
        lines = self.lines()
        if not lines:
            return "(no tools were called in this step)"
        return "\n".join(f"- {line}" for line in lines)


@dataclass
class StepRecord:
    """What is carried forward about one finished step."""
    number: int            # 1-based position in the plan when it ran
    title: str
    verdict: str           # "passed" | "failed" | "unverified"
    text: str = ""
    tool_lines: List[str] = field(default_factory=list)
    issues: Optional[str] = None


def make_record(step: Dict[str, Any], *, output_text: str, ledger: StepToolLedger,
                verdict: str, issues: Optional[str] = None, number: Optional[int] = None) -> StepRecord:
    """number: the step's 1-based position in the list as it ran. Pass it -- after a replan the
    steps' own "index" field can repeat (the retry of step 1 is also index 0)."""
    return StepRecord(
        number=number if number is not None else int(step.get("index", 0)) + 1,
        title=str(step.get("title", "")),
        verdict=verdict,
        text=_clip(output_text or "", PEV_STEP_RECORD_TEXT_CHARS),
        tool_lines=ledger.lines(),
        issues=_clip(issues, PEV_STEP_RECORD_ERROR_CHARS * 2) if issues else None,
    )


def _render_full(rec: StepRecord) -> str:
    verdict = {"passed": "verified", "failed": "FAILED verification", "unverified": "not verified"}.get(rec.verdict, rec.verdict)
    out = [f"Step {rec.number} — {rec.title} [{verdict}]"]
    if rec.tool_lines:
        out.append("  Tools it ran:")
        out.extend(f"    - {line}" for line in rec.tool_lines)
    else:
        out.append("  Tools it ran: none")
    if rec.issues:
        out.append(f"  Verifier found: {rec.issues}")
    out.append(f"  What it reported: {rec.text or '(no text)'}")
    return "\n".join(out)


def _render_short(rec: StepRecord) -> str:
    verdict = {"passed": "verified", "failed": "FAILED verification", "unverified": "not verified"}.get(rec.verdict, rec.verdict)
    return f"Step {rec.number} — {rec.title} [{verdict}]: {_clip(rec.text or '(no text)', _ONE_LINE_TEXT_CHARS)}"


# Upper bound on tool names listed in the "already active" line (names are ~5 tokens each).
_MAX_ACTIVE_TOOL_NAMES = 40


def active_tool_names(registry: Any) -> set:
    """
    Names of the tools currently in the model's tool list, excluding the describe_tool gateway.

    Empty when the registry is not progressive (every tool is active from the start, so there is
    nothing to say) or when it cannot be read -- this feeds a prompt hint, so it must never raise.
    """
    try:
        if registry is None or not getattr(registry, "_is_progressive", False):
            return set()
        return {str(n) for n in registry.get_active_tool_classes() if n != "describe_tool"}
    except Exception:
        return set()


def build_active_tools_line(current: set, baseline: set) -> str:
    """
    One short paragraph naming the tools THIS RUN has activated since it started ('' if none).

    Each plan step starts with a fresh context, so a step has no memory of the describe_tool calls
    earlier steps made -- yet the activated tools are still in its tool list (promotion lasts for the
    whole execution). Live 2026-09-21 (`gpt-oss:120b`): 17 of 23 describe_tool calls in one run
    re-described tools that were already active, ~10 s of model time each. Only the run's OWN
    additions are named: tools that were active from the start (the session's configured tools, the
    plan/execute orchestration tools) are already known to the model and would only add noise.
    Names only -- no schemas -- so this does not change what is promoted or the prompt's real size.
    """
    new = sorted(set(current) - set(baseline))
    if not new:
        return ""
    shown = new[:_MAX_ACTIVE_TOOL_NAMES]
    more = f" (and {len(new) - len(shown)} more)" if len(new) > len(shown) else ""
    return (
        "## Tools already active in this run\n"
        "You have already described these tools, so they are active and in your tool list: "
        + ", ".join(shown) + more + ". Call them directly -- do not call describe_tool for them "
        "again, it only wastes a turn. Any other tool from the manifest still needs describe_tool first."
    )


_HEADER = (
    "## Work already done in earlier steps of this plan\n"
    "This is what the earlier steps actually did. Build on it -- do not repeat work that succeeded, "
    "and do not assume a step did anything this record does not show."
)


def build_prior_steps_block(records: List[StepRecord], max_chars: Optional[int] = None) -> str:
    """
    The block added to a step's prompt, or '' for the first step.

    Kept within max_chars: when the plan is long, the OLDEST steps are cut to one line first, and
    if that is still too much the oldest are dropped with a note -- the newest steps stay complete.
    """
    if not records:
        return ""
    budget = PEV_PRIOR_STEPS_MAX_CHARS if max_chars is None else max_chars
    rendered = [_render_full(r) for r in records]

    def total() -> int:
        return len(_HEADER) + sum(len(x) + 2 for x in rendered)

    i = 0
    while total() > budget and i < len(records):
        rendered[i] = _render_short(records[i])
        i += 1
    dropped = 0
    while total() > budget and len(rendered) > 1:
        rendered.pop(0)
        dropped += 1
    if dropped:
        rendered.insert(0, f"({dropped} earlier step(s) omitted to keep this short.)")
    return _HEADER + "\n\n" + "\n\n".join(rendered)
