# EVA vs Competitors

*Last checked 22 October 2026, against each vendor's public documentation and announcements (sources listed at the bottom). This is a fast-moving space — products change monthly, so re-verify anything that matters to your decision.*

---

## How to read this page

This page is opinionated, and it says so. EVA's authors wrote it, so it leans toward what EVA does well. To keep it honest:

- Every competitor claim comes from a source listed below, and the date above is when we last checked.
- Where we couldn't confirm something from an official source, the cell says **not confirmed** rather than guessing in either direction.
- A section near the end lists where other products are ahead of EVA.

The AI assistant landscape changed substantially between late 2025 and late 2026. Products that were once simple chat boxes now run background agents, schedule tasks, use a computer for you, and coordinate parallel agents. EVA is not the only option, and for some jobs it isn't the best one.

---

## Products compared

| Product | What it primarily is | Made by |
|---------|---------------------|---------|
| **EVA** | Provider-agnostic, general-purpose AI agent that runs as a desktop app | Independent |
| **Claude Cowork** | Anthropic's agentic work product for knowledge work, built on the Claude Code architecture | Anthropic |
| **ChatGPT (Work)** | OpenAI's assistant with longer multi-step tasks and scheduled tasks. It replaced the earlier "ChatGPT agent" | OpenAI |
| **Perplexity (incl. Computer)** | Answer and research engine, now with an agentic "Computer" | Perplexity AI |
| **Cursor 3** | AI coding environment built around parallel agents | Anysphere |
| **GitHub Copilot** | AI coding assistant, coding agent and CLI | GitHub / Microsoft |
| **Gemini Spark** | Always-on personal agent tied to Google Workspace | Google |

---

## Feature comparison

✅ Documented · 🟡 Partial, limited, or depends on plan/setup · ❌ Not offered, per the source · ❔ Not confirmed from an official source

### Where it runs and who owns the data

| | EVA | Claude Cowork | ChatGPT (Work) | Perplexity | Cursor 3 | GitHub Copilot | Gemini Spark |
|---|-----|--------------|----------------|------------|----------|----------------|--------------|
| Where work runs | Your machine | Anthropic's cloud; local files through the desktop app | OpenAI's cloud | Perplexity's cloud | Local, worktrees, cloud and SSH | Your editor, CLI, or GitHub's cloud | Google Cloud VMs |
| Conversations and memory stored | Locally, in your workspace and database | Vendor account | Vendor account | Vendor account | Vendor account; code stays in your repo | Vendor account; code stays in your repo | Vendor account |
| Platforms | Windows 10/11 (64-bit) only | macOS, Windows (desktop app) | Web and apps | Web and apps | Desktop app | Editors, CLI, GitHub | Google surfaces; beta for AI Ultra subscribers |
| Open source | ❌ Not yet. A public repository is planned | ❔ | ❔ | ❔ | ❔ | ❔ | ❔ |

### Agents and automation

| | EVA | Claude Cowork | ChatGPT (Work) | Perplexity | Cursor 3 | GitHub Copilot | Gemini Spark |
|---|-----|--------------|----------------|------------|----------|----------------|--------------|
| Background work | ✅ Daemons, while EVA is running | ✅ Cloud | ✅ | 🟡 In Computer | ✅ Cloud agents | ✅ Coding agent (code only) | ✅ Cloud, 24/7 |
| Scheduled tasks | ✅ | ✅ `/schedule`, runs in the cloud | ✅ Scheduled Tasks | 🟡 Documented under Computer | ❔ | ❔ | ❔ Not described |
| Event or webhook triggers | ✅ Internal events and public webhooks | ❔ | 🟡 "Schedule or trigger" — details not confirmed | ❔ | ❔ | 🟡 GitHub events | ❔ Email address as a way in |
| Parallel / multi-agent | ✅ 13 built-in agents plus your own | ❔ | ❔ | ❔ | ✅ Many agents in parallel | ✅ `/fleet` | ❔ |
| Structured plan → execute → verify | ✅ With adaptive re-planning | ❔ | ❔ | ❔ | ❔ | ❔ | ❔ |

### Models and providers

| | EVA | Claude Cowork | ChatGPT (Work) | Perplexity | Cursor 3 | GitHub Copilot | Gemini Spark |
|---|-----|--------------|----------------|------------|----------|----------------|--------------|
| Bring your own provider or key | ✅ 18 providers | ❌ Claude models | ❌ OpenAI models | ❌ Perplexity's own selection | 🟡 Custom keys, with limits on agent mode (third-party report) | 🟡 Bring-your-own-key in VS Code chat | ❌ Gemini models |
| Local models | ✅ Ollama | ❌ | ❌ | ❌ | ❔ | 🟡 Via the Ollama extension in VS Code | ❌ |
| Automatic fallback to another provider | ✅ | None found | None found | None found | None found | None found | None found |

### Tools, approvals and extensibility

| | EVA | Claude Cowork | ChatGPT (Work) | Perplexity | Cursor 3 | GitHub Copilot | Gemini Spark |
|---|-----|--------------|----------------|------------|----------|----------------|--------------|
| MCP servers | ✅ | ✅ | 🟡 Developer mode (beta, paid plans) | 🟡 Remote connectors on paid plans; local MCP on macOS | ✅ stdio, SSE, streamable HTTP | ✅ Tools only; no OAuth remote servers in the coding agent | ✅ |
| Ask-before-acting approvals | ✅ Auto / Ask / Trust, plus per-tool authority levels | ✅ Approval modes for connectors | ❔ | ❔ | ✅ Approval for MCP tools by default | 🟡 Configured MCP tools run autonomously; allow-listing recommended | 🟡 Opt-in; payment limits via AP2 |
| User hook scripts | ✅ Shell or HTTP, before and after tools and runs | ❔ | ❌ | ❌ | ❔ | ❔ | ❌ |
| Skills you can install or build | ✅ | 🟡 Plugins | 🟡 Apps and GPTs | ❔ | ❔ | 🟡 Extensions | ❔ |
| Per-run cost tracking in the app | ✅ | ❔ | ❔ | ❔ | ❔ | ❔ | ❔ |

*Where this table says "None found" or "❔", we looked and either found nothing or found only third-party material. It doesn't mean the feature definitely doesn't exist.*

---

## Where EVA is genuinely different

These are the capabilities we couldn't find in a comparable form in the products above, as of the date at the top. They're EVA's design choices, not claims that nobody else will ever offer them.

### Plan-Execute-Verify with adaptive re-planning
EVA has a formal three-phase pipeline: a Planner generates a structured step plan, each step runs through a full tool loop, a Verifier checks the result, and a failure triggers an adaptive re-planner. You can review and approve the plan before anything runs. Other products have agentic loops, and several show plans; we didn't find one that exposes a discrete plan, verify and re-plan cycle with a user approval gate.

### Fine-grained tool authority
EVA lets you set a maximum authority level (read, mutating, all) and an approval mode (auto, ask, trust). You can require confirmation before any file write, shell command or external call. The approval pauses the stream, waits for your decision, and cancels the execution on rejection. Other products do ask before using tools — Cursor and Cowork do by default for connectors — but we didn't find the same per-tool authority tiers.

### Daemons with an event bus and webhook triggers
EVA daemons go beyond a schedule. They can fire on a timer, on an internal EVA event (such as a task reaching a particular state), or from a public webhook that a CI system or monitoring tool calls. Several products offer scheduled background work; we didn't find an equivalent of triggering from EVA's own internal events.

### Provider fallback without a visible break
You can configure an ordered fallback chain across any of EVA's 18 providers. If a provider fails before the first token, the next takes over and the stream continues; the usage record shows which provider actually answered. A provider that can't currently be used — for example after you remove its key — is skipped rather than failing the run.

### Hook scripts
Pre- and post-tool and pre- and post-execution hooks let you add rate limiting, custom logging, notifications, validation or clean-up without changing EVA. Programs EVA launches don't inherit your provider API keys, so a hook can't read them by accident.

### Skills — install or build
EVA installs skills from published libraries, including Anthropic's skill catalog, with one action, and lets you build your own named, versioned workflow bundles. Other products have plugin or extension ecosystems; EVA's difference is combining install-from-catalog with a custom builder inside one local app.

### Structured project workspaces
Project files — goals, tasks, knowledge, a journal — form persistent context the agent maintains and reads on every run. They are real files you can edit directly, and three injection tiers (compact, focused, full) control how much enters each run's token budget.

### Sensitive memory
Memory entries can be marked sensitive so they're never auto-injected into prompts. Reading one needs an explicit grant that expires after an hour. We didn't find an equivalent concept in the other products.

### Observability you can inspect
EVA records per-run token counts (including cached tokens), estimated cost in USD priced on the model that actually answered, duration, tool-call latency, fallback events and the reason a run ended, and exposes a full execution trace. Cost totals are marked as partial when some runs have no known price. Most consumer products show you much less.

### Local-first and nothing to run
EVA installs and runs like a regular desktop app — no external database or separate service, and the document search engine is built in. Your data stays on your machine except what you send to the AI provider you choose; see [Privacy & Security](02-privacy-and-security.md) for the complete list of what EVA contacts on its own.

---

## Where other products are ahead

- **Computer use and web agents.** Claude, ChatGPT, Perplexity and Gemini Spark can operate a browser or computer for you as a polished, hosted capability. EVA has a browser tool through MCP, not a hosted agent computer.
- **Always-on cloud execution.** Cowork's scheduled tasks and Gemini Spark run in the cloud, so your computer doesn't need to be on. EVA's daemons run only while EVA is running on your machine.
- **Platform reach.** EVA is built and tested for Windows only. Most of the products above run on more than Windows, including macOS and the web.
- **Deep IDE integration.** If your work is mainly writing code inside an editor, Cursor and GitHub Copilot are built for that, with parallel cloud agents and repository integration EVA doesn't try to match.
- **First-party integrations.** Gemini Spark's Gmail and Workspace integration and the large connector catalogs from Anthropic and OpenAI are deeper than what EVA offers out of the box, where you connect integrations through MCP.
- **Maturity.** EVA is in beta. The products above have much larger teams, user bases and support operations.

---

## Sources

Checked 22 October 2026. Where a page couldn't be opened directly, the claim relies on what a search of that vendor's site returned, and is marked in the text as unconfirmed where it matters.

- Claude Cowork — [Get started with Claude Cowork](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork) (cloud execution, connectors, `/schedule`, paid plans); [Schedule recurring tasks in Cowork](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-cowork)
- ChatGPT — [ChatGPT agent](https://help.openai.com/en/articles/11752874-chatgpt-agent) and [Introducing ChatGPT agent](https://openai.com/index/introducing-chatgpt-agent/) (the agent is no longer offered; Work with Scheduled Tasks replaces it); [Developer mode](https://developers.openai.com/api/docs/guides/developer-mode) (MCP apps, SSE and streamable HTTP). Two OpenAI help pages returned errors when opened directly, so the Work details come from search results of those pages
- Perplexity — [Local and remote MCPs](https://www.perplexity.ai/help-center/en/articles/11502712-local-and-remote-mcps-for-perplexity); [Adding custom remote connectors](https://www.perplexity.ai/help-center/en/articles/13915507-adding-custom-remote-connectors); [Introducing Perplexity Computer](https://www.perplexity.ai/hub/workshops/introducing-perplexity-computer)
- Cursor — [Cursor 3](https://cursor.com/blog/cursor-3) and [3.1 changelog](https://cursor.com/changelog/3-1) (parallel agents, worktrees, handoff); [MCP docs](https://cursor.com/docs/context/mcp) (three transports, approval before MCP tool use). Custom-key limits come from a third-party integration guide, not Cursor's own docs
- GitHub Copilot — [GitHub Docs: coding agent and MCP](https://docs.github.com/en/copilot) (tools only, no OAuth remote servers, autonomous use once configured); [Run multiple agents with /fleet](https://github.blog/ai-and-ml/github-copilot/run-multiple-agents-at-once-with-fleet-in-copilot-cli/); VS Code documentation on bring-your-own-key and the Ollama extension
- Gemini Spark — [TechCrunch, 19 May 2026](https://techcrunch.com/2026/05/19/google-introduces-gemini-spark-a-24-7-agentic-assistant-with-gmail-integration/) (cloud VMs, Gmail address, MCP connections, AI Ultra beta); [Google's Spark page](https://gemini.google/overview/agent/spark/)

If you spot something here that's out of date or wrong, tell us and we'll correct it.

---

> **Next:** [Privacy & Security](02-privacy-and-security.md) — where your data lives, how credentials are stored, and EVA's security model.
