---
name: adr-writer
description: "Use when a significant architectural or design decision needs to be
  recorded. Triggers on phrases like 'write an ADR', 'document this decision',
  'record the architecture decision', or after completing a design decision that
  has lasting consequences. Also useful when revisiting old decisions to understand
  their rationale."
license: MIT
allowed-tools: [read_file, write_file, shell]
compatibility: "Language and framework agnostic. Produces Markdown ADRs."
---

## ADR Writer

An Architecture Decision Record (ADR) captures a significant architectural or
design decision: what was decided, why, and what the consequences are.

---

### When to Write an ADR

Write an ADR when a decision:
- Has lasting consequences that are hard to reverse
- Involves a non-obvious trade-off between alternatives
- Will be questioned by future contributors unfamiliar with the context
- Changes a previously documented approach

**Do NOT write an ADR for:**
- Routine implementation choices (which variable name, which loop style)
- Trivial dependency upgrades with no architectural impact
- Decisions already obvious from the code itself

---

### ADR Template

```markdown
# ADR-NNN: <Short Title in Imperative Mood>

**Date:** YYYY-MM-DD
**Status:** Proposed | Accepted | Deprecated | Superseded by ADR-NNN
**Deciders:** <names or roles>

---

## Context

<1–3 paragraphs explaining the problem or situation that forces a decision.
Include: current state, constraints, relevant forces (performance, cost,
team skills, timeline). This section answers: "Why did we need to make a
decision at all?">

## Decision

<A clear, direct statement of what was decided.
Start with: "We will..." or "We decided to...">

## Alternatives Considered

### Option A — <name>
**Pros:** ...
**Cons:** ...

### Option B — <name>
**Pros:** ...
**Cons:** ...

### Option C — <name> (chosen)
**Pros:** ...
**Cons:** ...

## Consequences

**Positive:**
- <outcome>

**Negative / Trade-offs:**
- <trade-off or known downside>

**Risks:**
- <risk and mitigation>

## Notes

<Optional: links to discussions, PRs, or related ADRs>
```

---

### Numbering and Storage

**Numbering:** Sequential integers, zero-padded to 4 digits: `ADR-0001`, `ADR-0042`.

**Storage location:** `docs/decisions/` (preferred) or `adr/` at repo root.

**File naming:** `NNN-short-title-in-kebab-case.md`
- `docs/decisions/0001-use-fastapi-for-rest-api.md`
- `docs/decisions/0042-progressive-tool-disclosure.md`

**Status field values:**
- `Proposed` — under discussion, not yet in effect
- `Accepted` — in effect; this is the current approach
- `Deprecated` — no longer recommended but not yet removed
- `Superseded by ADR-NNN` — replaced by a newer decision

---

### Writing the Context Section

The Context section is the hardest and most important part. It must answer:
1. What is the current situation?
2. What forces or constraints apply?
3. What problem does the decision address?

**Bad context (too vague):**
> We needed to pick a framework.

**Good context:**
> EVA's tool execution layer dispatches tool calls synchronously. As the number
> of concurrent users grows, blocking tool calls (especially web search, shell
> commands) delay all other executions. We need an approach that allows tool
> calls to run concurrently without blocking the ReAct loop.
> Constraints: must work with Python's asyncio, cannot change the existing
> streaming SSE interface, must preserve execution ordering guarantees.

---

### Writing the Decision Section

State the decision directly, not the discussion:

**Bad:**
> After evaluating several options, we decided that asyncio was probably the
> best choice given the constraints.

**Good:**
> We will execute tool calls using Python's asyncio with `asyncio.gather()` for
> independent concurrent tool calls, maintaining sequential ordering for tool
> calls that depend on each other's outputs.

---

### Updating Existing ADRs

- Never edit the original ADR to change the decision — create a new ADR.
- Update the `Status` field of the old ADR to `Superseded by ADR-NNN`.
- Link the new ADR in the old ADR's Notes section.

This preserves the full decision history, including why earlier approaches were
chosen and why they were later changed.
