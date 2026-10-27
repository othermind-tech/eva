---
name: project-context
description: "Use at the start of any task involving an unfamiliar project or
  repository — of any kind, not just code. Triggers when the user says
  'understand this project', 'get familiar with the repo', 'what is this
  project', or when you need existing conventions or context before making
  changes. Also use before any multi-file edit to ensure you follow existing
  patterns."
license: MIT
allowed-tools: [read_file, list_files, stat_file, search_files, rag_retrieve, rag_ingest, project_artifact_read, project_artifact_write]
compatibility: "Works in all EVA environments with filesystem access."
---

## Project Context Skill

Before working in an unfamiliar project, systematically build context to
avoid breaking conventions, duplicating existing work, or making changes
that conflict with what's already there. A project can be anything — a
codebase, a collection of planning documents, a GTM playbook, a research
dataset, a design archive, or (very commonly) a mix of several of these at
once. Don't assume it's a codebase until you've actually looked.

---

### Phase 0 — Start with what's already known

Before exploring anything, check whether you already have a head start:

- If this conversation is linked to a project, its description/synopsis
  (if the user provided one when the project was created) is already in
  your context above — treat it as a useful hint about what to expect, not
  as ground truth. It may be partial, stale, or absent entirely; your job
  is to verify and fill in the rest, not to re-derive it from zero when
  it's already been told to you.
- Do one quick top-level listing (`list_files` on the project root).
  Skim the file extensions and folder names present. This is just a first,
  revisable impression — enough to guess roughly what kind of content
  you're dealing with before deciding how to explore it further. Be ready
  to update that impression as you read more.

---

### Phase 1 — Folder Structure (always run, regardless of content type)

Map the top-level layout — what lives in each top-level directory. This is
useful no matter what kind of project this turns out to be.

Real projects are very often **mixed**, not one clean category — for
example, a single repository might contain source code, tests,
documentation, planning notes, and research data all side by side (this is
true of EVA's own repository, for instance). Don't force everything into
one bucket. Note each meaningfully-sized part of the project and cover it
proportionally in what follows, rather than picking a single label for the
whole thing.

---

### Phase 2 — Understand the Content

The patterns below are common starting points, not an exhaustive list and
not a form to fill in. If what you find doesn't match any of them, use
your own judgment — the underlying approach is always the same: figure out
what something is, how it's organized, and what's actually worth knowing
about it. These examples exist to save you from starting that reasoning
from a blank page for the common cases; they aren't a ceiling on what
you're allowed to notice.

**If there's source code:**
- Check manifest/config files (`package.json`, `pyproject.toml`,
  `Cargo.toml`, `go.mod`, etc.) for language, framework, and key
  dependencies.
- Find the entry point(s) and how the code is organized (by layer, by
  feature, by domain?).
- Sample 2-3 existing files to pick up naming conventions, import
  ordering, error-handling style, and logging approach.
- Find how tests are run (`pytest`, `npm test`, `go test`, `cargo test`,
  etc.) and how they're organized (unit/integration/e2e, fixtures,
  CI setup).

**If there's substantial written content** (reports, playbooks, policies,
design docs, notes, a wiki):
- What's the subject/domain, and who is it apparently written for?
- How is it organized — by date, by team, by topic, by project phase?
- Is there an index, table of contents, or "source of truth" document that
  supersedes or summarizes the rest?
- Is there a versioning or draft-vs-final convention? Any domain-specific
  terminology worth knowing before discussing this content?

**If there's data:**
- What datasets exist, in what format, and roughly how large?
- What tools or notebooks are used to work with it?
- Is there a raw vs. processed distinction? Any documented findings or
  reports already derived from it?

**Binary or non-text content** (images, PDFs, spreadsheets, slide decks,
etc.): you often can't read the content directly. That's fine — their
presence, filenames, and where they're organized is still real signal
about what the project contains, and is worth noting even without reading
inside them.

**Anything else, or a genuine mix of the above**: apply the same
questions — what is it, how is it organized, what matters about it — to
whatever you actually find. Don't wait for a category that fits perfectly.

**If a file is too large to fully contextualize** (a long document, an
extensive research archive, a large data file) — don't try to compress it
all into a short summary. Use the `rag_ingest` tool to index it into the
RAG knowledge base instead, and record in `knowledge.eva` that it was
ingested and roughly what it contains, so a later `rag_retrieve` call can
find and pull the relevant details back on demand.

---

### Phase 3 — Context Summary

After completing phases 0–2, record what you found.

Call `project_artifact_read(file="knowledge")` to see what's already
recorded before writing anything — this may be a repeat visit to a project
you've already captured context for, not a first pass, and you can't tell
which without checking. (Lines starting with `#` in the result are just
the format legend, not recorded facts — if that's all there is, there's
nothing to reconcile against yet.)

Then call `project_artifact_write`:
- If the current content already covers this fact in some form, use
  `action="edit"` (`old_string`/`new_string`) to update it in place — do
  not `action="append"` a restatement of something already there, that
  just accumulates duplicate overview entries over repeated visits.
- Otherwise use `action="append"` with the summary below.

That tool call is the deliverable — this is a durable-memory operation.
Writing the summary out as your final text response is not a substitute
for calling the tool: nothing is saved from text alone. Call the tool.
Do not say or imply that you recorded, saved, or updated anything unless
you actually made the `project_artifact_write` call — a false claim here
is worse than no claim, since nothing will ever correct it afterward.

If `project_artifact_write` returns "No active project in this execution
context," there's nothing to persist into — this conversation isn't linked
to a project. That's fine; just continue normally, using what you found to
inform the actual task at hand.

If the current recorded content is very large (a long-running project can
accumulate a lot), use judgment rather than either ignoring it or stalling
on it — focus on the most relevant recent entries, use `search_files` to
look for a specific fact instead of reading everything, or delegate a
summarization pass if it's genuinely too much to reason about directly.

```
## Project: [name]
**Type:** [code / written content / data / mixed — be specific about the mix]
**Purpose:** [1-2 sentences — use the existing description/synopsis as a
  starting point if one was provided, refined by what you actually found]
**Content overview:** [stack & frameworks if code; domain & document types
  if written content; datasets & tools if data — cover each part that's
  actually present]
**Folder structure:** [top-level layout, what lives where]
**Key files/documents:** [the few things most worth knowing about]
**Conventions/patterns:** [coding style, or naming/organizational patterns
  — whichever actually applies]
**How to engage:** [run/test commands for code; where the canonical
  reference lives for written content; how the data's structured, for data]
**Watch out for:**
- [gotcha 1]
- [gotcha 2]
```

This becomes a durable project fact the moment it's recorded via
`project_artifact_write` — not something anyone has to rediscover next
time.

---

### Operating Rules

- **Read before writing.** Never edit a file you haven't read.
- **Follow, don't impose.** Match the existing style or organization even
  if you'd personally do it differently. Consistency beats correctness in
  a shared project.
- **Ask when uncertain.** If conventions or organization are ambiguous,
  ask the user rather than guessing — a wrong assumption compounds.
- **Update context incrementally.** When you discover something new, add
  it to your working context summary.
