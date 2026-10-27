# Creating and Managing a Project

This page walks through creating a project, assigning conversations to it, navigating its sections, and managing its files, knowledge, and policies.

## Creating a project

Open the workspace panel and switch to the **Projects** tab. Click **New project** to open the creation form.

**Name** — used throughout the interface and included in the context block that EVA reads at the start of every execution. Use something specific — "Q3 Investor Deck" rather than "Work stuff".

**Synopsis** — a one-line description of the project's intent. This is the first thing EVA reads when entering a project conversation and sets the frame for everything that follows.

**Description** (optional) — a longer statement of purpose, scope, or background. Included in `project.eva`.

**Workspace** (optional) — by default, EVA creates a managed workspace for the project under its own storage directory. If you want EVA to work inside an existing directory — a codebase you have already checked out, a folder where your documents live — supply the absolute path to that directory instead. EVA will create a `.eva/` subdirectory inside it and leave the rest of the directory as it is.

Creating the project writes six skeleton `.eva` files in the workspace directory and registers the project in the database. The project starts in **Active** status.

A background sync also starts immediately — EVA begins exploring the workspace and writes an initial `knowledge.eva` entry without you needing to send a message. See [What are Projects?](01-what-are-projects.md#automatic-project-sync) for what this does and how to re-trigger it later.

---

## Assigning conversations to a project

Any new conversation you start from within the Projects tab, or from the project's Conversations section, is automatically linked to that project.

For existing standalone conversations, right-click the conversation in the Chat tab recents list and select **Promote to project**. A picker shows all active projects. Selecting one links the conversation and moves it to that project's conversation list. Future messages in that conversation will have the project's context injected.

---

## The project browser

Clicking a project in the Projects tab expands its section tree. Nine sections are available:

| Section | What it shows |
|---------|---------------|
| Overview | Metrics: executions, tokens, cost, success rate, provider breakdown, daemon health, open task count |
| Conversations | All conversations linked to this project |
| Tasks | Contents of `tasks.eva` — tasks with their current status |
| Files | Workspace files created by EVA or uploaded by you |
| Knowledge | Documents indexed for semantic search in this project |
| Agents | Subagents scoped to this project |
| Daemons | Background agents configured for this project |
| MCP | MCP servers scoped to this project — manage, enable, disable, and check health here |
| Errors | Contents of `errors.eva` — open and resolved error entries |
| Policies | Contents of `policies.eva` — DOS, DONTS, and ASK_FIRST rules |

Badge counts on Conversations, Files, Knowledge, Agents, and Daemons update live as you add or remove items.

---

## Syncing a project

Every project's background sync (see [What are Projects?](01-what-are-projects.md#automatic-project-sync)) runs automatically at creation and can be re-triggered any time from two places:

- **Project header** — click **Sync** next to Pause/Resume/Archive on an active project's detail view.
- **Sidebar row** — hover a project in the Projects tab and click the sync icon.

Both trigger the same background process and show a live "Syncing…" state until it finishes. Useful after editing an external workspace outside EVA — a `git pull`, a manual file change — so EVA's knowledge of the project reflects what's actually there now. Only available on active projects, matching Pause/Resume/Archive's own visibility rule.

---

## Project metrics

The Overview section of a project shows a consolidated metrics view pulled from all executions that ran in its context:

- **Executions** — total count, successful, failed, cancelled, success rate, and breakdown by trigger type (conversation vs daemon)
- **Tokens** — prompt, completion, and reasoning token totals
- **Cost** — cumulative cost in USD, with a breakdown by provider and model
- **Duration** — total time spent in execution across the project's lifetime
- **Daemon health** — count of healthy, degraded, and failed daemons
- **Open tasks** — count of PENDING, ACTIVE, and BLOCKED tasks from `tasks.eva`

---

## Files and knowledge

Projects have two distinct types of file storage that serve different purposes.

**Knowledge** — documents you want EVA to be able to search. Upload a document to Knowledge and it is vector-indexed into the project's knowledge scope. When EVA needs to find information in it, it searches semantically rather than requiring the whole document to be in context. Supported formats include PDF, DOCX, CSV, plain text, Markdown, JSON, YAML, source code, and other text-extractable formats. Indexing is asynchronous — after uploading, you can monitor ingestion progress. Binary formats such as images and archives cannot be knowledge-indexed; use Files for those.

**Files** — everything else. Images, archives, binary blobs, data files, documents you want EVA to read in full rather than search. Files are stored in the workspace and accessible to EVA's file tools. Uploading is synchronous and immediate.

When EVA creates output files during task execution — generated code, reports, exports — they also appear in the Files section, tagged as workspace files.

**Very large projects.** If a project's workspace is a real codebase, the Files tab lists everything in it, including folders such as `.git` and `node_modules`. Opening it doesn't make the rest of EVA wait, but a huge listing can take a while. If the workspace holds more than 50,000 files, the tab shows the first 50,000 (in path order) with a banner saying the list was cut short. Knowledge and uploaded files are never cut. The limit is adjustable — see the [Configuration Reference](../15-configuration/01-configuration-reference.md#projects).

To delete a knowledge file, EVA removes both the vector index entries and the stored document. To delete a workspace file, it removes the file from the workspace path.

---

## Setting project policies through conversation

Policies are stored in `policies.eva`, but you do not have to edit the file directly. You can tell EVA in conversation to add, change, or remove a rule:

> *"From now on, always run the test suite before committing on this project."*
> *"Never install new dependencies without asking me first."*
> *"If a refactor touches more than three files, surface a plan before making changes."*

EVA recognises requests to modify project policies and applies them by rewriting the relevant section of `policies.eva`. The updated rule takes effect from the next execution.

---

> **Next:** [How EVA Remembers You](../06-memory/01-how-eva-remembers-you.md) — EVA's long-term memory system, separate from project context.
