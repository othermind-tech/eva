# What are Projects?

A project is a persistent workspace for a defined area of work. It is where EVA accumulates memory specific to that work — decisions made, tasks tracked, errors encountered, knowledge discovered — and carries all of it forward into every subsequent conversation on that project.

The practical effect is that EVA starts each new conversation inside a project already knowing the goal, the constraints, what has been tried, and what you have asked it to always and never do. You do not re-explain context. You just continue working.

---

## Project status

A project moves through three statuses in a defined sequence. The transitions are enforced — you must pause a project before you can archive it.

```
Active  →  Pause  →  Paused  →  Archive  →  Archived
                      Resume ↓
                      ←←←←←←←
```

**Active** — the project is in progress. EVA participates in project conversations, agents run, daemons execute. The only action available is **Pause**.

**Paused** — the project is on hold. All daemons associated with the project are suspended. Project-scoped MCP server connections are paused. The project record and all its files remain intact; nothing is deleted. Two actions are available: **Resume** (returns the project to Active, restarting daemons and MCP connections) and **Archive**.

**Archived** — a permanent terminal state. The project data is preserved and readable, but conversations are read-only, daemons cannot run, and MCP servers remain suspended. Archived projects are hidden from the main project list and appear only under the Archived filter. There is no unarchive action — if you need to restart work on an archived project, create a new project instead. No action buttons are shown on an archived project.

Multiple projects can be active simultaneously — there is no restriction on how many you can run in parallel.

---

## When to use a project

**Use a project** for:
- Work that spans multiple sessions — a feature, a research study, a product launch
- Anything with recurring context — a specific codebase, a recurring client, a domain you return to often
- Anything where consistent behaviour matters — coding conventions, brand guidelines, policies about what EVA should and should not do
- Work that involves multiple agents or background daemons

**Use a standalone conversation** for:
- One-off questions with no ongoing context
- Quick tasks that don't need persistent state
- Exploratory work you haven't committed to yet

> *A standalone conversation is like a whiteboard — immediate, disposable. A project is like a proper workspace — it remembers what was here before you left.*

---

## What a project contains

Every project is a container for the things that belong to a piece of work:

- **Conversations** — all the threads that happen in the context of this project
- **Agents** — subagents scoped to this project that EVA can delegate to during execution
- **Daemons** — background agents that run on a schedule for this project
- **MCP servers** — external tool servers scoped to this project, available only within it
- **Workspace files** — files stored in the project workspace that EVA can read and write
- **Knowledge** — documents indexed for semantic search, specific to this project

Project-scoped agents, daemons, and MCP servers are only visible inside the project browser — they do not appear in the global Subagents, Daemon Agents, or MCP Servers panels.

And underlying all of it: a set of six artifact files that EVA maintains on your behalf.

---

## The six artifact files

Every project has a `.eva/` directory inside its workspace. EVA maintains six files there, each with a specific role.

**`project.eva`** — the project record. Name, description, goal, scope, constraints, environment variables. This is the first thing EVA reads when entering a project conversation.

**`policies.eva`** — behavioural rules specific to this project, organised into three sections:

- *DOS* — things EVA must always do (run tests before committing, follow conventional commits format)
- *DONTS* — hard prohibitions (never push directly to main, never hardcode secrets)
- *ASK_FIRST* — situations where EVA must pause and confirm before acting (any change touching auth code, refactors affecting more than 3 files)

Policies are injected into every execution on this project regardless of context tier — they are always enforced.

**`tasks.eva`** — the project task list. Tasks carry a status (PENDING, ACTIVE, DONE, or BLOCKED) and a timestamp. EVA keeps this current: as work progresses and conclusions are reached in conversations, task statuses update automatically.

**`knowledge.eva`** — accumulated knowledge entries, each tagged with a type. CODE, DECISION, REFERENCE, TEST, and NOTE are the common examples, but the type isn't a fixed list — EVA picks whatever label best fits the project's actual content (a research archive or a marketing repo gets different, more relevant labels than a codebase would), staying consistent with whatever labels already appear in that project's own file. EVA adds entries here when meaningful things emerge from a conversation.

**`journal.eva`** — an append-only log. EVA writes one entry after every execution turn summarising what happened. The journal is never edited — only extended.

**`errors.eva`** — an error log. When EVA encounters a meaningful error during execution, it logs it here with status `open`. When the error is resolved, the status updates to `resolved`. This gives you and EVA a running record of what has broken and what was done about it.

### How the artifact files stay current

After every execution turn, EVA reviews what happened in the conversation and produces structured updates to the artifact files — up to five changes per turn. Task statuses advance as work completes. Knowledge entries are added when decisions or facts emerge. Errors are logged when they occur and marked resolved when they are fixed. Policies are only updated when you explicitly ask to add, change, or remove a rule. EVA can also read and write these files directly through tool calls when it needs to — automatic project sync, described below, is one example — not only through this per-turn review.

---

## Automatic project sync

New projects don't start with empty knowledge. EVA begins exploring the workspace in the background the moment a project is created — whether it's a managed workspace or points at an existing external directory, and whether it holds code, documents, or anything else. It reads what's there, works out what kind of project it actually is, and writes an initial `knowledge.eva` entry summarising the goal, structure, and how to work with it — before you've sent a single message.

This runs as a background process dedicated to the project. It's invisible in the Daemon Agents panel and never fires on its own schedule — only at project creation, or when you trigger it manually.

**Sync now** — click the sync button in the project's header, or the matching icon next to a project in the sidebar, to re-run this exploration on demand. This is useful after you've changed an external workspace outside EVA — a `git pull`, a manual edit — and want EVA's knowledge of it refreshed without waiting for a real conversation to surface the change.

Repeated syncs don't pile up duplicate entries. EVA checks what's already recorded before writing and updates it in place when a sync finds the same information again, rather than appending a near-identical entry every time.

---

## How project context enters your conversations

When you start a conversation inside a project, EVA injects a context block into its system prompt drawn from the artifact files. The amount of context injected depends on the context tier:

| Tier | What's included | Approx. tokens |
|------|-----------------|---------------|
| Compact | `project.eva` + `policies.eva` | ~300 |
| Focused | Compact + `tasks.eva` | ~600 |
| Full | All six files | ~1,500 |

EVA selects the tier automatically based on available context budget. `policies.eva` is always included — project rules apply from the first message, every time.

---

> **Next:** [Creating and Managing a Project](02-creating-a-project.md) — setting up a project, uploading knowledge, and managing its lifecycle.
