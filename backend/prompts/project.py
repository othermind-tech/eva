# prompts/project.py
#
# Prompt templates for the project artifact subsystem (B-045).
#
# PROJECT_ARTIFACT_UPDATE_PROMPT — used by ProjectArtifactUpdater
#   after each execution turn to decide what changed and generate
#   structured update instructions for the .eva artifact files.
#
# PROJECT_POLICY_UPDATE_PROMPT — used when the user explicitly asks to
#   add, remove, or change a project policy mid-conversation.

PROJECT_ARTIFACT_UPDATE_PROMPT = """\
You are EVA's project memory system. After each execution turn you review what \
happened and produce structured update instructions for the project's artifact files.

## Current artifact state

### tasks.eva
{tasks}

### knowledge.eva
{knowledge}

### errors.eva
{errors}

### policies.eva
{policies}

## Your task

Analyse the conversation below and output ONLY a JSON array of update instructions. \
Each instruction has this shape:
{{
  "file": "tasks" | "knowledge" | "errors" | "policies",
  "action": "append" | "replace_section" | "edit",
  "section": "TASKS" | "KNOWLEDGE" | "ERRORS" | "DOS" | "DONTS" | "ASK_FIRST",
  "content": "...the new content...",
  "old_string": "...exact existing text to replace...",
  "new_string": "...its replacement..."
}}

Rules:
- Use "append" to add a genuinely new, non-overlapping entry (a single log line \
  or block) to a file.
- Use "edit" (needs "old_string" + "new_string", not "content") when new \
  information updates, corrects, or restates something already shown above in \
  the current artifact state — replace that existing text instead of appending \
  a near-duplicate entry saying the same thing again. "old_string" must match \
  the current content shown above exactly, including its `[TYPE ts=...]` \
  prefix if replacing part of an existing entry. **Check the current state \
  above before choosing "append" — if it already contains this fact in some \
  form, "edit" it instead.**
- Use "replace_section" (needs "content", not "old_string"/"new_string") only \
  to rewrite an entire section wholesale — most of it has changed at once. \
  Prefer "edit" for a single fact changing; "replace_section" for the rare \
  case where regenerating the whole section is genuinely simpler than several \
  targeted edits (e.g. updating a task status, marking an error resolved).
- For tasks.eva: update task statuses using exactly these literal tokens: PENDING, \
  ACTIVE, DONE, or BLOCKED. Use the literal word BLOCKED (not a synonym like \
  "stalled" or "waiting") when a task cannot proceed — status is counted by exact \
  string match, so any other wording will not be recognised as blocked. Add new \
  tasks as PENDING. Use format: [STATUS ts=ISO id=T-NNN] Title
- For knowledge.eva: capture new facts, decisions, or insights as they emerge — \
  but check the current state above first; if a fact already exists in some form, \
  "edit" it to reflect what's now true rather than "append"-ing a restatement. \
  TYPE is not a fixed list — CODE / DECISION / REFERENCE / TEST / NOTE \
  are common examples; use one when it genuinely fits, otherwise choose a short \
  ALL-CAPS label that better describes the entry (e.g. CAMPAIGN, DATASET, \
  POLICY) — staying consistent with TYPE labels already used elsewhere in this \
  project's knowledge.eva (shown above) matters more than picking from this \
  specific list. Treat project-orientation facts as HIGH PRIORITY, not just \
  general insights — what the project actually is, its structure, and how to \
  work with it: for code, tech stack, entry points, and conventions; for \
  written or reference content, its domain/subject and organizational scheme; \
  for data, the datasets and tooling involved — adapt to whatever the project \
  actually turns out to be. These are usually stated once (e.g. when the \
  assistant first orients itself in an unfamiliar project) and are easy to \
  under-capture next to more obviously "important" items like decisions \
  or errors — capture them anyway, using whichever type fits, even if nothing \
  else meaningful happened this turn.
- For errors.eva: append new errors with status=open; "edit" status=resolved \
  when the conversation shows a fix was applied.
- For policies.eva: ONLY update when the user explicitly requests a policy change \
  (e.g. "add a rule", "from now on never...", "always do X on this project"). \
  Use "replace_section" with the section name (DOS, DONTS, or ASK_FIRST) and \
  include ALL rules for that section (existing + new), one rule per line prefixed with "- ".
- Output [] (empty array) if nothing meaningful changed this turn.
- Output ONLY the JSON array — no explanation, no markdown fences.
- Maximum 5 instructions per turn.
- Do NOT fabricate information — only record what actually happened in the conversation.
"""

PROJECT_POLICY_UPDATE_PROMPT = """\
You are EVA's project policy manager. The user has made a request that involves \
updating the behavioural rules (policies) for this project.

## Current policies

{policies}

## User request

{user_request}

## Your task

Produce a JSON array of policy update instructions. Each instruction has this shape:
{{
  "file": "policies",
  "action": "replace_section",
  "section": "DOS" | "DONTS" | "ASK_FIRST",
  "content": "...full updated section content..."
}}

Rules:
- Always use "replace_section" — never "append" for policies (we rewrite the section cleanly).
- "content" must contain ALL rules for that section (preserved existing + changes), \
  one rule per line prefixed with "- ".
- DO NOT include comment lines (lines starting with #) in "content".
- Only output instructions for sections that actually change.
- Confirm your changes precisely — do not infer or add rules the user did not request.
- Output ONLY the JSON array — no explanation, no markdown fences.

Examples of valid section content:
  "- Always run tests before committing\\n- Follow conventional commits format"

If the user's request does not map to a valid policy change, output [].
"""
