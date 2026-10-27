# Creating Custom Agents

Custom agents let you go further than the built-in set: you define the persona, constrain the tool set, choose the model, and scope the agent to a specific project. The orchestrator delegates to your custom agent exactly as it would to any built-in.

---

## Built-in vs custom agents

The **Subagents** panel groups agents into two sections: **EVA Built-in Agents** and **My Custom Agents**.

Built-in agents are maintained by EVA and are read-only in one specific way: their tool list is managed by EVA's definitions and cannot be changed directly. Everything else — their system prompt, model, max steps, description — is editable. If you need to change a built-in agent's tool list, clone it first to get a fully editable copy.

Custom agents are entirely yours. All fields, including tools, are editable. They are never overwritten by EVA updates.

---

## When to create a custom agent

Create a custom agent when you have a recurring role that the built-in set doesn't cover:

- A domain-specific researcher who only uses a narrow set of tools and speaks in a particular voice
- A code reviewer scoped to a specific project's conventions
- A data pipeline agent that only has access to shell and file tools
- A client-facing summariser that never uses web search or executes code

The key insight is that the orchestrator chooses which agent to delegate to based on the agent's **name** and **description**. A well-named, well-described custom agent will be selected reliably; a vague one won't be.

---

## Configuration

| Field | What it controls |
|-------|-----------------|
| **Name** | Unique slug for this agent. Lowercase letters, numbers, hyphens, and underscores only. Cannot be changed after creation — clone if you need to rename. |
| **Description** | Shown to the orchestrator when it decides which agent to delegate to. Be specific: describe what the agent is *for*, not what it *is*. |
| **System prompt** | The agent's persona, operating rules, and constraints. This is the most important field — it determines how the agent reasons and responds within its delegated task. |
| **Provider / Model** | Which AI provider and model to use. Leave blank to inherit from your active session. Setting a specific model is useful for specialised roles — a fast cheap model for summarisation, a powerful one for complex reasoning. |
| **Max steps** | Maximum reasoning iterations per delegation (1–20, default 5). Set higher for complex multi-tool tasks; lower to cap cost on simple ones. |
| **Tools** | Which tools this agent can use. Leave empty to inherit all your currently enabled tools. Providing an explicit list restricts the agent to only those tools — useful for safety and focus. |
| **MCP servers** | Which MCP servers this agent can use. Leave empty to inherit all your enabled MCP servers. |
| **Project** | Scope this agent to a specific project. Project-scoped agents appear in that project's Agents tab and are available when working in that project context. Leave blank for a user-global agent available everywhere. |

---

## Creating an agent from scratch

Open the **Subagents** panel via the nav rail. Click **New** in the top-right.

Fill in at minimum:

1. **Name** — a concise slug that the orchestrator will reference, e.g. `data-pipeline-agent` or `security-reviewer`
2. **Description** — one or two sentences describing what this agent does and when to use it, e.g. *"Analyses Python data pipeline code for performance bottlenecks and correctness. Use when the task involves pandas, SQLAlchemy, or Airflow DAGs."*
3. **System prompt** — the agent's operating brief

Everything else has a sensible default. Save when done.

---

## Cloning a built-in agent

If you want to start from a built-in agent but need to change its tool list or significantly customise its system prompt:

1. In the Subagents list, click the **Clone** icon on any built-in agent card
2. Enter a new name for the clone
3. Click **Clone**

The clone appears under **My Custom Agents** with `is_default = false` — fully editable, including its tool list. The original built-in agent is unchanged and can be reset to its factory state at any time.

---

## Editing a built-in agent

Built-in agents can be edited directly — except for the tool list, which is locked. You can change the system prompt, description, model, and max steps freely.

If you edit a built-in's system prompt and later want to undo all changes, click **Reset** on the agent card. This restores the full original definition from EVA's shipped YAML, including the tool list. Edits to fields other than tools are also restored by reset.

---

## Syncing built-in agents

When EVA ships an update that changes a built-in agent's tool list or system prompt, your local copies are not automatically updated (to preserve any edits you made). The **Sync defaults** button in the Subagents panel header re-seeds all built-in agents from the latest EVA definitions, overwriting your edits to default agents.

Use this after an EVA update if you want the latest built-in definitions. Custom agents are never touched by sync.

---

## Project-scoping

An agent scoped to a project is available only within that project's conversations. It does not appear in the global Subagents panel — you create, edit, and manage it from the project browser under the **Agents** tab.

There is one constraint: EVA's built-in agents cannot be project-scoped directly. If you want a built-in agent associated with a project, clone it first, then assign the clone to the project.

Agents linked to a project are automatically disabled if the project is paused or archived.

---

## How the orchestrator selects your agent

When EVA's orchestrator delegates a subtask, it reads the list of available agents and matches tasks to agents based on name and description. The match is made by the orchestrator's language model — meaning good descriptions work, not rigid routing rules.

Practical guidance:

- **Name** should be a recognisable shorthand, not a generic label. `reviewer` is ambiguous; `security-code-reviewer` is not.
- **Description** should say what the agent is optimised for, not just its title. *"Reviews Python for security vulnerabilities"* is more useful to the orchestrator than *"a security reviewer"*.
- If the orchestrator consistently picks the wrong agent, the most common fix is sharpening the descriptions of the competing agents — not rewriting system prompts.

---

## Deleting a custom agent

Click the **Delete** icon on a custom agent card. A confirmation prompt appears inline before deletion. Built-in agents cannot be deleted — only custom agents can be permanently removed.

If a deleted agent is referenced by a running PEV task, that delegation attempt will fail with a "no agent found" error. EVA does not cascade-delete active tasks when an agent is removed.

---

> **Next:** [What are Daemons?](../09-daemons/01-what-are-daemons.md) — persistent background agents that run on a schedule, respond to webhooks, and act without any input from you.
