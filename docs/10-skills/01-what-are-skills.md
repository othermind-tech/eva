# What are Skills?

A skill is a structured instruction document that tells EVA how to handle a particular kind of task. When EVA recognises that your request matches a skill's purpose, it reads the skill's procedure and follows it — instead of improvising from general knowledge.

Skills are how you give EVA a consistent, repeatable methodology for recurring work: a code review approach that always checks security first, a research process that always evaluates sources, a commit message style that always follows your team's conventions.

---

## How EVA uses skills

Every skill is a directory on disk containing at minimum a `SKILL.md` file. EVA loads all your skills at startup and builds a catalogue from their names and descriptions. That catalogue is injected into the agent's system prompt as a compact list — just name, path, and description per skill. A skill can be told to appear only when it's likely to be relevant, using a [trigger](#scoping-a-skill-with-a-trigger); a skill without one is always listed.

When you send a message, the agent reads this catalogue and decides whether any skill is relevant. If one is, it reads the full `SKILL.md` before responding. If none match, the skills have zero runtime cost — they're invisible until needed.

This is **progressive disclosure**: the catalogue is always present, but the full procedure content is only loaded when the agent determines it's relevant to the current task. No skill content is held in context unless the agent actively uses it.

---

## Controlling when EVA uses skills

By default EVA decides for itself whether any skill is relevant to your request. You can override this with the **Skill mode** setting, which controls whether the skills catalogue is injected into the system prompt at all.

| Mode | What it does |
|------|-------------|
| **Auto** (default) | EVA receives the catalogue and decides independently whether to load a skill. Most tasks that don't match any skill description run without any skill overhead. |
| **Enabled** | Same injection behaviour as Auto — explicitly opts in. Useful when you want to make clear that skills should always be considered, even if Auto would already do the same thing. |
| **Disabled** | The skills catalogue is never injected. Skills have zero token cost and EVA runs on pure model behaviour. Use this when you want predictable, unshaped responses or are debugging a prompt. |

**Where to change it:**

- **Modes button** in the chat toolbar (the button that shows the current mode summary) — opens a quick popover with a three-way segmented control labelled Auto / Enabled / Disabled under the "Skill" row.
- **Session Panel** → "Skill mode" — the same three-way control, persistent in the panel alongside memory and agent settings.

Changes take effect on the next message sent. The setting is stored in your session config and persists across conversations until you change it.

---

## The SKILL.md format

Every skill begins with a YAML frontmatter block delimited by `---`, followed by a markdown body containing the actual instructions.

```
---
name: code-reviewer
description: "Use when asked to review code for quality, bugs, or security. Load
  this skill for PR reviews, refactoring guidance, or any request that includes:
  review this code, check for bugs, security audit, code quality, find issues."
license: MIT
allowed-tools: [bash, files]
compatibility: "Works in all EVA environments"
---

## Code Review Methodology

You are an expert code reviewer. When reviewing code, follow this structured
four-phase approach...
```

### Frontmatter fields

| Field | Required | What it does |
|-------|----------|-------------|
| `name` | ✅ | Unique identifier. Kebab-case, 2–64 characters (`a-z`, `0-9`, `-`). Cannot be changed after creation. |
| `description` | ✅ | What this skill is for and when to use it. Max 1024 characters, no `<` or `>`. **This is what the agent reads to decide if the skill is relevant — it must be specific.** |
| `allowed-tools` | — | Whitelist of tools the agent may use when this skill is active. Leave empty to inherit all enabled tools. Example: `[bash, files, web_search]` |
| `license` | — | License identifier (e.g. `MIT`). Informational only. |
| `compatibility` | — | Free text describing environment requirements. Shown in the skill card. |
| `metadata` | — | Arbitrary key-value pairs for your own use. Not interpreted by EVA. |
| `trigger` | — | Limits when the skill is offered to the agent. Without it, the skill is always in the catalogue. See below. |

### Scoping a skill with a trigger

As your skill library grows, listing every skill on every message costs tokens and adds noise. A `trigger` block tells EVA to list a skill only when it looks relevant:

```
trigger:
  type: keyword
  keywords: [browser, localhost, dev server, console]
```

With a keyword trigger, the skill appears in the catalogue for a message only if that message contains one of the keywords. A message about a database migration never sees a browser-testing skill in its catalogue at all. Matching is:

- **Whole words, not substrings** — `git` matches "use git" but not "github".
- **Case-insensitive.**
- **Phrase-aware** — `dev server` matches that exact phrase.
- **Slash commands work** — a keyword like `/deploy` matches when the message contains it.

A few rules of thumb:

- **Choose the words you'd actually type**, and include the variations (`webpage`, `web page`, `website`). A trigger that never matches makes the skill effectively invisible.
- **Skills with no trigger are unaffected** — they behave exactly as before, so you can add triggers only where a skill is narrow.
- **It applies to subagents too.** When EVA delegates a task, the skill is matched against the task text.
- **A trigger only decides when the skill is *offered*.** The agent still reads the full skill only when it decides it's needed.

`type: task` works the same way, with its list under `triggers:` instead of `keywords:`. A `type: path` trigger (file patterns such as `*.sql`) is accepted but isn't active yet — EVA has no way to match it during a conversation today — so a path-triggered skill is never listed. If a trigger block is malformed or has an unknown type, EVA ignores the block and lists the skill as if it had none, rather than failing to load it.

### The body

Everything after the closing `---` is the skill's body — the actual procedure the agent follows. Write it as you would any detailed instruction: use headings to separate phases, use checklists for step-by-step processes, use code blocks for examples. The agent reads it verbatim and follows it.

---

## Single-file vs multi-file skills

**Single-file skills** are a single `SKILL.md` with the full procedure in the body. Use this for focused, self-contained tasks: a commit message convention, a specific output format, a short checklist.

**Multi-file skills** are a directory that contains `SKILL.md` plus supporting files organised into subdirectories. The `SKILL.md` body can reference these files — the agent reads them using the file-read tool as part of the skill execution.

A multi-file skill directory typically looks like this:

```
sdlc-orchestrator/
  SKILL.md               ← entry point, references the phase files
  phases/
    00-preflight.md
    01-planning.md
    02-implementation.md
    03-testing.md
    04-review.md
    05-commit-and-pr.md
  checklists/
    definition-of-done.md
  templates/
    final-report.md
```

The `SKILL.md` body instructs the agent to read each phase file at the appropriate stage. This keeps individual files focused and makes the skill easy to maintain — you can update a single phase without touching the others.

Use multi-file skills for complex orchestration workflows, long procedures that would be unwieldy in a single file, or skills that need reusable templates and checklists.

---

## Writing effective skill descriptions

The description is the most important field in the frontmatter. It is the only part the agent reads before deciding whether to load the skill — the body content is only fetched afterwards.

An effective description includes:
- **What the skill does** — one sentence on its purpose
- **When to use it** — specific scenarios or task types
- **Trigger phrases** — words and patterns that signal a match

Compare:

**Too vague — agent may not load it:**
```
description: "A code review skill."
```

**Effective — agent reliably recognises it:**
```
description: "Use when asked to review code for quality, bugs, security
  vulnerabilities, or style issues. Load this skill for PR reviews,
  refactoring guidance, code quality analysis, or any request that
  includes: review this code, check for bugs, security audit, code
  quality, refactor, find issues."
```

Keep the description under 1024 characters. Avoid `<` or `>` — these are rejected by the parser.

---

## Default skills

EVA ships 16 skills that are copied into your workspace on first launch (new ones added in later releases are added to existing accounts automatically, without touching anything you've edited). They cover common software development workflows and can be edited freely — your edits are never overwritten by EVA updates.

| Skill | Purpose |
|-------|---------|
| `code-reviewer` | Four-phase code review: security → correctness → quality → performance |
| `web-search-methodology` | Structured research: decompose → search → evaluate → synthesise → cite |
| `python-best-practices` | Module structure, type annotations, docstrings, error handling, tooling |
| `python-helper` | Debugging, advanced patterns, async, profiling |
| `typescript-react-patterns` | Component patterns, hooks, state management, typing |
| `sdlc-orchestrator` | Full feature implementation: planning → coding → testing → review → commit |
| `commit-message-conventions` | Conventional Commits format with type/scope/body rules |
| `pr-description-writer` | Pull request descriptions with context, changes, and test plan |
| `adr-writer` | Architecture Decision Record (ADR) template and process |
| `security-checklist` | OWASP-aligned security review checklist |
| `testing-conventions` | Test naming, fixture patterns, mocking boundaries |
| `memory-curator` | Memory review, quality criteria, conflict resolution, pruning rules |
| `project-context` | Explore an unfamiliar project of any kind — code, documents, data — and record what it is and how it's organised. Used by the automatic [project sync](../05-projects/01-what-are-projects.md#automatic-project-sync) |
| `playwright-browser-testing` | Look at a web page in a real browser, read console errors and failed requests, fix the code and confirm the fix. Needs the [Playwright MCP](../12-mcp/01-what-are-mcp-servers.md#default-mcp-browser-automation) turned on. Uses a keyword trigger, so it only appears for browser-related messages |
| `agent-builder` | Creates, edits and deletes subagents through conversation — gathers requirements, shows a draft, saves only after you confirm |
| `daemon-builder` | The same, for daemons |

The `sdlc-orchestrator` is a multi-file skill — it has phase files, checklists, and templates in subdirectories. The rest are single-file skills.

---

## Skills vs custom agents

A skill is a procedure the agent follows for a task. A custom agent is a distinct reasoning entity with its own system prompt, model, and tool set.

Use a skill when you want to standardise *how* EVA approaches a recurring task within a normal conversation. Use a custom agent when you want a specialised entity that the orchestrator can delegate to independently — one with a different identity, tool scope, or model.

A skill can reference subagent delegation in its body (as `sdlc-orchestrator` does). An agent can also have skills available to it just as the main EVA agent does.

---

> **Next:** [Managing Your Skill Library](02-managing-your-skill-library.md) — creating, installing, editing, and organising your skills.
