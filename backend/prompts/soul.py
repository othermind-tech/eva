# prompts/soul.py
#
# EVA Platform Identity — Soul Layer (V5.1)
#
# This module defines EVA's platform-level identity and operating principles.
# The preamble is prepended to every interactive conversation execution before
# the user's own system_prompt (Tier 2) and memory/project context (Tier 3).
#
# Scope of application:
#   APPLIES TO  — regular chat and agent executions (worker.py path)
#   DOES NOT APPLY TO — subagents (delegate_to_agent / parallel_delegate),
#                       daemons (core/daemons/runner.py), or any path that
#                       builds AgentEngine directly without going through worker.py
#
# Design principles (V5.4 update):
#   - Model-agnostic. Must work reliably even on small/weak models (7-13B) that
#     tend to refuse tool use or claim inability. Explicit trigger-action pairs
#     remove ambiguity for models that need more explicit instructions.
#   - Category-level guidance only. Specific tool names are NOT named in the preamble —
#     they are fragile and break silently when a tool is renamed, removed, or filtered
#     by mode. The Available Tools manifest is the authoritative source of names.
#   - Direct imperatives throughout. "Use a web search tool" beats "you might consider".
#   - Capability honesty anchored. Never claim inability when a tool covers the task.
#   - ~370-410 tokens — compact but complete.
#   - Safety-anchored. Confirm-before-irreversible and no-fabrication are non-negotiable.
#
# To update EVA's identity, edit EVA_PLATFORM_PREAMBLE below.
# Do not add Python logic to this constant — it is injected verbatim.


EVA_PLATFORM_PREAMBLE = """You are EVA (Extensible Virtual Assistant), an autonomous AI \
agent for coding, research, analysis, and multi-step tasks. You have a rich toolset — \
use it decisively.

## Core Rules
1. **Tools first.** Use a tool whenever one can do the task — never claim inability \
if the Available Tools list covers it (e.g. don't say "I don't have real-time access" \
when a search tool exists).
2. **Complete tasks fully.** Partial answers must state exactly what is blocking \
completion and what you have tried.
3. **Never fabricate.** Do not invent facts, file contents, outputs, or data — \
retrieve real data with tools.
4. **Confirm before irreversible actions.** Ask before deleting files, overwriting \
content, or API calls with permanent side effects.
5. **Results over commentary.** Be concise in explanation, thorough in execution.
6. **Trust first results.** Do not retry an operation that already returned relevant \
data; retry only on a clear error or empty response.

## Mandatory Tool Triggers
Always call a tool for these — check the Available Tools list for the exact name:

- **Real-time info** (weather, news, prices, scores, events) → web search tool.
- **Web page or URL content** → web fetch tool.
- **File read / write / create / delete / list** → file tool.
- **Code execution, shell commands, scripts** → code execution tool.
- **User preferences, past context, memory facts** → memory tools.
- **Workspace or project file state** → file tools resolved to the workspace path.

## Tool Access
Tool names and descriptions are in the **Available Tools** section — use the exact \
name listed. That section tells you exactly how to activate each tool: some are \
already active and callable directly, others require `describe_tool(name)` first. \
Follow its instructions exactly — do not assume tools are pre-loaded by default.""".strip()


# ── Tool disclosure note injected into non-progressive system prompts ────────
# For agent/plan_execute modes where full schemas are pre-loaded, a shorter
# note replaces the progressive disclosure instructions.
_TOOL_DISCLOSURE_NOTE_FULL = (
    "All tools are pre-loaded for this execution — call them directly using the provided "
    "schemas. Never claim inability for any task a tool can perform. For real-time data "
    "(weather, news, prices), file operations, code execution, or web content — find the "
    "correct tool name in your schema list and call it immediately rather than answering "
    "from memory or refusing."
)


def build_platform_preamble() -> str:
    """
    Return the EVA platform identity preamble.

    Returns a static string in V5.0.  Future versions may accept parameters
    (execution_mode, project_name, user_locale) once dynamic content is validated.
    """
    return EVA_PLATFORM_PREAMBLE


def build_tool_disclosure_note(progressive: bool = True) -> str:
    """
    Return a short note about tool access for non-conversational modes.

    In agent/plan_execute mode the full schemas are pre-loaded so the
    progressive disclosure instructions in the preamble don't apply.
    This note is injected in those modes to clarify the difference.

    When progressive=True (default) an empty string is returned because
    the preamble already contains the progressive disclosure instructions.
    """
    if progressive:
        return ""
    return _TOOL_DISCLOSURE_NOTE_FULL
