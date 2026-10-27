# What are Subagents?

A subagent is a specialised AI agent you configure to handle a specific type of task — code review, research, git operations, testing, deployment, and so on. When EVA is working on a complex task in Plan mode, it can break that task into subtasks and delegate each one to the right specialist. Multiple subagents can run at the same time. The orchestrating agent collects all their results and assembles the final answer.

A task that would take a single agent many sequential steps can instead be decomposed, parallelised across specialists, and completed in a fraction of the time.

---

## How subagents work

The orchestrating agent — EVA in Plan mode — analyses the task and decides how to decompose it. Each subtask is handed to a subagent along with a description of what it needs to accomplish and any relevant context. The subagent runs its own independent reasoning loop, calls its own tools, and returns a result.

Each subagent starts fresh: it has no memory of the current conversation and no awareness of what other subagents are doing in parallel. It knows only what the orchestrator explicitly passes to it. This isolation is intentional — it keeps each specialist focused and prevents context bleed between workstreams.

Progress from all running subagents is streamed to your conversation in real time. You can see which agents are active, what tools they are calling, and when each one finishes — while the work is happening, not after.

**Single-agent delegation (`delegate_to_agent`) is available in both chat and Plan mode.** In chat mode, EVA can hand off a specific sub-task to a named subagent and incorporate its result without switching to Plan mode. Multi-agent orchestration — parallel delegation and coordinated workflows — requires Plan mode.

---

## How EVA splits and parallelises work

The orchestrator has three delegation modes, each suited to a different class of task.

### Sequential delegation — one agent, one task

The simplest mode: hand a single subtask to a single named agent and wait for its result before continuing. Use this when the output of one agent is needed as input to the next.

> *"Review this code"* → code-review-agent → result fed into orchestrator → next step

A delegation can also run **in the background**: the subagent works on its own and the call returns straight away, so the orchestrator can carry on — or pause the whole task and resume automatically when the subagent finishes. Background subagents appear in the [Activity tab](../02-conversations/07-activity-and-preview.md) while they run.

### Parallel delegation — multiple agents simultaneously

The orchestrator can dispatch multiple agents at the same time. All agents start concurrently; the total wall-clock time is equal to the slowest agent, not the sum of all of them. This is the primary mechanism for accelerating complex tasks.

> *"Analyse the codebase and research best practices simultaneously"*  
> → file-system-analyst + deep-research running in parallel → both results collected → orchestrator continues

Results from each agent are written to a shared workspace so the orchestrator can read all of them after the parallel step completes.

### Coordinated workflow — multi-phase, LLM-planned

For the most complex tasks, the orchestrator can invoke a coordinator that plans the entire workflow automatically. Given a high-level goal and the list of available agents, the coordinator:

1. Analyses the goal and decides which agents to use
2. Groups them into phases — determining which phases can run in parallel and which must run in sequence
3. Executes each phase, passing the outputs of each phase as context to the next
4. Synthesises all phase results into a single coherent final answer

This produces a fully planned multi-agent pipeline from a single high-level goal, without the orchestrator needing to manually specify each step.

The coordinator prefers agents you already have. But when the goal needs a specialisation none of them cover — say a poet and a networking explainer, with only a research agent registered — it can invent temporary agents for the plan, each with its own persona and instructions. They exist only for that plan, exactly like the dynamic subagents described below. Because the plan is produced in one step with no chance for you to review it first, the number of new agents it may invent in a single plan is capped (three by default; see the [Configuration Reference](../15-configuration/01-configuration-reference.md)).

There are also two fast-path strategies that skip the planning step: **parallel** (all agents run at once on the same goal) and **sequential** (agents chain outputs one into the next, pipeline-style).

**What you see in the conversation:** the coordinator appears as a single muted summary line — for example, `Coordinated 2 phases · 5/6` — rather than a bordered card, matching how EVA renders every other internal-process block. Click it to expand the full phase breakdown: each phase with its strategy label, each agent's result with a success or failure indicator, and the coordinator's synthesis at the bottom. This is distinct from individual subagent activity rows, which appear below the coordinator's line as their own collapsed entries.

---

### A worked example

**Task:** *"Review our API codebase, write tests for any gaps, document the endpoints, and check for security issues."*

The orchestrator decomposes this into four independent workstreams and runs them in parallel:

| Agent | Task |
|-------|------|
| code-review-agent | Review the API files for correctness and quality issues |
| security-reviewer | Audit for vulnerabilities and produce a findings report |
| testing-agent | Write tests for untested endpoints |
| documentation-writer | Generate API reference documentation from the source |

All four run simultaneously. The orchestrator collects all four results, synthesises them into a unified summary, and presents the final report — in the time it would have taken to do one of those tasks sequentially.

---

## Registered and dynamic subagents

There are two ways a subagent can come into existence.

**Registered subagents** are configured in advance through the Subagents panel. They have a saved name, description, system prompt, tool whitelist, and step budget. When the orchestrator delegates to a registered agent, it looks up that configuration by name and uses it exactly as defined. These are the agents you create, tune, and reuse.

**Dynamic subagents** are created on-the-fly by the orchestrator when a task calls for a specialisation that has no pre-configured agent. The orchestrator defines the agent's persona, tool list, and step budget inline, spawns it for that one task, and it disappears when done. You do not set these up — they emerge from the model's judgment about what the task requires.

Dynamic subagents are useful when a PEV task is sufficiently unusual that no pre-built specialist fits. Registered subagents are preferable for routine tasks: they are consistent, documented, and tunable.

---

## Configuration

Each registered subagent has the following settings:

| Setting | What it controls |
|---------|-----------------|
| **Name** | Unique identifier (lowercase, numbers, hyphens, underscores). The orchestrator refers to the agent by this name. Cannot be changed after creation. |
| **Description** | What this agent is for. Helps the orchestrator choose the right agent for a subtask. |
| **System prompt** | The agent's persona, instructions, and operating constraints. This is where the specialisation is defined. An **Enhance** button next to the field expands a rough draft into a fuller prompt using AI — available whenever a provider is configured. |
| **Provider** | Which AI provider this agent uses. Leave blank to inherit from your current session. |
| **Model** | Which model this agent uses. Leave blank to inherit from your current session. |
| **Max steps** | Maximum reasoning iterations (1–20). A focused task like code review needs fewer; deep research or implementation needs more. |
| **Tools** | Which tools this agent can use. Leave empty to give it access to all your enabled tools. Set a specific list to restrict it to only what it needs. |
| **MCP servers** | Which MCP servers this agent can connect to. Leave empty to inherit all configured servers. |
| **Project** | Limit this agent to a specific project. A project-scoped agent is only available in conversations within that project. Leave blank for a globally available agent. |

---

## Built-in agents

EVA includes thirteen built-in agents covering the most common task types: research, coding, code review, testing, git operations, documentation, security review, data analysis, error diagnosis, filesystem exploration, deployment, self-improvement, and running skills. These are available immediately — no setup required.

Built-in agents are marked **default** in the Subagents panel. They have one difference from custom agents: their tool lists are fixed and cannot be edited directly. This ensures the built-in agents remain internally consistent with their purpose. You can, however:

- **Edit the system prompt and step budget** of any built-in agent.
- **Reset** a built-in agent to its original definition at any time, discarding any customisations.
- **Clone** a built-in agent to create a fully editable copy. The clone is a custom agent — every field, including the tool list, is editable.

See [Built-in Agents](02-built-in-agents.md) for a description of each one.

---

## Project scoping

Subagents can be scoped to a specific project. A project-scoped agent is only available in conversations within that project and does not appear in the global Subagents panel — it is visible and managed exclusively from the project browser under the Agents tab.

Built-in agents are always available regardless of project — they cannot be project-scoped. Custom agents are global by default; you can assign them to a project when you create or edit them.

If you try to assign a built-in agent to a project, EVA will offer to clone it first. The clone becomes the project-scoped agent; the original built-in continues to exist globally, unchanged.

---

## Managing subagents

The **Subagents** panel, accessible from the workspace sidebar, lists your global agents in two sections: EVA Built-in Agents and My Custom Agents. Project-scoped custom agents are not shown here — they appear only in their project's Agents tab in the project browser.

**Creating an agent** — click **New** to open the creation form. Fill in the name (permanent), system prompt, and any other fields you want to configure. Fields left empty inherit sensible defaults.

**Or just ask EVA** — in any conversation, tell EVA what you want: *"Create a subagent that reviews pull requests for security issues, using only read-only git and file tools."* EVA gathers the details it needs, shows you the full draft configuration, and saves it only after you confirm. The same conversational path also works for editing an existing agent (EVA reads its current configuration first, then applies just the changes you asked for) and for deleting one.

**Editing an agent** — click any agent card to open its detail view. All fields except the name are editable. Changes take effect immediately for subsequent tasks.

**Cloning** — available on any agent. Creates a new custom agent pre-populated with the source agent's configuration. Give it a new name and the clone is immediately editable and usable.

**Resetting** — available on built-in agents only. Restores the agent to its original factory definition, discarding any customisations.

**Deleting** — available on custom agents only. Built-in agents cannot be deleted.

**Syncing defaults** — the **Sync defaults** button re-seeds all built-in agents from their latest definitions. Use this after an EVA update to pick up any improvements to the built-in agent library.

You can search agents by name or description.

---

> **Next:** [Built-in Agents](02-built-in-agents.md) — the thirteen specialised agents included with EVA.
