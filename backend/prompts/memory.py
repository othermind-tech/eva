# prompts/memory.py
#
# Prompt templates for the memory subsystem.
#
# Three prompts live here:
#
#   SUMMARY_SYSTEM_PROMPT      — used by ConversationSummarizer (core/memory/summarizer.py)
#                                to compress conversation history into a compact long-term
#                                summary entry.
#
#   USER_EXTRACTION_SYSTEM     — used by MemoryExtractor (core/memory/extractor.py)
#                                to pull user-level facts (preferences, skills, style)
#                                from a conversation.
#
#   AGENT_EXTRACTION_SYSTEM    — used by MemoryExtractor (core/memory/extractor.py)
#                                to pull agent-scope entries (errors, decisions, state,
#                                artifacts) from a conversation.
#
# To tune memory behaviour, edit the prompts here.
# The engine classes pick up changes automatically — no other edits needed.

# ---------------------------------------------------------------------------
# Conversation summariser
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM_PROMPT = """\
You are creating a comprehensive memory summary of a conversation for EVA, an AI agent.

This summary is the ONLY persistent record of this conversation. It will be injected verbatim \
into future sessions when the conversation overflows context — EVA will read only THIS document, \
not the original messages. It must be detailed enough that EVA can continue without any loss of \
continuity: full awareness of what was discussed, built, decided, and left pending.

Write as if handing off to a colleague who has never seen this conversation. Do not compress \
away specifics — exact names, exact paths, exact errors, and exact task states are essential.

## Sections to cover

### Primary Objective
What was the user trying to accomplish? State the main goal, all sub-goals, and any explicit \
constraints or restrictions the user imposed. Preserve the user's exact words for restrictions \
(e.g. "DO NOT modify X", "always commit after tests pass") — do NOT paraphrase.

### Work Completed
Specific tasks that were finished — be exhaustive, not selective:
- Files created, modified, or deleted (exact absolute or relative paths)
- Functions, classes, and methods written or changed (include signatures if relevant)
- Code blocks written verbatim when they are short and critical (e.g. a config snippet, \
  a key fix, a schema change) — copy them exactly, do not summarize
- Commands run and their outcomes (include exact command and key output)
- API calls made and results received
- Configurations changed, migrations run, DB schemas altered
- Tests executed and whether they passed or failed (include test names/files)

### Errors & Resolutions
Every error encountered during this session:
- Exact error message or traceback (copy verbatim — do not paraphrase)
- Root cause identified
- Resolution applied (exact fix, file, line if known)
- If unresolved: state that explicitly and what was tried

### Decisions & Constraints
Every architectural, design, or workflow choice made or confirmed:
- The decision and the rationale given
- Any constraint the user imposed that affects future work
- User preferences stated during this session that affect how EVA should behave

### Pending Tasks
ALL tasks that were discussed but not completed — list every single one explicitly:
- Tasks that were started but not finished (exact state of each)
- Tasks that were agreed on but not yet started
- The immediate next step (the FIRST thing to do when this conversation resumes)
Do NOT summarize pending work as "several items remain" — name each item.

### Current State
The precise state at the end of this conversation segment. For ongoing work: which files are \
in a modified-but-not-committed state, which tests are passing/failing, what the working \
directory or environment state is. This section and "Pending Tasks" are the most critical.

### Key Context
Background essential for continuity: codebase layout, environment specifics (Python version, \
venv name, ports, DB names), dependencies, known gotchas, important file locations, anything \
EVA must not forget to avoid repeating past mistakes.

## Rules
- Be specific: exact file paths, function names, error messages, command outputs, IDs, versions
- Preserve user constraints and instructions verbatim — never paraphrase restrictions
- Include short critical code snippets verbatim (configs, key fixes, schema diffs)
- List ALL pending tasks explicitly — never collapse them into vague "remaining work"
- Write in past tense for completed work, present tense for current state and pending items
- Do NOT include assistant reasoning or internal monologue
- Do NOT include pleasantries, meta-commentary, or small talk
- Do NOT speculate about things not discussed
- Skip any section that genuinely has nothing to report
- Maximum 2000 words — use the space fully if the conversation warrants it
"""

# Used when an existing summary is being extended with new messages.
# The LLM receives this as the system prompt and the new messages as the user turn.
# It must produce ONE coherent integrated document — not an append.
SUMMARY_UPDATE_SYSTEM_PROMPT = """\
You are updating a running memory summary for EVA, an AI agent.

An existing summary covers earlier parts of the conversation. New messages have occurred since \
then. Produce a single, coherent, integrated summary that merges the old summary with the new \
content. Do NOT simply append — rewrite sections where the situation has changed. The result \
must read as one continuous document, not an old section plus an addendum.

## Existing summary
{existing_summary}

## Update rules
- "Work Completed": add newly finished tasks; keep all completed items from the old summary
- "Pending Tasks": update each item's state — remove tasks that are now done, add newly \
  identified tasks, update the immediate next step. Never collapse tasks into vague summaries.
- "Current State": rewrite to reflect the newest situation precisely
- "Errors & Resolutions": add new errors and their fixes; keep unresolved items until resolved
- "Decisions & Constraints": preserve all existing constraints unless the user explicitly changed \
  them; add new decisions that emerged in the new messages
- Preserve short critical code snippets from the existing summary; add new ones from the new messages
- Keep specifics: exact file paths, function names, error messages, IDs, version numbers
- Do NOT include assistant reasoning or pleasantries
- Maximum 2000 words — use the space fully if the conversation warrants it

Produce the complete updated summary now, using the same section structure as the existing summary.
"""

# ---------------------------------------------------------------------------
# Conversation export — used when referencing a conversation (B-075)
# ---------------------------------------------------------------------------
#
# Used by api/conversation_management.py when a conversation with > 15
# genuine events is referenced. The JSONL transcript is passed as user
# message; this prompt is the system instruction.
#
# Template variables: {title}, {date}, {message_count}, {model}

CONVERSATION_EXPORT_PROMPT = """\
You are producing a reference document for the conversation below.
This document will be injected as context into a NEW conversation so the \
user can continue or build on the previous work without re-reading the original.

Be thorough. Preserve specifics. Do not compress away exact values — names, \
numbers, file paths, error messages, and code are essential.

Produce a structured markdown document with exactly these sections:

# {title}
**Date:** {date}  **Events:** {message_count}  **Model:** {model}

## Topic & Goal
What the user was trying to accomplish. State the main goal and any constraints \
the user imposed (preserve exact wording of restrictions).

## Key Exchanges & Conclusions
Bullet-pointed summary of the main Q&A, decisions, and outcomes. One bullet per \
distinct exchange. Preserve exact answers, not paraphrases.

## Important Data & Facts
Specific numbers, names, URLs, file paths, credentials (non-sensitive), \
configurations, or values that appeared. List as bullets.

## Code & Artifacts Produced
Verbatim code blocks, commands run, files created or modified. Do NOT truncate \
code — copy it exactly. If too long, include the critical sections.

## Tool Calls & Results
For each tool call: what was called, with what arguments, and what the key result \
was. Format: `tool_name(args)` → result summary.

## Open Items
Anything explicitly left unresolved, flagged for follow-up, or marked incomplete.

---
IMPORTANT: A reader of this summary should be able to continue the work without \
having seen the original conversation. Be specific. Preserve exact values.
"""

# ---------------------------------------------------------------------------
# Memory extractor — user facts (Scope 2)
# ---------------------------------------------------------------------------

# Slot vocabulary injected into the prompt so the LLM can pick consistently.
# Keep this in sync with SINGULAR_SLOTS in core/memory/fact_summary.py.
_SLOT_VOCABULARY = """
SINGULAR slots (only ONE fact per slot — new info replaces old):
  user.name             — The user's name
  user.occupation       — Job title / profession / role
  user.location         — City, country, or timezone
  user.primary_language — Primary programming language
  user.experience_level — junior | mid | senior | expert
  user.timezone         — Timezone identifier (e.g. "Asia/Kolkata")

PLURAL slots (multiple facts allowed — skip if semantically identical to an existing one):
  user.skill            — A specific technical skill or area of expertise
  user.tool             — A tool, framework, or library they use regularly
  user.preference       — A stated preference about how they work or want responses
  user.style            — Communication or coding style trait
  user.project          — A project or domain they are actively working on
  user.misc             — Anything personal that does not fit the above slots

RESERVED slots (NEVER propose "new" or "supersedes" for these — they are
profile-locked, set only via account settings, not by extraction):
  user.full_name        — Formal/legal name (profile settings only)
  user.about            — Bio / about text (profile settings only)
  If the user states their formal name or bio-like info in conversation, do
  NOT target these slots. Use user.name for an informal/preferred name
  mention instead, or skip the fact entirely if it duplicates profile info.
"""

USER_EXTRACTION_SYSTEM = (
    "You are a strict memory extractor for an AI assistant called EVA.\n"
    "Your ONLY job is to pull out DURABLE facts about the USER that will still be\n"
    "true and useful weeks or months from now. You are not a summariser. You are\n"
    "not a task logger. You do not record what the user asked about — only who the\n"
    "user IS and how the user PREFERS to work.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 0 — GATE CHECK (do this FIRST, before anything else)\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Read the conversation once and ask: does the USER explicitly state any\n"
    "durable fact about themselves, their identity, their skills, their\n"
    "preferences, or their working style?\n\n"
    "If the answer is NO — return exactly []  and stop.\n\n"
    "Conversations that should almost always return [] :\n"
    "  • Greetings and small talk  (\"Hi\", \"Hello\", \"Thanks\", \"Bye\")\n"
    "  • Factual lookups           (\"What's the weather in Mumbai?\",\n"
    "                               \"Who won the match yesterday?\")\n"
    "  • Task requests             (\"Fix this bug\", \"Write a Python script\")\n"
    "  • Tool-use sessions         (the user asked EVA to search / read / run\n"
    "                               something — that is a TASK, not a fact)\n"
    "  • Error debugging           (unless the user stated a preference about\n"
    "                               HOW they want errors handled)\n"
    "  • One-off confirmations     (\"ok\", \"yes\", \"do it\", \"cancel\")\n\n"
    "Returning [] is the correct, expected answer most of the time.\n"
    "It is MUCH better to return [] than to invent a weak fact.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 1 — WHAT TO EXTRACT (only if the gate check passed)\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Extract ONLY facts the user STATES about themselves using first-person\n"
    "language, or facts the user confirms when asked directly.\n\n"
    "Good signals (first-person, durable):\n"
    "  \"I'm a backend engineer at Acme\"          → user.occupation\n"
    "  \"I live in Berlin\"                        → user.location\n"
    "  \"My name is Alex\"                         → user.name\n"
    "  \"I mainly work in Python and Go\"          → user.primary_language + user.skill\n"
    "  \"I prefer short, direct answers\"          → user.preference\n"
    "  \"Always show me code before explaining\"   → user.style\n"
    "  \"I use Neovim and tmux\"                   → user.tool\n"
    "  \"I've been programming for 10 years\"      → user.experience_level\n\n"
    "Weak signals — do NOT extract:\n"
    "  ✗ \"What's the weather in Mumbai?\"\n"
    "      (this is a QUERY about a topic, not a fact about the user)\n"
    "  ✗ \"Can you help me debug this auth bug?\"\n"
    "      (this is a TASK, not a preference)\n"
    "  ✗ \"Search for the latest React docs\"\n"
    "      (this is a TOOL INSTRUCTION, not a skill)\n"
    "  ✗ \"Hi\" / \"Hello there\"\n"
    "      (this is a greeting)\n"
    "  ✗ \"user asked about Mumbai weather\"\n"
    "      (extractor is inventing a meta-fact — forbidden)\n"
    "  ✗ \"user was interested in Python\"\n"
    "      (interest in a topic ≠ preference — needs explicit statement)\n"
    "  ✗ assistant actions, tool results, task outcomes\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 2 — SLOT VOCABULARY\n"
    "════════════════════════════════════════════════════════════════════════\n"
    + _SLOT_VOCABULARY + "\n"
    "Slot rules:\n"
    "  • Every fact MUST be assigned to exactly one slot from the list above.\n"
    "  • SINGULAR slots hold only ONE value. If a new value is stated, the\n"
    "    action MUST be \"supersedes\" with the existing fact's ID.\n"
    "  • PLURAL slots can hold multiple values. Before adding, check existing\n"
    "    facts — if the meaning is the same, mark it \"redundant\".\n"
    "  • user.misc is NOT a trash bin. Use it ONLY for durable personal\n"
    "    context that has no other slot AND is meaningful for personalisation\n"
    "    (e.g. \"has a cat named Luna\", \"is vegetarian\", \"uses a standing desk\").\n"
    "    NEVER use user.misc for query topics, task descriptions, tool-use\n"
    "    intents, or anything transient.\n"
    "  • user.project ONLY when the user explicitly names a project they own\n"
    "    or are actively working on (\"I'm building a Rust CLI called zap\").\n"
    "    Mentioning a third-party project in passing is NOT a user.project.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 3 — DEDUPE AGAINST EXISTING FACTS\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Existing facts already stored for this user:\n"
    "{existing_facts_block}\n\n"
    "For each candidate fact, classify the action:\n"
    "  • \"new\"        — genuinely new, not captured by any existing fact\n"
    "  • \"redundant\"  — same meaning as an existing fact (even if worded\n"
    "                    differently); skip by setting this action\n"
    "  • \"supersedes\" — directly updates or contradicts an existing fact;\n"
    "                    put the existing fact's ID in supersedes_id\n\n"
    "Hard rule: for every SINGULAR slot where a fact already exists, a new\n"
    "candidate MUST be either \"redundant\" or \"supersedes\" — never \"new\".\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 4 — OUTPUT FORMAT\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Return a JSON array. Nothing else. No prose. No markdown. No code fences.\n"
    "Maximum 5 items. Return [] if nothing qualifies.\n\n"
    "Schema per item:\n"
    '  {\n'
    '    "fact":          "<concise first-person-ish statement, max 120 chars>",\n'
    '    "category":      "preference|skill|style|context|misc",\n'
    '    "slot_key":      "<one slot from the vocabulary above>",\n'
    '    "action":        "new|redundant|supersedes",\n'
    '    "supersedes_id": "<id of existing fact, or null>"\n'
    '  }\n\n'

    "Example — a substantive conversation:\n"
    "  USER: Hey, I'm Alex. I'm a senior backend engineer in Berlin, I do\n"
    "        mostly Python and Go. Please keep answers short and code-first.\n"
    "  → Output:\n"
    '  [\n'
    '    {"fact": "Name is Alex", "category": "context", '
    '"slot_key": "user.name", "action": "new", "supersedes_id": null},\n'
    '    {"fact": "Senior backend engineer", "category": "context", '
    '"slot_key": "user.occupation", "action": "new", "supersedes_id": null},\n'
    '    {"fact": "Based in Berlin", "category": "context", '
    '"slot_key": "user.location", "action": "new", "supersedes_id": null},\n'
    '    {"fact": "Primary languages: Python and Go", "category": "skill", '
    '"slot_key": "user.primary_language", "action": "new", "supersedes_id": null},\n'
    '    {"fact": "Prefers short, code-first answers", "category": "style", '
    '"slot_key": "user.style", "action": "new", "supersedes_id": null}\n'
    '  ]\n\n'
    "Example — a trivial conversation:\n"
    "  USER: Hi, what's the weather in Mumbai today?\n"
    "  ASSISTANT: <calls web_search and reports the weather>\n"
    "  → Output:\n"
    "  []\n\n"
    "Now extract facts from the conversation provided in the user turn."
)

# ---------------------------------------------------------------------------
# Memory extractor — agent entries (Scope 3)
# ---------------------------------------------------------------------------

AGENT_EXTRACTION_SYSTEM = (
    "You are a strict memory extractor for an AI agent's WORKING memory inside a\n"
    "specific project or task scope. Your job is to capture the small number of\n"
    "technical facts that future executions of this same agent will need in order\n"
    "to avoid repeating mistakes, rediscovering facts, or losing track of state.\n\n"
    "You are writing notes for your future self — not a summary for a human.\n"
    "Every entry must be specific, actionable, and worth re-reading next week.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 0 — GATE CHECK\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Ask: did anything in this conversation produce knowledge that the agent\n"
    "would regret losing?\n\n"
    "If the answer is NO — return exactly []  and stop.\n\n"
    "Conversations that should return [] :\n"
    "  • Pure chit-chat or greetings\n"
    "  • Simple factual Q&A with no lasting impact\n"
    "  • Trivial one-shot tool calls (single web search, single file read)\n"
    "  • Tasks where nothing was learned, decided, or completed\n"
    "  • Failed/aborted executions with no diagnostic value\n\n"
    "Returning [] is correct and expected most of the time.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 1 — ENTRY TYPES\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Every entry has a type. Use the most specific type that fits:\n\n"
    "  error    — A concrete problem encountered AND its root cause.\n"
    "             Must name the error, the trigger, and what was wrong.\n"
    "             Good: \"SQLAlchemy 'greenlet_spawn has not been called' when\n"
    "                     accessing lazy-loaded attribute after session close;\n"
    "                     fix: use selectinload() in repository query.\"\n"
    "             Bad:  \"there was an error\" / \"db failed\"\n\n"
    "  success  — A specific approach that WORKED and should be reused.\n"
    "             Must name the technique and the context it applies to.\n"
    "             Good: \"Batching tool calls through parallel_delegate cut\n"
    "                     research time from 40s to 9s for 5-source lookups.\"\n"
    "             Bad:  \"it worked\" / \"good result\"\n\n"
    "  decision — An architectural or design choice made, with rationale.\n"
    "             Good: \"Chose Redis pub/sub over DB polling for SSE fan-out\n"
    "                     because we already have Redis and latency matters.\"\n"
    "             Bad:  \"decided to use redis\"\n\n"
    "  state    — The precise current state of an ongoing task at end of\n"
    "             session. What is done, in progress, pending, next step.\n"
    "             Good: \"V4.37 rollback is implemented in worker.py; still\n"
    "                     need to add unit test for PEV-mode cancellation.\"\n"
    "             Bad:  \"working on worker.py\"\n\n"
    "  artifact — A concrete file, URL, ID, or reference the agent will need\n"
    "             to locate again. Include the identifier verbatim.\n"
    "             Good: \"core/memory/extractor.py lines 117–120 — where\n"
    "                     scope_key is resolved from session.\"\n"
    "             Bad:  \"the extractor file\"\n\n"
    "  note     — Use sparingly, only when nothing else fits but the fact is\n"
    "             still worth remembering. Prefer a more specific type.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 1b — DEDUPE AGAINST EXISTING ENTRIES\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Existing entries already stored for this scope:\n"
    "{existing_agent_entries_block}\n\n"
    "For each candidate entry, classify the action:\n"
    "  • \"new\"        — genuinely new, not captured by any existing entry\n"
    "  • \"redundant\"  — same meaning as an existing entry (even if worded\n"
    "                    differently); skip by setting this action\n"
    "  • \"supersedes\" — updates, corrects, or reinforces an existing entry;\n"
    "                    put the existing entry's ID in supersedes_id\n\n"
    "Hard rule — \"state\" is SINGULAR per scope: if any existing entry above\n"
    "has type \"state\", a new \"state\" candidate MUST be \"supersedes\" targeting\n"
    "that entry's ID — never \"new\". There is only ever one current state per\n"
    "scope; an old state snapshot is not a separate fact, it is the same fact\n"
    "grown stale.\n\n"
    "Recurrence rule: if a new candidate closely reinforces (not merely\n"
    "repeats) an existing entry — e.g. the same error happened again, the\n"
    "same approach worked a second time — prefer \"supersedes\" over\n"
    "\"redundant\" and fold the recurrence into the content (e.g. append\n"
    "\"(recurring — seen again)\"), so repetition strengthens the memory\n"
    "instead of being silently dropped.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 2 — QUALITY RULES\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "  • Be specific: exact file paths, function names, error messages, IDs,\n"
    "    version numbers, command outputs.\n"
    "  • Be self-contained: the entry must be understandable weeks later\n"
    "    without the surrounding conversation.\n"
    "  • Be concise: one to three sentences, max ~250 characters.\n"
    "  • No assistant monologue or reasoning — facts only.\n"
    "  • No user personality or preferences — that belongs to user memory.\n"
    "  • No duplicates within this batch — if two candidates in THIS\n"
    "    extraction say the same thing, keep one (separate from STEP 1b,\n"
    "    which checks against entries already stored from past sessions).\n"
    "  • Maximum 5 entries. Fewer is better. Return [] when nothing qualifies.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 3 — TAGS\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Tags are short lowercase keywords (2–5 per entry) that make the entry\n"
    "searchable. Prefer concrete nouns: module names, error codes, tool names,\n"
    "framework names. Avoid generic tags like \"code\", \"bug\", \"fix\".\n\n"
    "  Good tags: [\"sqlalchemy\", \"async\", \"session-scope\", \"selectinload\"]\n"
    "  Bad  tags: [\"code\", \"error\", \"database\"]\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 3b — SENSITIVE FLAG\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Set \"sensitive\": true if the entry contains credentials, tokens, API\n"
    "keys, personal/private data, or anything the user explicitly asked to\n"
    "keep private. Sensitive entries are hidden from casual recall and\n"
    "require explicit unlock before being shown again. Default to false —\n"
    "only mark sensitive when the content genuinely warrants it.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "STEP 4 — OUTPUT FORMAT\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Return a JSON array. Nothing else. No prose. No markdown. No code fences.\n"
    "Maximum 5 items. Return [] if nothing qualifies.\n\n"
    "Schema per item:\n"
    '  {\n'
    '    "type":          "error|success|decision|state|artifact|note",\n'
    '    "content":       "<specific, self-contained, max ~250 chars>",\n'
    '    "tags":          ["tag1", "tag2", "tag3"],\n'
    '    "sensitive":     true | false,\n'
    '    "action":        "new|redundant|supersedes",\n'
    '    "supersedes_id": "<id of existing entry, or null>"\n'
    '  }\n\n'

    "Example — a substantive debugging session, no relevant existing entries:\n"
    "  → Output:\n"
    '  [\n'
    '    {"type": "error",\n'
    '     "content": "greenlet_spawn error in MemoryRepo.get_user_facts when '
    'accessing fact.slot_key after session closed. Root cause: lazy load '
    'outside async context. Fix: add selectinload(UserMemoryFact.slot).",\n'
    '     "tags": ["sqlalchemy", "async", "selectinload", "memoryrepo"],\n'
    '     "sensitive": false, "action": "new", "supersedes_id": null},\n'
    '    {"type": "state",\n'
    '     "content": "Fix applied to repository.py line 412; extractor.py '
    'tests green; still need to update long_term.py which uses the same '
    'query pattern.",\n'
    '     "tags": ["memory", "repository", "pending"],\n'
    '     "sensitive": false, "action": "new", "supersedes_id": null}\n'
    '  ]\n\n'
    "Example — an existing entry above reads:\n"
    "  [ID: a1b2] [state] Fix applied to repository.py line 412; still need "
    "to update long_term.py\n"
    "  and this session finished that pending work:\n"
    "  → Output:\n"
    '  [\n'
    '    {"type": "state",\n'
    '     "content": "long_term.py updated to match repository.py\'s '
    'selectinload fix; both files now consistent. Task complete.",\n'
    '     "tags": ["memory", "repository", "selectinload"],\n'
    '     "sensitive": false, "action": "supersedes", "supersedes_id": "a1b2"}\n'
    '  ]\n\n'
    "Example — a trivial conversation:\n"
    "  USER: What time is it in Tokyo?\n"
    "  → Output:\n"
    "  []\n\n"
    "Now extract entries from the conversation provided in the user turn."
)

# ---------------------------------------------------------------------------
# A-04 Phase C — one-time reconciliation (backfill for entries/facts that
# accumulated BEFORE the dedupe/supersedes pipeline above existed).
#
# Distinct from extraction: there is no new conversation here, only an
# existing stored list examined against itself. User-triggered only (never
# scheduled/automatic) -- proposes deletions, never executes them; the
# caller (core/memory/reconciler.py) applies only the IDs the user actually
# confirmed.
# ---------------------------------------------------------------------------

AGENT_MEMORY_RECONCILE_SYSTEM = (
    "You are reviewing an AI agent's WORKING memory for one scope, looking\n"
    "for entries that should be deleted because a newer entry already\n"
    "covers them. You are not extracting anything new -- only identifying\n"
    "redundant history in a list you are shown in full.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "WHAT TO FLAG FOR DELETION\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "1. Near-duplicates: two or more entries that say essentially the same\n"
    "   thing (even if worded differently). Keep the newest (highest in the\n"
    "   list below, since entries are shown newest-first); flag the rest.\n"
    "2. Multiple \"state\" entries for this scope. There should be exactly one\n"
    "   current state per scope. Keep only the newest \"state\" entry; flag\n"
    "   every older one.\n"
    "3. An entry clearly superseded/contradicted by a newer one -- e.g. an\n"
    "   old \"decision\" a later entry explicitly reverses, or an \"error\"\n"
    "   entry whose fix a later entry confirms is done. Keep the newer one;\n"
    "   flag the older, now-incorrect one.\n\n"
    "Do NOT flag an entry just because it is old, or because it is similar\n"
    "in topic but describes a genuinely different fact, error, or decision.\n"
    "When in doubt, do not flag it -- this is cleanup of clear redundancy,\n"
    "not a quality judgment on what is worth keeping.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "OUTPUT FORMAT\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Return a JSON array. Nothing else. No prose. No markdown. No code fences.\n"
    "Return [] if nothing should be deleted -- this is the expected answer\n"
    "for a scope that is already clean.\n\n"
    "Schema per item:\n"
    '  {\n'
    '    "delete_id": "<id of the entry to delete>",\n'
    '    "reason":    "<one short sentence: which newer entry supersedes it and why>"\n'
    '  }\n\n'

    "Example -- two state entries and a near-duplicate error:\n"
    "  Entries (newest first):\n"
    "  [ID: e5] [state] Auth module: refresh tokens done, task complete\n"
    "  [ID: e4] [error] greenlet_spawn error in get_user_facts; fixed with selectinload()\n"
    "  [ID: e3] [state] Auth module: JWT done, refresh tokens pending\n"
    "  [ID: e2] [error] greenlet_spawn error accessing fact.slot_key after session close; fix: selectinload()\n"
    "  → Output:\n"
    '  [\n'
    '    {"delete_id": "e3", "reason": "Superseded by e5 -- refresh tokens are now done, this state is stale."},\n'
    '    {"delete_id": "e2", "reason": "Near-duplicate of e4 -- same greenlet_spawn/selectinload fix."}\n'
    '  ]\n\n'
    "Example -- already clean, nothing to flag:\n"
    "  → Output:\n"
    "  []\n\n"
    "Now review the entries provided in the user turn."
)

AGENT_MEMORY_RECONCILE_USER_TEMPLATE = (
    "Entries for this scope, newest first:\n"
    "{entries_block}\n\n"
    "Identify which entries should be deleted per the rules above. "
    "Respond with ONLY the JSON array."
)

USER_FACT_RECONCILE_SYSTEM = (
    "You are reviewing a user's stored profile facts, looking for entries\n"
    "that should be deleted because a newer fact already covers them. You\n"
    "are not extracting anything new -- only identifying redundant history\n"
    "in a list you are shown in full.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "WHAT TO FLAG FOR DELETION\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "1. Two or more facts occupying the SAME singular slot (e.g. two\n"
    "   user.occupation facts). This should not normally happen, but old\n"
    "   history predating slot enforcement can contain it. Keep the newest;\n"
    "   flag the rest.\n"
    "2. Near-duplicate facts in the same plural slot (e.g. two user.tool\n"
    "   facts both naming the same tool, worded differently). Keep the\n"
    "   newest; flag the rest.\n"
    "3. A fact clearly contradicted by a newer one in the same slot (e.g.\n"
    "   an old \"uses Python 2\" fact contradicted by a newer \"uses Python\n"
    "   3.11\" fact in the same slot). Keep the newer one; flag the older.\n\n"
    "Do NOT flag two facts in the same PLURAL slot that describe genuinely\n"
    "different things (e.g. \"uses Neovim\" and \"uses tmux\" are both\n"
    "user.tool but are not duplicates of each other). When in doubt, do not\n"
    "flag it.\n\n"

    "════════════════════════════════════════════════════════════════════════\n"
    "OUTPUT FORMAT\n"
    "════════════════════════════════════════════════════════════════════════\n"
    "Return a JSON array. Nothing else. No prose. No markdown. No code fences.\n"
    "Return [] if nothing should be deleted -- this is the expected answer\n"
    "for a user whose facts are already clean.\n\n"
    "Schema per item:\n"
    '  {\n'
    '    "delete_id": "<id of the fact to delete>",\n'
    '    "reason":    "<one short sentence: which newer fact supersedes it and why>"\n'
    '  }\n\n'

    "Example -- a duplicate singular slot:\n"
    "  Facts (newest first):\n"
    "  [ID: f9] [user.occupation] Senior backend engineer\n"
    "  [ID: f3] [user.occupation] Backend engineer\n"
    "  → Output:\n"
    '  [\n'
    '    {"delete_id": "f3", "reason": "Same singular slot (user.occupation) as f9, which is newer."}\n'
    '  ]\n\n'
    "Example -- already clean, nothing to flag:\n"
    "  → Output:\n"
    "  []\n\n"
    "Now review the facts provided in the user turn."
)

USER_FACT_RECONCILE_USER_TEMPLATE = (
    "Facts for this user, newest first:\n"
    "{facts_block}\n\n"
    "Identify which facts should be deleted per the rules above. "
    "Respond with ONLY the JSON array."
)
