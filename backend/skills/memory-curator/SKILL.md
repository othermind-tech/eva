---
name: memory-curator
description: "Use when reviewing, cleaning, or organising stored memory entries.
  Triggers on phrases like 'review my memory', 'clean up memory', 'what do you
  remember about me', 'update your memory', or when the Self-Improvement Agent
  (SA-12) is performing a memory quality review. Also applies when new facts need
  to be stored in a structured, durable way."
license: MIT
allowed-tools: [memory_recall, memory_store, memory_forget]
compatibility: "Applies to EVA's memory system (scope-based key-value store)."
---

## Memory Curator

EVA's memory is only as useful as its quality. Stale, contradictory, or vague
entries create noise that degrades responses. This skill governs how to store,
review, and prune memory to keep it accurate and useful.

---

### Memory Architecture

EVA uses a **scope-based key-value store**. Each memory entry has:
- **Scope key:** A dot-separated namespace (e.g., `user.preferences`, `project.acme`)
- **Entry:** A structured fact or preference stored within that scope

**Scope naming conventions:**
| Scope prefix | Contents |
|--------------|----------|
| `user.*` | Long-term user preferences, working style, recurring instructions |
| `project.<name>.*` | Project-specific facts, conventions, contacts |
| `eva.self_review` | EVA's own performance notes (written by SA-12) |
| `session.*` | Short-lived facts (expire after session or task) |

---

### What Deserves to Be Remembered

**Store facts that are:**
- Durable: will still be true weeks from now
- Actionable: changes how EVA behaves in future interactions
- Non-obvious: not recoverable from the conversation itself

**Store these:**
- User's preferred coding language, framework, style
- Project conventions (test framework, commit style, deployment target)
- Recurring instructions ("always use type hints", "prefer async functions")
- Contacts and their roles within a project
- Decisions made that affect future work

**Do NOT store:**
- One-time task details with no future relevance
- Information already in the conversation context
- Sensitive data (passwords, API keys, personal IDs)
- Speculation or inferences presented as facts

---

### Storage Format — Structured and Specific

Memory entries must be specific enough to act on without context.

**Bad (too vague):**
```
"User likes Python"
"Working on a project"
"Prefers concise answers"
```

**Good (specific and actionable):**
```
"User's primary language is Python 3.12+. Prefers type hints, pathlib over os.path,
and dataclasses over dicts for structured data."

"Project: eva-local. Backend: FastAPI (Python), port 8000.
Test runner: pytest with pytest-asyncio. Commit style: Conventional Commits."

"User prefers concise technical explanations. Skip background/context unless asked.
Lead with the answer, not the reasoning."
```

---

### Fact Quality Criteria

Before storing a fact, verify:

- [ ] **Is it true?** Confirmed from the conversation, not inferred.
- [ ] **Is it durable?** Will this still be relevant in a week?
- [ ] **Is it specific?** Could EVA act on this without asking a follow-up?
- [ ] **Is it conflict-free?** Does it contradict an existing entry? If so, update or replace.
- [ ] **Is it safe?** No passwords, tokens, or PII.

---

### Conflict Resolution

When new information conflicts with stored memory:

**Rule: newer explicit information wins over older inference.**

1. If the user explicitly states a preference → overwrite the old entry.
2. If EVA inferred a preference and new evidence contradicts it → overwrite.
3. If two explicit statements conflict → store the more recent one; note the
   change: "Updated from: <old value>".
4. If the conflict is ambiguous → ask the user to clarify before updating.

---

### Memory Review Protocol

When reviewing memory quality (e.g., when running SA-12):

**Step 1 — Recall all scopes:**
```python
memory_recall(scope="user")
memory_recall(scope="project")
```

**Step 2 — Assess each entry against:**
- Is it still accurate? (flag entries older than 30 days for time-sensitive facts)
- Is it specific enough to be useful?
- Does it contradict another entry?
- Is it duplicated in multiple scopes?

**Step 3 — Produce a report with three lists:**
1. **Entries to update** — still valid but could be more specific
2. **Entries to verify** — may be stale; needs user confirmation
3. **Entries to prune** — clearly no longer relevant

**Step 4 — Act only with user confirmation.**
Do not delete or modify memory entries without explicit instruction.

---

### Pruning Rules

| Entry type | Retention |
|------------|-----------|
| Active project conventions | Keep while project is active |
| Completed task context | Prune after task is done (30 days max) |
| User preferences | Keep indefinitely (update on change) |
| Inferred facts | Prune if contradicted or unconfirmed after 60 days |
| Session-scoped facts | Prune at end of session |

---

### Anti-Patterns to Avoid

**Over-storing:** Storing every sentence from every conversation pollutes
memory with noise that drowns out useful facts. Be selective.

**Under-specifying:** "User works on a web project" is not actionable.
"User's primary project is eva-local: FastAPI backend, React/TypeScript
frontend, Groq as the default LLM provider" is.

**Stale facts presented as current:** When recalling memory, note the
recency of stored facts. Prefix with "As of [date stored]..." for facts
that may have changed.

**Sensitive data storage:** Never store passwords, API keys, credit card
numbers, or government ID numbers in memory. If the user provides these,
use them for the immediate task only — do not persist.
