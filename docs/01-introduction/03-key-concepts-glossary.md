# Key Concepts Glossary

EVA uses a specific vocabulary that is important to understand before diving into any feature. This page defines each core concept in plain English, moving from the most common interaction points through to the advanced capabilities and security model.

---

## Conversation

A conversation is the most basic way to interact with EVA — an ongoing dialogue where you type a message and EVA responds. It is a persistent record of your exchanges, including every tool call made and every result returned. Conversations can be linked to a Project, giving EVA access to that project's context on every message in that thread.

> *Example: You ask EVA to research a topic, then ask it to write a report. Both tasks share the same history — EVA has the research available when it writes the report, without you re-explaining anything.*

---

## Project

A project is EVA's builder mode — a persistent environment for a specific area of work. It holds conversations, subagents, daemons, uploaded documents, and a set of context files that EVA actively maintains. EVA reads these files at the start of every execution within the project and updates them automatically after each one — so the project's knowledge stays current without you managing it manually.

> *Example: You create a project called "Q3 Campaign" and add brand guidelines as a rule. Every conversation in this project — research, copywriting, analytics — has those guidelines in context automatically. The project accumulates knowledge over time.*

---

## Workspace

Your workspace is the local folder on your machine where all your EVA data lives — conversations, memory, project context, uploaded documents, skills, and hook scripts. It defaults to a folder in your home directory. If you are starting a project around an existing folder or codebase, EVA can use that directory as the project's workspace instead.

---

## Execution

An execution is the unit of work behind every message you send. Each message creates exactly one execution, which runs in the background while EVA streams results back to you. EVA automatically routes each execution to conversational mode for quick tasks, or Plan-Execute-Verify (PEV) mode for complex multi-step work.

> *Example: You type "Summarise the files in my project and write a report." EVA creates an execution, routes it to PEV mode, generates a step plan, and works through each step.*

---

## Plan-Execute-Verify (PEV)

A three-phase execution strategy EVA uses for complex tasks. First, a Planner generates a numbered step plan. Next, each step runs through a full tool loop. Finally, a Verifier checks whether the result meets the original goal. If verification fails, EVA replans and continues. PEV is triggered automatically for multi-step tasks when Agent Mode is set to Auto, or can be forced via Agent Mode → Plan.

> *Example: You ask EVA to research three competitors and write a comparison report. EVA plans the research steps, executes each with web search, then verifies the output is complete and accurate before presenting the result.*

---

## ReAct Loop

ReAct stands for **Re**ason + **Act**. It is the engine that drives every execution. The model reasons about what to do, takes an action (calling a tool), observes the result, then reasons again — repeating until the task is complete or the step limit is reached. In PEV mode, hitting the step limit doesn't necessarily end the task — you may be asked whether to extend it. See [Step limits](../04-plan-execute-verify/01-what-is-pev.md#execute).

> *Example: EVA reasons → calls web search → reads the result → reasons → calls the shell tool → reads the result → responds with its findings.*

---

## Fallback Chain

A fallback chain is an ordered list of backup providers that EVA tries in sequence if the primary provider fails — due to a rate limit, outage, timeout, or quota exhaustion. The switch happens automatically and the response continues without interruption.

> *Example: Primary is Anthropic. Fallback chain: OpenAI → Groq. Anthropic hits a rate limit. EVA switches to OpenAI silently and completes the task. No error, no manual intervention.*

---

## User Facts

Named facts EVA extracts from your conversations and stores in your memory profile — your name, occupation, location, skills, tools, preferences, and more. User Facts are injected into every session as a compact summary so EVA knows who you are without you re-explaining context each time.

---

## Agent Memory

Task-scoped memory entries created during PEV executions. Where User Facts record things about you personally, Agent Memory records what happened during a task — errors encountered, decisions made, approaches that worked — scoped to a project. Agent Memory lets EVA continue long tasks across sessions without losing the thread.

> *Example: EVA spent two sessions debugging a data pipeline. Agent Memory recorded the root cause found in session one and the validated fix from session two — so in session three, EVA picks up exactly where it left off.*

---

## Session

Your session is the personal configuration that applies to every conversation and execution — which provider and model to use, which tools are enabled, how approvals work, and fallback behaviour. You set it once and it persists across all your work.

---

## Context window

The total number of tokens a model can process in a single turn — your messages, EVA's system prompt, tool schemas, memory context, conversation history, and any attached files all count toward this limit. When a long conversation approaches the context limit, EVA automatically compresses earlier messages to free up space. See [Long Conversations](../02-conversations/06-long-conversations.md).

---

## Blackboard

A temporary shared scratchpad used by the orchestrator and subagents during a single execution. Agents write findings, planning outputs, and messages to one another into the blackboard. It is cleared when the execution completes. When blackboard encryption is enabled, entries are AES-GCM encrypted at rest so inter-agent data is protected even in the in-memory broker.

---

## Tool

A tool is a capability EVA can invoke during a ReAct loop — a real action beyond generating text. Tools are how EVA interacts with your machine, the web, your documents, and external services. Each tool has an authority level (read-only, mutating, or destructive) that determines whether your approval is required before it runs.

> *Example: Web search runs immediately without any approval. Writing a file pauses for your confirmation when approval mode is on.*

---

## HitL (Human-in-the-Loop)

HitL is EVA's approval gate — your ability to review what EVA is about to do before it does it. When enabled, EVA pauses before any action that writes, modifies, or deletes something, shows you exactly what it plans to do, and waits for your confirmation before proceeding.

> *Example: You ask EVA to "delete all log files older than 30 days." EVA identifies the matching files and pauses: "I'm about to delete these 47 files. Approve?" You review and confirm — or cancel.*

---

## Skill

A skill is a named, reusable capability bundle stored in your personal skill library. When skills mode is active, EVA is aware of your entire library at the start of every execution and can invoke any skill by name. You can install ready-made skills from published libraries — including Anthropic's skill catalog — or build your own.

> *Example: You create a skill called `weekly-review` describing a fixed workflow. From then on, "run the weekly review" is enough — EVA knows exactly what that means.*

---

## RAG (Document Search)

RAG — Retrieval-Augmented Generation — lets EVA search documents you have uploaded and pull in the relevant sections when answering questions or completing tasks. Upload a PDF, Word document, or text file, and EVA can find the right passage even across large documents. The search engine is embedded directly in EVA — no separate service or setup required.

> *Example: You upload a 200-page product specification. You ask "What are the latency requirements?" EVA finds and quotes the relevant section accurately.*

---

## Hook Scripts

A hook script is a custom script — shell or HTTP — that EVA runs automatically at specific points in the execution lifecycle. You upload it once and EVA calls it at the right moment, passing context about what is happening as environment variables.

> *Example: A `post_execution` hook posts a summary to a Slack channel every time EVA finishes a task — including overnight daemon runs.*

---

## MCP (Model Context Protocol)

MCP is an open standard for connecting AI models to external tools and data sources. EVA supports registering external MCP servers, each of which exposes its own set of tools and resources that become available inside EVA just like built-in tools — without writing any integration code.

> *Example: A database tool publishes an MCP server. You register it in EVA. EVA can now query your database in any conversation.*

---

## Subagent

A subagent is a named AI persona with its own system prompt, model, provider, tool permissions, and step limit. When a task benefits from specialisation, the orchestrator delegates to the relevant subagent, which runs its own ReAct loop and returns the result to the main conversation. EVA ships 13 built-in agents; you can clone any of them or build your own from scratch.

> *Example: You ask EVA to "review my code and write tests for the gaps." The orchestrator calls the code review agent, then the testing agent. Both results stream back into your conversation.*

---

## Daemon

A daemon is an autonomous background agent that runs without you being present. You configure it once — with a trigger, a system prompt, a model, and a set of tools — and it operates on its own indefinitely. Triggers can be time-based (a schedule), event-based (reacting to internal EVA events), or webhook-based (an external system calling a URL).

> *Example: "Every weekday at 7:45am, read my open task list and draft a standup summary." EVA runs this every morning on its own. You open the app and the summary is ready.*

---

> **Next:** [Conversations](../02-conversations/01-conversations.md) — how EVA builds context across a dialogue.
