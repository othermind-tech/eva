# EVA Local — AI Execution Engine

**EVA Local is a zero-infrastructure, single-process AI execution backend. It delivers multi-user, provider-agnostic AI with a ReAct tool loop, Plan-Execute-Verify orchestration, long-term memory, RAG, and a structured project workspace — through a clean REST + SSE API. No Redis. No separate worker process. One command to start.**

---

## Table of Contents

- [Core Concepts](#core-concepts)
- [Architecture Overview](#architecture-overview)
- [Architecture Flow Diagrams](#architecture-flow-diagrams)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Running EVA](#running-eva)
- [API Reference](#api-reference)
  - [Authentication](#authentication)
  - [Chat & Execution](#chat--execution)
  - [Session Configuration](#session-configuration)
  - [Conversations & Messages](#conversations--messages)
  - [Projects](#projects)
  - [Agents (Subagents)](#agents-subagents)
  - [Daemons](#daemons)
  - [Memory](#memory)
  - [RAG](#rag)
  - [Tools](#tools)
  - [Providers](#providers)
  - [MCP Servers](#mcp-servers)
  - [Hook Scripts](#hook-scripts)
  - [Skills](#skills)
  - [Attachments](#attachments)
  - [Secrets](#secrets)
  - [Approvals (Human-in-the-Loop)](#approvals-human-in-the-loop)
  - [Events](#events)
  - [Metrics](#metrics)
  - [Prompt Enhancer](#prompt-enhancer)
- [Provider Fallback Chain](#provider-fallback-chain-v65)
- [Streaming Protocol](#streaming-protocol)
- [Execution States](#execution-states)
- [Memory System](#memory-system)
- [Workspace Layout](#workspace-layout)
- [Tool Development Guide](#tool-development-guide)
- [Configuration Reference](#configuration-reference)
- [Deployment Notes](#deployment-notes)
- [Monitoring & Observability](#monitoring--observability)
- [Troubleshooting](#troubleshooting)
- [Error Reference](#error-reference)
- [File & Directory Reference](#file--directory-reference)

---

## Core Concepts

Understanding EVA requires distinguishing five entities that are often conflated in other systems.

### Conversation
A conversation is the persistent record of a dialogue between a user and EVA. It holds the ordered message history (user turns, assistant turns, tool calls, tool results) and belongs to exactly one user. A conversation may be linked to a project. Each conversation can have its own system prompt.

Conversations are created automatically when a user posts to `/chat` without supplying a `conversation_id`. Subsequent posts with the same `conversation_id` continue the same conversation.

### Execution (Conversational or PEV)
An execution is the unit of work triggered by a `/chat` request. Each POST to `/chat` creates exactly one execution. Executions are asynchronous — the API returns immediately with an `execution_id` and the work happens in the background `ExecutionWorker` (which runs as an asyncio task inside the server process).

There are two execution modes, selected automatically by the intent router:

| Mode | When activated | What happens |
|------|---------------|--------------|
| **Conversational** | Greetings, questions, single-turn requests | Single LLM call through the ReAct loop (up to `MAX_REACT_STEPS` tool iterations). Fast and lightweight. |
| **Plan-Execute-Verify (PEV)** | Task-oriented requests ("search and summarise", "build and test", multi-step work) | LLM generates a structured step plan → each step executed by ReAct loop → VerifierEngine validates → optionally re-plans on failure. |

The routing decision (`conversational` or `plan_execute`) is returned in the `/chat` response and never changes mid-execution.

You can override automatic routing by setting `agent_mode` in your session to `chat` (always conversational) or `plan_execute` (always PEV).

### Session
A session is the per-user runtime configuration: which provider and model to use, which tools are enabled, fallback chain, memory preferences, tool approval mode, etc. Session config is updated via `POST /set-session`. It persists to DB and is reloaded on server restart.

**V6.22: EVA has no default provider/model.** A brand-new user's session has `provider`/`model`
unset (`null` on `GET /session`) until `POST /set-session` is called at least once — every
LLM-touching endpoint (`/chat`, `/prompts/enhance`, `PATCH /session/permissions`) returns a clean
`424` (`error_code: "provider_not_configured"`) rather than guessing one. Previously a fresh session
silently defaulted to a hardcoded provider and could fire a real API call before the user ever chose
one — found via a live trace, not by inspection.

### Project
A project is a named workspace that groups related conversations, agents, and daemons under one context. Projects have their own AI workspace files (`.eva` artifacts) that are automatically injected into the agent's context when a conversation is linked to that project.

**Only one project per user can be `active` at a time.** Activating a project automatically pauses all other active projects.

Project workspace files (inside `~/eva_workspace/{user_id}/projects/{project_id}/.eva/`):
- `project.eva` — metadata, goals, status
- `policies.eva` — behavioral rules (always injected)
- `tasks.eva` — current action items
- `knowledge.eva` — domain knowledge and notes
- `journal.eva` — execution history and key turns
- `errors.eva` — failure log

Context tiers control how much is injected per execution:
- **Compact** (~400 tokens): `project.eva` + `policies.eva`
- **Focused** (~700 tokens): + `tasks.eva`
- **Full** (~1800 tokens): all six files

### Agent (Subagent)
An agent (`SubagentConfig`) is a named, reusable AI persona scoped to a user. Agents have their own system prompt, model, provider, tool whitelist, and step limit. They are invoked by the orchestrator using the `delegate_to_agent` tool — the orchestrator spawns a child `AgentEngine` using the subagent's config and runs it within the current execution.

Agents can be scoped to a project (`project_id`) or user-global.

### Daemon
A daemon is an autonomous background agent that runs on a schedule or in response to events. Daemons have their own system prompt, trigger message, model, provider, tools, and retry policy.

Daemon triggers:
- **Time-based**: runs every `run_interval_seconds` (minimum 60 s)
- **Event-based**: triggers when a named EVA event fires (e.g. `execution.completed`)
- **Manual**: `POST /daemons/{name}/trigger`

Daemon lifecycle states: `idle` | `running` | `retrying` | `paused` | `permanently_failed`

---

## Architecture Overview

EVA Local runs as a **single process**. The API server, execution worker, daemon scheduler, and broker all live in the same Python process — started with one `uvicorn` command.

```mermaid
graph TD
    Client["Client\n(Browser / API Consumer)"]

    subgraph Process["Single Server Process  —  FastAPI · port 8000"]
        Chat["api/chat.py\nPOST /chat"]
        SSE["api/eva_response.py\nGET /eva_response (SSE)"]
        REST["Other REST endpoints\nsession · auth · projects · agents\ndaemons · memory · rag · tools · mcp"]

        subgraph Broker["core/broker/  —  LocalBroker (in-process)"]
            Queue["Job queue\n(asyncio.Queue)"]
            Meta["Execution metadata\n+ status\n(LocalRedisAdapter dict)"]
            Shadow["LocalShadowBuffer\n(per-execution chunk list\n+ asyncio.Event)"]
            HitL["HitL approval mailbox\n(LocalRedisAdapter key)"]
            Heartbeat["Worker heartbeat\n(LocalRedisAdapter key)"]
        end

        subgraph Worker["ExecutionWorker  —  asyncio task"]
            WLoop["• Dequeue job\n• Reconstruct history from DB\n• Load project context\n• Inject long-term memory\n• Run AgentEngine or PEVRunner\n• Append chunks → LocalShadowBuffer\n• Flush metrics to DB"]
        end

        subgraph Sched["DaemonScheduler  —  asyncio task"]
            SchedLoop["• Polls daemon schedules\n• Triggers run_daemon()\n• Handles event subscriptions"]
        end
    end

    subgraph Storage["Persistent Storage"]
        DB["SQLite\nmessages · executions · memory · config"]
        ChromaDB["ChromaDB\nvector embeddings (RAG)\n(embedded — no Docker)"]
        FS["Filesystem\nproject .eva files · facts.md · RAG blobs"]
    end

    Client -->|POST /chat| Chat
    Client -->|GET /eva_response| SSE
    Client <-->|REST| REST
    Chat -->|enqueue job| Queue
    SSE -->|zero-copy chunks| Shadow
    Queue --> Worker
    Worker --> Meta
    Worker --> Shadow
    Worker --> HitL
    Worker --> Heartbeat
    Worker <-->|read/write| DB
    Worker <-->|read/write| ChromaDB
    Worker <-->|read/write| FS
    Sched <-->|read/write| DB
```

### Layer Responsibilities

| Layer | Responsibility |
|-------|---------------|
| **API** | Accept request, persist user message, enforce one-execution-per-conversation guard, enqueue job |
| **LocalBroker** | In-process job queue, execution metadata store, per-execution shadow buffers for stream replay, HitL approval mailbox — all backed by asyncio data structures. No network I/O. |
| **ExecutionWorker** | Asyncio task started in `lifespan`. Dequeues jobs, reconstructs history, injects memory + project context, runs reasoning engine, streams to buffer, persists result. |
| **AgentEngine** | Stateless reasoning engine — drives the ReAct loop, manages provider fallback. Never touches DB. |
| **PEVRunner** | Orchestrates multi-step Plan → Execute → Verify cycle using AgentEngine per step. |
| **LocalShadowBuffer** | Per-execution in-memory chunk list. Wakes SSE consumers instantly via `asyncio.Event`. Makes the live stream replayable — clients that disconnect can reconnect and receive all prior chunks. |
| **SSE** | Projection layer only — reads from `LocalShadowBuffer.chunks_from()`. Never a source of truth. |
| **DB** | Durable source of truth for messages, execution records, memory, preferences. |
| **DaemonScheduler** | Asyncio task. Polls DB for due daemons, triggers `run_daemon()`, registers event-bus subscriptions for event-triggered daemons. |

### Design Principles

- Execution is the unit of truth. At most one execution per conversation at any time.
- Streaming is a projection, never a source. Shadow buffers make it replayable.
- History has a single owner: the worker. No other layer reconstructs history.
- Cancellation is destructive — there is no pause/resume (except HitL approval gates).
- DB is the only durable storage. The broker is execution-time orchestration only.
- AgentEngine is stateless and portable — it receives messages, returns chunks.
- Provider fallback is transparent: the model may switch mid-execution without stream interruption.
- Tool authority is static per-tool. The HitL gate fires in the dispatcher — AgentEngine is unaware.
- Zero external services required — ChromaDB (RAG vector store) is embedded in-process. No Docker required.

---

## Architecture Flow Diagrams

### 1. Conversational Execution (Normal Flow)

```mermaid
sequenceDiagram
    participant C as Client
    participant API as API Server
    participant B as LocalBroker
    participant W as ExecutionWorker
    participant LLM as LLM Provider
    participant DB as Database

    C->>API: POST /chat {messages, conversation_id?}
    API->>DB: Persist user message
    API->>B: Enqueue execution job (asyncio.Queue)
    API-->>C: 201 {conversation_id, execution_id, status: accepted, mode: conversational}

    C->>API: POST /auth/sse-token
    API-->>C: {sse_token, expires_in: 30}
    C->>API: GET /eva_response?sse_token=...&conversation_id=...

    W->>B: Dequeue job
    W->>DB: Reconstruct message history
    W->>DB: Load project context (if linked)
    W->>DB: Load long-term memory (user facts, agent entries --\nnot chat summaries, disabled V4.40)

    loop ReAct loop (max MAX_REACT_STEPS)
        W->>LLM: Stream completion request
        LLM-->>W: TextChunk / ToolCallChunk
        W->>B: shadow.append(chunk) → asyncio.Event.set()
        B-->>C: SSE event: text / tool_call (zero-copy)
        opt Tool call
            W->>W: Execute tool (ToolDispatcher)
            W->>B: shadow.append(ToolResultChunk)
            B-->>C: SSE event: tool_result
        end
    end

    W->>DB: Persist assistant message
    W->>DB: Flush execution metrics
    W->>B: Mark execution COMPLETED
    B-->>C: SSE event: finish
    B-->>C: SSE event: done (stream closes)
```

---

### 2. Plan-Execute-Verify (PEV) Execution

```mermaid
sequenceDiagram
    participant C as Client
    participant API as API Server
    participant B as LocalBroker
    participant W as ExecutionWorker
    participant Planner as PlannerEngine
    participant Agent as AgentEngine
    participant Verifier as VerifierEngine

    C->>API: POST /chat {messages}
    Note over API: Intent router → plan_execute
    API->>B: Enqueue job (mode: plan_execute)
    API-->>C: 201 {execution_id, mode: plan_execute}

    W->>B: Dequeue job
    W->>B: FSM: PENDING → PLANNING
    W->>Planner: Generate step plan
    Planner-->>W: Structured JSON plan (N steps)
    W->>B: Append plan text chunks to shadow buffer
    B-->>C: SSE events: text (plan output)

    alt require_plan_approval = true
        W->>B: FSM: PLANNING → AWAITING_PLAN_APPROVAL
        B-->>C: SSE event: plan_approval_required
        C->>API: POST /executions/{id}/plan-decision (decision=approve/edit/reject)
        alt decision = edit
            Note over W: body.plan replaces the\nLLM-generated plan verbatim
        end
        W->>B: FSM: AWAITING_PLAN_APPROVAL → RUNNING
    else auto-proceed
        W->>B: FSM: PLANNING → RUNNING
    end

    loop For each plan step
        W->>B: Append step_progress event to shadow buffer
        B-->>C: SSE event: step_progress
        W->>Agent: Execute step (full ReAct loop)
        Agent-->>W: Step result chunks
        W->>B: Append chunks to shadow buffer
        B-->>C: SSE events: text / tool_call / tool_result
        W->>Verifier: Validate step result
        alt Verification passed
            Verifier-->>W: OK → proceed to next step
        else Verification failed
            Verifier-->>W: FAIL → trigger adaptive re-plan
            W->>Planner: Re-plan remaining steps
            Planner-->>W: Revised plan
        end
    end

    W->>B: Mark execution COMPLETED
    B-->>C: SSE event: finish / done
```

> **V6.9:** `VerifierEngine.verify()`, `PlannerEngine.replan()`, and the intent-router's LLM
> classifier each call an LLM with a small `max_tokens` cap sized for a short expected answer.
> Against a "reasoning-mandatory" model (e.g. gpt-oss, on Ollama Cloud or Groq) that can't be told
> to skip its chain of thought, that reasoning consumes the same budget before the real answer is
> ever emitted — starving the response to empty and causing verification/re-plan/classification to
> silently fall back. Fixed with flat, empirically-validated `max_tokens` increases (5→100, 200→500,
> 600→900) plus an anti-deliberation prompt line at each call site; a dynamic OpenRouter-style ratio
> formula was prototyped and rejected after empirical testing showed it under-predicted harder
> prompts.

> **V6.11:** a context-budget watchdog now runs inside the ReAct loop itself — Layer 1 dynamically
> caps any single tool result to remaining context headroom (generalizing the shell tool's static
> byte cap to every tool); Layer 2 proactively compresses accumulated tool-call history in place
> (deterministic, not an LLM call) once usage crosses 85% of the model's budget, and the
> `MAX_REACT_STEPS` cutoff now attempts one compressed, tool-disabled best-effort answer before
> erroring. Falls back to a conservative default when a model's context size isn't known via
> `capability_registry` (confirmed some providers, e.g. Ollama Cloud, have no cached capability data
> at all) rather than silently disabling itself. `VerifierEngine` also now asks for a minimal
> `{"passed": true}` on success instead of LLM-generated prose.

> **V6.12:** two more gates added to the same family — `AWAITING_STEP_EXTENSION` (backend complete,
> this note) lets a user extend the ReAct step budget when it's reached, instead of the execution
> just erroring out; fires *after* V6.11's forced-best-effort-answer attempt, not instead of it.
> `POST /executions/{id}/extend`, new `max_step_extensions` session field (default 2, `0` disables).
> Separately, plan approval (`require_plan_approval`) gained a third option beyond approve/reject:
> `POST /executions/{id}/plan-decision` now also accepts `decision: "edit"` with a replacement plan,
> which becomes what actually executes — **breaking change**: `.../approve-plan` and
> `.../reject-plan` no longer exist, replaced by this one unified endpoint.

> **V6.13:** three additive gaps surfaced during frontend integration. `GET
> /daemons/{name}/executions` now returns `model_used`/`provider_used`/
> `prompt_tokens`/`completion_tokens`/`reasoning_tokens` — the underlying `ExecutionMetrics` row
> already had them, they were just never copied onto the response. `event_subscriptions` is now
> actually settable through the daemon API — it turned out to be missing from the write path
> entirely (`DaemonConfigRequest`, `DaemonRegistry.upsert()`), not just the response as first
> reported. Tool-call `tool_args` and tool-result `tool_result` (plus a derived `is_error`) now
> persist and survive a conversation reload — `_save_tool_step()` was never passing them to
> `add_message()` even though the columns and the repository method already supported both; known
> limitation carried forward: one arg-set per message row, so a step with multiple simultaneous tool
> calls can't be fully represented.

> **V6.14:** `core/execution/pev_runner.py` no longer mixes hand-authored markdown narration ("##
> Planning...", "_Verifying step..._", etc.) into the same `TextChunk` stream as the model's actual
> answer — it was the only file in the backend doing this, and the frontend had no way to tell EVA's
> own process narration apart from the real response. All 19 sites now carry the same information as
> structured fields on `StepProgressChunk` (`plan_steps`, `verification_summary`,
> `verification_issues`, `replan_reason`, `error_detail`, `steps_completed`/`steps_failed`/
> `replan_count`, new `step_status` values) or a genuine `ErrorChunk` for the two actual error
> conditions in that set. `api/executions.py`'s trace endpoint gained a `step_progress` branch it
> was missing entirely — without it this data would have been invisible outside the live SSE stream.
> Shipping this surfaced and fixed a real regression (V6.14.1): rejecting a plan silently stopped
> transitioning the execution to `cancelled`, because the removed narration chunks had been
> unintentionally driving that transition — fixed with a `step_status="cancelled"` signal plus a
> defensive backstop in the consumer loop. Also fixes a frontend-reported gap (V6.14.2): tool-call
> message rows never carried `tool_name`/ `tool_call_id` (only the paired tool-result row did),
> which also incidentally fixes conversation export's tool-call lines that had silently printed
> "unknown" since that feature shipped.

> **V6.42:** the PEV verifier was too literal — it checked whether a step's exact wording was
> satisfied instead of whether the step's underlying goal was achieved, and a
> verifier-infrastructure failure (LLM error, timeout, unparseable response) was treated identically
> to a genuine step failure, both spending a full adaptive-replan cycle on trivial tasks. Fixed on
> four fronts: `prompts/verifier.py` rule 1 now judges outcome fulfillment, not literal description
> matching (still a quick binary check, not detailed re-derivation); a new rule 7 requires the
> `issues` field to name the concrete technical reason a step failed rather than a wording complaint
> — this text already flowed unchanged to the replanner, so no new pipeline was needed.
> `VerificationResult` gained `infra_error: bool` (`core/execution/verifier.py`), and
> `pev_runner.py` now treats an infra-error step as a non-blocking warning — the step continues
> instead of burning a replan attempt on a failure that was never the step's fault, while still
> never silently treating an unverified step as good (preserving what the V4.41 fail-closed policy
> actually guaranteed). `execution_plans`'s plan-level `status` no longer counts a step that failed
> but was then resolved by a successful replan — each step's own historical `status` still
> accurately reads "failed" where it happened, only the plan-level tally that previously disagreed
> with the user-facing outcome was fixed. Live-verified against a real restarted server across 4
> tasks with a real groq model: a genuine verifier-infra failure completed with a warning and no
> replan spent; a genuine step failure produced a specific, actionable `verification_notes` reason,
> triggered a real replan, and the plan still finished `status="completed"`; two further tasks with
> soft/subjective and format-flexible wording completed clean with no false-literalism failures.

> **V6.29:** V6.12's `AWAITING_STEP_EXTENSION` gate applied uniformly to every execution, including
> unattended daemon runs — a daemon hitting its own step limit would pause waiting for a decision on
> `POST /executions/{id}/extend` that nobody could ever send, blocking on
> `STEP_EXTENSION_TIMEOUT_SECONDS` (3600s default) before auto-resolving to "stop" anyway. Found via
> a real "Sync now" button click hanging in practice. `core/agent_engine.py`'s gate call site now
> checks `execution.execution_mode == "daemon"` and skips straight to that same "stop cleanly"
> outcome instead of waiting out the timeout — a daemon's own `max_steps` (`DaemonConfig`, e.g.
> `PROJECT_SYNC_DAEMON_MAX_STEPS`) is the explicit, intentional step budget for unattended runs; if
> that's not enough, the fix is raising it directly rather than letting the owning user's
> interactive-session `max_step_extensions` preference implicitly extend it further. Conversational
> and plan-execute executions are unaffected.

> **V6.30:** UX-01 Phase 2 — a plan reject now carries weight. Rejecting with feedback text (and
> revision rounds remaining, `PLAN_REVISION_MAX_ROUNDS`, default 2) calls a new
> `PlannerEngine.revise()` to regenerate a complete plan informed by that feedback, persists it, and
> re-opens the approval gate with a fresh token — instead of the reject just cancelling the
> execution outright, which is still exactly what happens for a bare reject (no reason) or once
> revision rounds run out. No frontend change was needed: `ApprovalGate.tsx` already collected and
> sent the optional reason on reject; the backend previously only logged it. Live-verified against a
> real model: two feedback-driven revisions each correctly incorporated the stated change, a third
> reject correctly hit the cap and cancelled instead of revising again. Verifying this also surfaced
> and fixed B-099: `execution_plans.status` was never actually being persisted as `"cancelled"` on
> any reject, ever (predates this session) — the DB write sat after a `yield` the real consumer
> never resumes past on a cancellation exit, at three separate sites in `pev_runner.py`, dead code
> since the V6.15 fix that added it.

> **V6.31:** Test suite cleanup — `tests/test_features.py` had carried a stable set of 22
> pre-existing failures through this session's work (confirmed via `git stash` A-B comparisons to be
> unrelated to any of it, every time). Investigated properly rather than continuing to carry them:
> all 22 were test-suite staleness (renamed/moved symbols, mock targets pointing at removed
> functions, hardcoded source-slice windows that grew stale, and two cases of the code intentionally
> changing after the test was written and never being updated — `delegate_to_agent` un-excluded from
> chat mode and `plan_execute` added to progressive disclosure, both V6.3). Fixed all 22; suite now
> passes 610/610. Surfaced one small, separately-logged gap along the way (`Q-05` — no committed
> `eva.env` template).

> **V6.32:** DB-02 closed — `execution_plans` rows are now deleted along with their conversation,
> instead of being left orphaned forever (`DBRepo.delete_conversation_and_children()`,
> `core/db/repository.py`). Checked first whether this should instead follow `ExecutionMetrics`'
> precedent of deliberate retention for audit/cost history (same FK shape) rather than assuming
> "cascade delete is missing" was automatically the right fix — decided against it: unlike
> `ExecutionMetrics` (aggregate numbers only), `ExecutionPlan`'s `goal`/step
> `description`/`result_summary` are actual conversation content, so a user deleting a conversation
> should see it gone too. `ExecutionMetrics` itself is unchanged. Found, but did not fix, a
> byte-identical dead-code duplicate of the deletion method sitting in `AuthRepo`, never called
> anywhere (`Q-06`).
>
> **Same-day addendum:** properly auditing `Q-06` before just deleting it
> found a second dead `AuthRepo` duplicate (of
> `delete_all_conversations_for_user()` this time) — and comparing it
> against the real implementation surfaced that the real one, the actual
> logic behind the bulk `DELETE /conversations` endpoint, had never
> received the `ExecutionPlan` fix above at all. Same bug, bulk form,
> missed in the original pass. Fixed, unit-tested, live-verified (two
> conversations, bulk-deleted, both plans gone, both metrics retained).
> Both dead `AuthRepo` copies removed. `Q-06` closed.

> **V6.33:** `Q-05` closed properly — the original V6.31 framing turned
> out to rest on a false premise. That pass had fixed a failing test by
> adding an `OLLAMA_CLOUD_API_KEY` placeholder to the local `eva.env`,
> trusting `OllamaCloudSettings`' own docstring that it belonged there.
> Re-auditing against the canonical user docs
> (`docs/15-configuration/01-configuration-reference.md`, not the
> incomplete `README.md` block used the first time) showed that's wrong:
> `OLLAMA_CLOUD_API_KEY` is a provider secret in `PROVIDER_KEY_MAP`,
> migrated to Windows Credential Manager and **actively stripped out of
> `eva.env`** by `config/env_loader.py` on every startup, identical
> treatment to every other cloud provider's key — confirmed by three
> independent, currently-running code paths, none of which agreed with
> the one stale docstring. Kept the code as the authority, not the test:
> corrected the docstring (no behaviour change, purely descriptive),
> replaced the false-premised test with three that check the real,
> correct things (the new template documents `OLLAMA_CLOUD_BASE_URL`; it
> never documents a provider secret; the docstring no longer claims
> otherwise), and reverted the local `eva.env` placeholder line (it would
> have been silently stripped on the next real startup anyway). Also
> added `backend/eva.env.example` — a tracked template new checkouts can
> copy from (`eva.env` itself stays gitignored, per-developer), sourced
> from the canonical docs' already-correct, comprehensive template, not
> the incomplete `README.md` one. Synced that canonical doc with
> `PLAN_REVISION_MAX_ROUNDS` (V6.30, added mid-session, missing from the
> doc until now).

> **V6.34:** `DB-01` and `S-02` closed together, both re-scoped from their original
> generic-webservice framing to what's actually true for EVA (single-user desktop app, but bound to
> `0.0.0.0` — confirmed by reading `build/entrypoint.py` directly, so not fully off-network either)
> and built to OWASP standards, verified via web search before design. `DB-01`: `auth_sessions`'s
> two real lookup paths are both indexed exact-match, so "lookups degrade" didn't hold — the real
> cost was unbounded disk growth. `AuthRepo.purge_expired_sessions()` (dead sessions: revoked or
> refresh-expired) and `purge_old_audit_logs()` (`AUTH_AUDIT_LOG_RETENTION_DAYS`, default 180 — a
> policy default, not a compliance number) now run daily alongside the existing workspace cleanup
> pass. `S-02`: rate limiting landed on the endpoints that are actually reachable without
> authentication — `/auth/login` (dual-key, IP **and** account per OWASP's Authentication Cheat
> Sheet, failure-only counted, resets on success, temporary window not a permanent lockout),
> `/auth/signup` and `/auth/refresh` (IP-keyed anti-spam). New shared
> `core/rate_limit.py::SlidingWindowRateLimiter` replaces the webhook rate limiter's original
> hand-rolled version (S-3) with zero behaviour change there. Live verification surfaced a real,
> pre-existing, app-wide bug in the process: `main.py`'s global `HTTPException` handler never passed
> `exc.headers` through to its `JSONResponse`, so `Retry-After` had been silently dropped on every
> 429 since before this session (the webhook limiter had the same bug the whole time) — fixed
> alongside. 629 tests passing, full regression clean, all of it live-verified against a real
> restarted server.

> **V6.50 — what a plan step and its verifier can see, and how a re-plan is kept honest.** Every
> step of a plan used to start with only the original request plus its own step text, so later steps
> redid earlier work and the closing "summarise" step invented details (one live answer blamed a
> typo that did not exist). Each finished step now leaves a small record —
> `core/execution/step_record.py`: one line per tool call (`tool`, what it acted on, `ok` / `ERROR:
> …` / `exit N`, never full arguments or results), the step's own text (trimmed) and the verifier's
> verdict. Later steps receive the earlier steps' records and are told to state only what they
> actually saw, plus a names-only line listing the tools this run has already activated (so they
> stop re-calling `describe_tool` for them); the per-step verifier receives the current step's tool
> lines and fails a claimed change / run / start / stop that has no matching successful call. All
> sizes are settings (`PEV_STEP_RECORD_*`, `PEV_PRIOR_STEPS_MAX_CHARS`). A re-plan that returns only
> a retry of the failed step used to end the run `completed` with the rest of the goal never
> attempted; the re-plan prompt now demands the whole remainder and `planner._keep_remaining_steps`
> puts dropped steps back (also in both single-step fallbacks). A provider failure that leaves a
> plan/execute run with no output now ends it `failed` (with the provider's message) instead of
> `completed`. There is exactly one verifier call site (per step, after the step); no overall "goal"
> verifier exists.

> **V6.51 — the per-step verifier can now see what earlier steps did (A-16).** A model sometimes
> completes a later step's goal early, while working on an earlier one — the later step then makes
> no tool calls of its own, and the verifier used to fail it: "no evidence the file was modified,"
> even though the work genuinely happened a step earlier. The executing agent already got
> `prior_steps_block` (A-14); the verifier judging that same step never did. It now receives the
> identical block the step itself got, plus a rule that a step with an empty ledger of its own still
> passes if an earlier step's *real evidence* covers it — narration alone still isn't enough (A-13's
> rule is unrelaxed). Live-verified two ways: a direct before/after call against a real model (fails
> without the block, passes with it, same step), and — the stronger proof — a real 2-step plan
> through the actual server where the model organically did this exact thing on its own (not
> scripted) and the fixed verifier handled it correctly. Also logged, not built: A-21, an idea for
> the runner to skip *executing* an already-satisfied future step, not just verify it correctly
> after the fact.

> **V6.52 — Ollama Cloud's real context window (A-17), and a re-plan budget that was still too small
> (A-18).** EVA's context-budget watchdog was firing for `gpt-oss:120b` at ~7,000 tokens instead of
> anywhere near its real 131,072-token window — not a missing-data problem but a missing-wiring one:
> the registry's LiteLLM merge never included `ollama`/`ollama_cloud` in its provider map, so real
> data already in LiteLLM's own dataset was being silently discarded. Wired in for Ollama Cloud only
> — local Ollama is deliberately left out, since its real usable context depends on the user's own
> `num_ctx`, which no static dataset can know. Separately, `PlannerEngine.replan()`'s 900-token
> budget was confirmed live to still truncate a reasoning-mandatory model's response before any JSON
> was ever emitted; raised to 2000, the number found empirically (1200/1500/1800 each cleared 4/4
> live trials), not guessed. A same-day follow-up fixed a related display endpoint that still
> reported "unknown" for Ollama Cloud models after the main fix landed.

> **V6.53 — one real closing answer instead of every step's text glued together (A-19), and an
> independent check that the finished plan actually did what it was asked (A-20).** The saved answer
> for a plan/execute run used to be `BufferedStream.aggregated()`'s naive `full_text +=
> chunk.content` over every step's `TextChunk`, no separator — a live-captured example read `"...No
> errors occurred.I ran the command..."`, one step's sentence fused directly onto the next. Fixed as
> a prompt change on the *last* step's own execution turn (no extra LLM call — it already has the
> full earlier-steps record) plus new plumbing (`execution.final_answer_override`) so that text, not
> the raw concatenation, is what actually gets saved. Paired with a new, narrow `PlanVerifierEngine`
> — one extra call per run, no tools, tri-state verdict (success/pending/not_successful) against a
> new `success_criteria` field the planner now generates, explicitly kept independent of the plan's
> own closing narrative (self-grading the whole plan would reopen the exact problem A-13 fixed for
> single steps). Verified with a real before/after comparison against the running server: the fixed
> run's independent check caught a genuine evidence gap a human reviewer would also flag — not a
> false alarm. `success_criteria` and the verdict itself are now persisted to `execution_plans` (two
> new migrations) after a same-day follow-up — both had previously only existed in memory or an
> SSE-only chunk that doesn't survive the shadow buffer's eviction window.
>
> **V6.54:** the execution watchdog's per-state timeout table covered two of its three sibling
> human-in-the-loop pause gates (`AWAITING_APPROVAL`, `AWAITING_PLAN_APPROVAL`) but never
> `AWAITING_STEP_EXTENSION` (V6.12) — a user who never responded to a step-limit-extension prompt
> left that execution with no automatic watchdog abort, unlike its two siblings (still recoverable
> on a process restart via the existing gate-marker crash recovery, just not within the same
> uptime). Found incidentally while live-testing A-21 below, when a real execution's live broker
> status (`awaiting_step_extension`) turned out to explain what first looked like a hung LLM call.
> Fixed by adding the missing `_TIMEOUTS` entry; on request, also split what was one shared
> `WATCHDOG_APPROVAL_TIMEOUT_SECONDS` constant into three independently-configurable settings, one
> per sibling gate (`WATCHDOG_APPROVAL_TIMEOUT_SECONDS`, `WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS`,
> `WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS` — all default to the same 4h value today, but each can
> now be tuned on its own).
>
> **V6.55 — adaptive PEV: skip a future step whose goal an earlier step already achieved (A-21).**
> A-16 (V6.51) fixed the per-step verifier to correctly *pass* a step whose work an earlier step
> already did — but the runner still *executed* that step first, wasting an agent call before the
> now-fixed verifier recognized nothing was needed. The per-step verifier is now also shown the
> steps planned immediately after the current one (always excluding the plan's own true final step)
> and may report, only when it judges the current step to have passed, how many of them its same
> evidence already covers too — fail-closed in code, not just prompt discipline, and counted
> contiguously from the first upcoming step so a gap never counts. The runner clamps that claim
> against a hard deployer-level ceiling (`PEV_LOOKAHEAD_MAX_SKIP`, default 2) before ever skipping
> anything, marks skipped steps `status="skipped"` (fully visible in the plan, never silently
> dropped, never counted as `"completed"`), and jumps straight to the next unsatisfied step. A new
> per-user session preference, `pev_lookahead_skip_enabled` (default **true** — opt-out, not
> opt-in), follows `require_plan_approval`'s exact DB-backed wiring end to end so it has a real
> frontend toggle, not just a server-side flag. Live-verified through the real restarted server,
> including a genuine organic skip: a step that wrote and self-verified a file caused the
> separately-planned "read the file again" step to be marked skipped with zero agent invocation,
> while the plan's own true final step still ran for real immediately after.
>
> **V6.56 — CDP-embedded preview pane backend (browser automation frontend spec).** Three new
> endpoints (`POST`/`DELETE`/`GET /session/preview-endpoints`) let EVA's own Electron app register a
> live CDP endpoint so the agent's `playwright` MCP attaches to a browser view embedded in the app,
> instead of spawning a separate OS window. A new in-memory registry
> (`core/mcp/preview_registry.py`, keyed by name, never persisted) feeds a new
> `resolve_connection_args()` resolver wired into the two real connection-construction sites
> (`core/mcp/session_pool.py`, `core/mcp/manager.py`) — checked at actual connection time, not baked
> in once at seed time, so a pane opening or closing mid-session takes effect on the very next
> attempt. Registering or unregistering the pane resyncs the `playwright` config immediately if
> enabled: `bump_generation()` + `discard()` (an active close, not the lazy `bump_generation()`
> alone `PUT /mcp/{id}` uses) so an already-spawned standalone browser window is cleanly killed
> rather than left orphaned. Backend-only; the Electron pane and its relay are built separately. 23
> new unit tests, mutation-confirmed on both the playwright-name guard and the active-kill call
> specifically (the one behaviour explicitly called out as worth testing). Live-verified end to end
> against the real Electron relay, including the agent driving a real embedded-pane browser session
> (navigating to live sites, screenshots rendering in-app).
>
> **V6.57 — `MCPSessionPool.discard()` could orphan an in-flight tool call.** Found live: a
> `browser_navigate` call hung for the full 300s tool-execution timeout, then auto-retried and
> succeeded instantly. Root cause: `discard()` popped and closed a pooled connection without ever
> acquiring `entry.lock` — the same lock every `call_tool()` holds for its entire duration. If
> `discard()` ran while a connection was still mid-handshake, its "close" request could land in the
> connection's internal queue ahead of the in-flight call's own "call" request (which only enqueues
> once the handshake finishes) — the owner task then processed "close" first and returned, never
> reaching the queued call, whose awaited future then had no one left to resolve it. Latent since
> `discard()` was written (its only prior caller, `DELETE /mcp/{id}`, rarely races a live call);
> V6.56's resync-on-register/-unregister made it fire routinely while the agent may be actively
> mid-call, turning a latent race into a readily reproducible one. Fixed by having `discard()`
> acquire the same lock before touching anything — it now only ever closes a genuinely idle
> connection, queuing up behind an in-flight call instead of racing it. Mutation-tested: the new
> test reproduces the exact hang against the original code (`asyncio.TimeoutError`) and passes
> cleanly against the fix. Incidentally hardened an unrelated flaky test found while re-verifying
> (`test_midflight_cancellation_unit.py`'s real-subprocess-orphan check — a fixed 0.5s
> sleep-then-check replaced with a 3s poll, since OS process teardown isn't a hard 0.5s guarantee
> under heavy concurrent test load).
>
> **V6.58 Phase 1 — background process PID-reuse hazard.** Every consumer of a `BackgroundProcess`
> row (boot reconciliation, Stop, the daily reaper, duplicate-detection) trusted a bare PID lookup —
> after a long uptime or reboot, Windows (and any OS) can reuse a PID, so any of those could in
> principle act on a completely unrelated process. Fixed as part of a broader
> session-scoped-background-process effort (in progress): adopted `psutil` for all
> process-management pieces (one cross-platform implementation instead of hand-rolled per-OS
> branches, closing the door on further platforms needing their own copy) and added
> `pid_create_time` (captured at spawn) so every one of those call sites now verifies identity, not
> just existence. `stop_background()` on an identity mismatch now correctly reports the tracked
> process as `"crashed"` rather than falsely claiming a successful `"stopped"` — and, critically,
> never touches whatever unrelated process actually holds that PID. Mutation-tested (disabling the
> check makes exactly the wrong-process-killed safety test fail) and live-verified end to end
> through the real server: start → real process spawned with identity recorded → stop → real OS
> process confirmed killed via `Get-CimInstance`.
>
> **V6.58 Phase 2 — `keep_alive_on_app_shutdown` opt-in.** Agreed policy: background processes
> become session-scoped by default (stopped automatically when EVA quits) with a per-process opt-in
> for one that should deliberately survive. This phase adds the flag and makes it settable — the
> `background_process` tool's `start` action gained `keep_alive_on_app_shutdown` (default `false`),
> and a new human-facing `PATCH /processes/{id}/keep-alive` endpoint lets the Activity panel toggle
> it afterward, both wired through to `GET /executions/running` so current state is visible. Doesn't
> yet change quit behavior itself — the cleanup sweep that reads this flag is a later phase.
> Live-verified end to end: a real chat/tool call started a process pre-pinned via the new argument,
> confirmed via `GET /executions/running`, toggled off via the new endpoint, confirmed the
> round-trip.
>
> **V6.58 Phase 3 — graceful-shutdown endpoint.** The piece that makes quit behavior actually
> change: new `POST /internal/shutdown` (localhost-only, no JWT — there's no single "current user"
> for an app-wide quit) stops every running background process that isn't
> `keep_alive_on_app_shutdown`, across all users, concurrently, reusing `stop_background()`'s
> existing identity check and kill path for each one — then schedules a short-delayed
> **self-directed** `signal.raise_signal(SIGINT)` and returns. Self-directed because it's delivered
> within the same process, sidestepping the root cause found in Phase 1's investigation entirely
> (Windows has no real signal delivery to a non-Node child) rather than working around it per-OS —
> Electron's `stopBackend()` just needs to call this endpoint before its existing SIGTERM/SIGKILL
> fallback, unchanged as the safety net it already is. Caught a real regression of the exact same
> bug class live: the first cut used `os.kill(getpid(), SIGINT)`, which every unit test happily
> mocked past, but which Windows silently routes to `TerminateProcess()` for any signal other than
> `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` — confirmed by an unplanned live call that hard-killed the dev
> server with zero graceful-shutdown log lines. `signal.raise_signal()` (Python 3.8+) is the actual
> fix — it invokes the registered handler synchronously and in-process, identically on every OS.
> Mutation-tested three branches (the `keep_alive_on_app_shutdown` filter, the localhost guard, and
> — the strongest confirmation — reverting to `os.kill()` under test, which hard-kills the test
> process itself). A second, deliberate live-verification pass (two real background processes, one
> `keep_alive_on_app_shutdown` each way) confirmed the fix and the full graceful-shutdown log
> sequence — but also caught a second, independent, pre-existing bug: the `=true` process died
> anyway, despite `/internal/shutdown` correctly never touching its DB row. Root cause:
> `start_background()`'s Windows spawn used `CREATE_NEW_PROCESS_GROUP` only, which says nothing
> about Windows Job Object membership — this backend's own process sat inside a job with
> `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (common under VS Code, Windows Terminal, conda-activated
> shells), so every descendant died with it regardless of the opt-in flag. Predates Phase 3 entirely
> (it would equally undermine the original feature's "survives a backend restart" promise on any
> ordinary crash); Phase 3's live test was simply the first to exercise the whole-process-tree-exits
> path at all. Added `CREATE_BREAKAWAY_FROM_JOB` with a required defensive fallback (confirmed live
> it fails outright with WinError 5 when the ambient job disallows it, so `start_background()`
> retries once without it rather than failing to spawn) — real and shipped, but a third live round
> showed it's not a complete fix: `CreateProcess` can succeed with the flag yet the child still
> reports `IsProcessInJob()==True` moments later and still dies, consistent with **nested** Job
> Objects the flag can't escape. Known limitation, not chased further — likely specific to this dev launch chain (conda-activated shell / VS Code
> terminal), not verified against the actual packaged Electron app. Electron wiring itself is a separate follow-up, out of scope for this backend-only phase.
>
> **V6.59 Phase 0 — locked, hash-verified dependencies; LiteLLM 1.106.0; MCP client on the mcp 2.x
> API.** Dependencies were open ranges (`litellm>=1.30,<2.0`), the exposure behind the March 2026
> LiteLLM PyPI compromise (1.82.7/1.82.8). `requirements.txt` is now the direct-dependency list
> (`litellm==1.106.0` pinned exactly); install from the universal, sha256-hashed `requirements.lock`
> + `requirements-dev.lock` into `backend/.venv` (Python 3.11: `uv pip sync requirements.lock
> requirements-dev.lock --require-hashes`). LiteLLM's published hashes were cross-checked against
> PyPI and the venv scanned for the known compromise indicators. Two previously undeclared
> dependencies surfaced and are now declared (`pydantic[email]`, `sqlalchemy[asyncio]`). LiteLLM
> 1.106.0 requires `mcp>=2.2`, so the MCP client (`core/mcp/connection.py` and the
> camelCase→snake_case result fields) was migrated; verified against a real mcp 2.3.0 server on
> stdio, streamable HTTP and SSE (54/54), by pushing EVA's real `LiteLLMHandler` through the real
> LiteLLM against a local fake provider (identical to the old stack), and live against a running
> server (26/26 including an agent-called MCP tool). The full suite is 1925 passed / 1 skipped / 0
> failed; 21 tests that only passed on a Program Files Python 3.10 (string-path `mock.patch` and
> `sys.executable` location assumptions) were fixed. Two bugs found along the way were fixed: an
> empty planner plan crashed the PEV runner with an `UnboundLocalError` (A-22 — the planner now
> substitutes the single catch-all step, keeping the model's goal), and under mcp 2.x a healthy
> server's protocol-level tool rejection made the session pool treat the connection as dead and
> **re-run the tool** (now surfaced as `MCPToolRejected`, no retry, the server's tools stay
> available). The old conda/system environments can no longer run the source.

---

### 3. Intent Routing — How Execution Mode is Decided

```mermaid
flowchart TD
    MSG["Incoming message\nPOST /chat"]
    L0{"Layer 0\nagent_mode setting"}
    L1{"Layer 1\nHeuristic regex"}
    L2{"Layer 2\nLLM classifier\n~150 token call"}
    CONV["ExecutionMode\nCONVERSATIONAL"]
    PEV["ExecutionMode\nPLAN_EXECUTE"]

    MSG --> L0
    L0 -->|agent_mode = chat| CONV
    L0 -->|agent_mode = plan_execute| PEV
    L0 -->|agent_mode = auto| L1

    L1 -->|Greeting / question\npattern matched| CONV
    L1 -->|Multi-step task\npattern matched| PEV
    L1 -->|Ambiguous| L2

    L2 -->|conversational| CONV
    L2 -->|plan_execute| PEV
    L2 -->|error / timeout| CONV

    CONV --> RUN["Worker runs\nAgentEngine\nReAct loop"]
    PEV  --> PLAN["Worker runs\nPlannerEngine →\nPEVRunner →\nVerifierEngine"]
```

---

### 4. Provider Fallback (V6.5)

```mermaid
flowchart TD
    START["AgentEngine\n_gen_with_fallback()"]
    COOLDOWN{"Candidate in\ncross-execution cooldown?"}
    TRY["Try candidate\n(structured SDK-error classification)"]
    CHUNK{"First chunk?"}
    ERR{"ErrorType / tier?"}
    RETRY["Retry SAME candidate\nwith backoff (Tier A only,\nmax_retries exhausted → cooldown)"]
    FALLBACK["Emit ProviderFallbackTriggered\nAdvance to next candidate\n(AUTH: skip rest of same provider)"]
    EXHAUSTED{"Candidates\nexhausted?"}
    STREAM["Commit candidate, emit\nProviderFallbackChunk (SSE) if\nnot the original primary,\nstream remainder normally"]
    FAIL["Yield terminal ErrorChunk →\nrunner.py marks execution FAILED\n(stream still ends cleanly)"]

    START --> COOLDOWN
    COOLDOWN -->|Yes — skip, no attempt| EXHAUSTED
    COOLDOWN -->|No| TRY
    TRY --> CHUNK
    CHUNK -->|Error| ERR
    CHUNK -->|Healthy| STREAM
    ERR -->|"Tier A: rate_limit / timeout /\nprovider_down (retries left)"| RETRY
    RETRY --> TRY
    ERR -->|"Tier A exhausted, or Tier B:\ninvalid_model / content_policy / auth"| FALLBACK
    ERR -->|"Tier C: invalid_request /\ninternal / unknown"| FAIL
    FALLBACK --> EXHAUSTED
    EXHAUSTED -->|No — try next| COOLDOWN
    EXHAUSTED -->|Yes| FAIL
```

---

### 5. Human-in-the-Loop Tool Approval

```mermaid
sequenceDiagram
    participant W as Worker (ReAct loop)
    participant D as ToolDispatcher
    participant A as approval.py
    participant B as LocalBroker
    participant C as Client
    participant API as API Server

    W->>D: Dispatch tool call
    D->>D: Check authority + tool_approval_mode

    alt READ_ONLY, or tool_approval_mode = auto
        D-->>W: Execute immediately
    else MUTATING or DESTRUCTIVE + approval required
        D->>A: gate_approval(tool, args)
        A->>B: SET approval_pending:{token}
        A->>B: FSM: RUNNING → AWAITING_APPROVAL
        A->>B: Append approval_required event to shadow buffer
        B-->>C: SSE event: approval_required\n{tool_name, authority, args_preview, approval_token}
        A->>B: BLPOP approval_decision:{token}  (async wait)

        alt User approves
            C->>API: POST /executions/{id}/approve {approval_token}
            API->>B: RPUSH approval_decision:{token} "1"
            A->>B: FSM: AWAITING_APPROVAL → RUNNING
            A-->>D: Approved — proceed
            D-->>W: Tool executes normally
        else User rejects or timeout
            C->>API: POST /executions/{id}/reject {approval_token}
            API->>B: RPUSH approval_decision:{token} "0"
            A->>B: FSM: AWAITING_APPROVAL → CANCELLED
            A-->>W: Execution terminates
        end
    end
```

---

### 6. Multi-Agent Delegation

```mermaid
sequenceDiagram
    participant W as Worker
    participant OA as Orchestrator AgentEngine
    participant LLM as Primary LLM
    participant DT as delegate_to_agent tool
    participant SR as SubagentRegistry
    participant SA as Subagent AgentEngine
    participant SLLM as Subagent LLM
    participant B as LocalShadowBuffer

    W->>OA: run_execution(messages, tools)
    OA->>LLM: Stream completion
    LLM-->>OA: ToolCallChunk: delegate_to_agent\n{agent_name: "researcher", task: "..."}
    OA->>DT: Execute delegate_to_agent
    DT->>SR: Look up SubagentConfig "researcher"
    SR-->>DT: {system_prompt, model, provider, tools, max_steps}
    DT->>SA: Spawn child AgentEngine with subagent config
    loop Subagent ReAct loop (max subagent.max_steps)
        SA->>SLLM: Stream completion
        SLLM-->>SA: Chunks
        SA->>B: Append subagent chunks to shadow buffer
        B-->>W: SSE event: subagent {agent_name, chunk}
    end
    SA-->>DT: Final result text
    DT-->>OA: ToolResultChunk with subagent output
    OA->>LLM: Continue with subagent result
    LLM-->>OA: Final response
    OA->>B: Append final text chunks
```

---

### 7. Daemon Lifecycle

```mermaid
stateDiagram-v2
    [*] --> idle : Daemon created / enabled

    idle --> running : Time trigger fires\n(run_interval_seconds elapsed)\nOR event subscription matches\nOR manual POST /daemons/{name}/trigger

    running --> idle : Run completes successfully\n(next_run_at updated)

    running --> retrying : Run fails\n(retry_on_failure = true\nfailure_count < max_retries)

    retrying --> running : Retry delay elapsed\n(exponential backoff)

    retrying --> permanently_failed : failure_count >= max_retries

    running --> permanently_failed : Run fails\n(retry_on_failure = false)

    idle --> paused : enabled set to false

    paused --> idle : enabled set to true

    permanently_failed --> idle : Manual reset\n(re-enable daemon)

    idle --> project_paused : pause_for_project()\n(owning project → paused/archived;\nskips daemons already paused or\npermanently_failed)
    running --> project_paused : pause_for_project()
    retrying --> project_paused : pause_for_project()

    project_paused --> idle : resume_for_project()\n(owning project → active again;\nnext_run_at recalculated from now,\nnot fired immediately)
```

---

### 8. Memory Injection into Execution Context

```mermaid
flowchart TD
    START["Worker starts execution"]
    HIST["Load message history\nfrom DB (ordered by sequence)"]
    PROJ{"Conversation\nlinked to project?"}
    CTX["Load project context\n(.eva files — compact/focused/full tier)"]
    MEM2{"user_memory_enabled?"}
    RECALL{"memory_recall\nin active tools?"}
    HINT["Inject hint: call memory_recall\n(suppresses direct fact injection)"]
    FACTS["Inject user facts\n(Scope 2 — facts.md markdown block)"]
    MEM3{"agent_memory_enabled\n+ agent_scope_key set?"}
    AGENT["Inject agent memory entries\n(Scope 3 — non-sensitive entries)"]
    SKILL{"skills_mode ≠ disabled?"}
    SKILLS["Inject available skills block"]
    BUILD["Build final system prompt\n+ message list"]
    RUN["Pass to AgentEngine"]

    NOTE["⚠ Scope 1 (chat summaries)\nnot injected — disabled V4.40\nto prevent rogue task continuation"]

    START --> HIST
    HIST --> PROJ
    PROJ -->|Yes| CTX
    PROJ -->|No| MEM2
    CTX --> MEM2
    MEM2 -->|Yes| RECALL
    MEM2 -->|No| MEM3
    RECALL -->|Yes| HINT
    RECALL -->|No| FACTS
    HINT --> MEM3
    FACTS --> MEM3
    MEM3 -->|Yes| AGENT
    MEM3 -->|No| SKILL
    AGENT --> SKILL
    SKILL -->|Yes| SKILLS
    SKILL -->|No| BUILD
    SKILLS --> BUILD
    BUILD --> RUN
    NOTE -.-> START
```

---

### 9. Execution State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING : POST /chat accepted\njob enqueued

    PENDING --> PLANNING : Worker dequeues job\n(plan_execute mode)
    PENDING --> RUNNING : Worker dequeues job\n(conversational mode)
    PENDING --> ABORTED : Watchdog\n(stuck > WATCHDOG_PENDING_TIMEOUT_SECONDS)
    PENDING --> CANCELLED : POST /executions/{id}/cancel\nwhile still in queue

    PLANNING --> AWAITING_PLAN_APPROVAL : require_plan_approval = true
    PLANNING --> RUNNING : require_plan_approval = false
    PLANNING --> FAILED : PlannerEngine error
    PLANNING --> CANCELLED : cancelled mid-plan

    AWAITING_PLAN_APPROVAL --> RUNNING : POST plan-decision\n(decision=approve or edit)
    AWAITING_PLAN_APPROVAL --> CANCELLED : decision=reject, no feedback\nor revision rounds exhausted,\nOR approval timeout
    AWAITING_PLAN_APPROVAL --> AWAITING_PLAN_APPROVAL : decision=reject with feedback\nPlannerEngine.revise() (V6.30),\nrounds < PLAN_REVISION_MAX_ROUNDS
    AWAITING_PLAN_APPROVAL --> ABORTED : Watchdog\n(gate itself got stuck)

    RUNNING --> VERIFYING : PEV step completed\n(VerifierEngine checks result)
    VERIFYING --> RUNNING : Pass + more steps,\nor Fail (PEV retries/continues)
    VERIFYING --> COMPLETED : Pass + last step
    VERIFYING --> FAILED : Unrecoverable verify error
    VERIFYING --> CANCELLED : cancelled mid-verify

    RUNNING --> AWAITING_APPROVAL : HitL gate fires\n(MUTATING/DESTRUCTIVE tool)
    AWAITING_APPROVAL --> RUNNING : POST /executions/{id}/approve
    AWAITING_APPROVAL --> CANCELLED : POST /executions/{id}/reject\nOR approval timeout
    AWAITING_APPROVAL --> ABORTED : Watchdog\n(gate itself got stuck)

    RUNNING --> AWAITING_STEP_EXTENSION : ReAct step limit reached\n(V6.12, after V6.11's forced\nbest-effort-answer attempt)
    AWAITING_STEP_EXTENSION --> RUNNING : POST extend\n(decision=continue)
    AWAITING_STEP_EXTENSION --> COMPLETED : decision=stop, extensions\nexhausted, or timeout\n(deliberately not CANCELLED --\nkeeps work already done)
    AWAITING_STEP_EXTENSION --> ABORTED : Watchdog\n(gate itself got stuck)

    RUNNING --> AWAITING_WAKEUP : Agent called schedule_wakeup\n(P-19) -- execution ends here,\nnothing blocks waiting
    AWAITING_WAKEUP --> COMPLETED : WakeupScheduler resolves it\n(a brand-new execution continues\nthe conversation, not this one)
    AWAITING_WAKEUP --> CANCELLED : Explicit user cancel\n(POST /wakeups/{id}/cancel)
    AWAITING_WAKEUP --> ABORTED : Watchdog\n(owning conversation deleted)

    RUNNING --> COMPLETED : All steps finished\nsuccessfully
    RUNNING --> ABORTED : POST /cancel (by conversation)\nworker detects at safe point,\nor Watchdog (stuck > WATCHDOG_RUNNING_TIMEOUT_SECONDS)
    RUNNING --> CANCELLED : POST /executions/{id}/cancel\nworker detects via broker poll
    RUNNING --> FAILED : Unrecoverable error

    COMPLETED --> [*]
    CANCELLED --> [*]
    ABORTED --> [*]
    FAILED --> [*]

    note right of AWAITING_APPROVAL : SSE stream stays open\napproval_required event\nalready in shadow buffer
```

---

### 10. SSE Streaming — Connection and Replay

```mermaid
sequenceDiagram
    participant C as Client
    participant API as API Server
    participant B as LocalShadowBuffer
    participant W as ExecutionWorker

    C->>API: POST /auth/sse-token
    API-->>C: {sse_token, expires_in: 30}

    C->>API: GET /eva_response?sse_token=...&conversation_id=...
    API->>B: chunks_from(0) — replay all buffered chunks
    B-->>C: SSE: buffered chunks replayed (zero-copy)

    loop Worker is still running
        W->>B: shadow.append(chunk) → asyncio.Event.set()
        B-->>C: SSE: live chunk (woken immediately by Event)
    end

    W->>B: append finish + done events
    B-->>C: SSE: finish
    B-->>C: SSE: done (stream closes)

    Note over C,B: Client disconnects and reconnects
    C->>API: GET /eva_response?sse_token=new_token&conversation_id=...
    API->>B: chunks_from(0) — full replay from start (idempotent)
    B-->>C: SSE: full replay from beginning
```

---

### 11. Cancellation

EVA supports two cancel paths. `POST /cancel` (by conversation) is used by the UI to abort a running execution. `POST /executions/{id}/cancel` targets a specific execution by ID — including executions still in the queue.

**V6.15:** cancellation now interrupts a tool call *while it's running*, not just between tool calls
or between LLM tokens — including killing the real OS process for an in-flight shell command. This
required two things working together: (1) `AgentEngine._execute_tool_cancellable()` races every tool
call against `cancel_token`, polling every ~150ms and cancelling the task if it fires — for shell
specifically, `ShellExecutor` uses a real `asyncio.subprocess.Process` handle (not a thread-wrapped
blocking call) so `.terminate()`/`.kill()` actually works; (2) a background
`_watch_for_external_cancellation()` task in `ExecutionWorker`, running independently of the main
execution coroutine for the whole lifetime of the execution, polling the broker for an external
cancel signal on its own schedule. (2) is required because the worker reconstructs its own
`ConversationExecution` from broker data rather than sharing the object the API layer mutates —
without an independent watcher, the only bridge between the two was a poll living inside the
runner's own chunk-consumption loop, which never got a turn to run while a tool call was in flight.

A cancelled execution also recovers whatever token usage was accumulated
before cancellation landed (instead of discarding it) and emits it as a real
`usage` SSE event and a normal `execution_metrics` row — a cancelled request
still gets a genuine (non-zero, where applicable) token count both live and
in the per-model/provider cost dashboard.

```mermaid
sequenceDiagram
    participant C as Client
    participant API as API Server
    participant B as LocalBroker
    participant CW as Cancel Watcher (background task)
    participant W as ExecutionWorker

    note over C,W: Path A — cancel by conversation (POST /cancel)
    C->>API: POST /cancel?conversation_id=conv_xyz
    API->>B: Look up active execution for conversation
    alt No running execution
        API-->>C: 404 No running execution to cancel
    else Execution found
        API->>B: Mark execution CANCELLED in broker metadata
        API-->>C: 200 {status: cancelled}
        CW->>B: Polls broker status every ~250ms (independent of the
        CW->>W: main loop -- runs even mid-tool-call)
        CW->>W: Sets execution.cancel_token.cancelled = True
        W->>W: Tool-call poll (~150ms) or step-loop poll detects it
        W->>W: In-flight shell subprocess killed via .terminate()/.kill()
        W->>B: FSM: RUNNING → CANCELLED
        W->>B: Recovers partial usage, persists usage_json, shadow.clear()
        B-->>C: SSE: usage {recovered token counts}
        B-->>C: SSE: cancelled {reason: user}
        B-->>C: SSE: finish {status: cancelled}
        B-->>C: SSE: done
    end

    note over C,W: Path B — cancel by execution ID (POST /executions/{id}/cancel)
    C->>API: POST /executions/{id}/cancel
    alt Not found / wrong user
        API-->>C: 404 / 403
    else Already terminal
        API-->>C: 409 Conflict
    else Execution queued or running
        API->>B: mark_execution_cancelled()
        API-->>C: 200 {execution_id, status: cancelled}
        CW->>B: Broker poll detects "cancelled" status
        W->>B: FSM: PENDING/RUNNING → CANCELLED
        B-->>C: SSE: usage / cancelled / finish {status: cancelled}
        B-->>C: SSE: done
    end
```

**V6.20:** two more reliability fixes, found while building backend support for the Electron
app-close task-cancellation flow. **`GET /executions/running`** is a new endpoint — the only place a
currently-running execution is actually visible, since a DB row for one doesn't exist until it
reaches a terminal state. Building it surfaced a real bug: daemon cancellation was cosmetic. `POST
/daemons/{name}/cancel` returned 200 and reset `lifecycle_status`, but the daemon's
`ConversationExecution` was never registered in `ExecutionRegistry` at all and had no background
cancel watcher (unlike conversational executions — see V6.15 above), so nothing ever stopped the
running agent loop. Daemons now register through the same
`ExecutionRegistry.create_execution_record()` path and run the same watcher, so `POST
/cancel?conversation_id=<daemon.id>` — the exact endpoint chat already uses — now works identically
for both, verified end to end against a real running daemon. Separately, fixed a deterministic
conflict between progressive-disclosure auto-promotion and the duplicate-tool-call guard: a model's
mandatory retry immediately after a tool gets auto-promoted was always flagged as a false-positive
duplicate, killing the whole execution — the fingerprint is now undone post-dispatch for stub-only
calls, while a real repeat of an *already-executed* call is still caught.

**V6.26: the duplicate-tool-call guard is now consecutive, not cumulative,
and tiered by tool risk** (V6.25 briefly raised the flat trigger from 2 to
3 identical calls; V6.26 replaces that with this redesign). Previously any
identical `(tool_name, arguments)` pair seen `N` times *anywhere* in a ReAct
step tripped the guard — so a legitimate "run a check, fix something, run
the same check again" pattern could abort a real execution even with
genuinely different tool calls in between. The counter now resets the
moment a *different* call happens, so only an unbroken run of identical
calls counts against the limit, and the trigger threshold is looked up per
call from the tool's existing `ToolAuthority` (`read_only` tolerates 5
in a row, `mutating` 3, `destructive` 2 — reusing the same classification
the Human-in-the-Loop approval gate already uses, not new metadata).
Argument fingerprints are also now type-aware (an int `1` and a string
`"1"` no longer collide) and the fingerprinting-error fallback fails open
instead of merging unrelated calls into one bucket.

Live-verified against a real running server (`ollama_cloud`/`gpt-oss:20b`, real tool dispatch, no
mocks): a genuine multi-step task called `read_file` 6 times cumulatively — interleaved with
`describe_tool`/`list_files` calls — and completed normally, something the old flat-cumulative
counter would have aborted on the 3rd occurrence regardless of what happened in between. A second
live run explicitly forcing 4 identical back-to-back `write_file` (`mutating`) calls correctly
tripped the guard on the 3rd attempt, every time, across 4 separate PEV replanning cycles. That same
run also surfaced a live example of the guard's one known, accepted limitation: after being blocked
twice, the model's next attempt slipped past the fingerprint check by adding a stray extra argument
the previous calls didn't have — exact-argument-match fingerprinting can't catch a call that's
deliberately (or incidentally) varied, at any threshold. Left to the existing `MAX_REACT_STEPS`
ceiling and PEV's own replan-on-failure handling as the backstop, rather than building
fuzzy/semantic argument matching to close it.

---

## Quick Start

### 1. Sign Up

```bash
curl -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "email": "alice@example.com", "password": "secret123"}'
```

Response:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400,
  "refresh_token": "rft_..."
}
```

### 2. Configure Session

```bash
curl -X POST http://localhost:8000/set-session \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "model": "gpt-4o",
    "tools": ["read_file", "shell", "web_search"],
    "agent_mode": "auto"
  }'
```

### 3. Send a Message

```bash
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What files are in the workspace?"}]}'
```

Response:
```json
{
  "conversation_id": "conv_abc123",
  "execution_id": "exec_def456",
  "status": "accepted",
  "execution_mode": "conversational"
}
```

### 4. Stream the Response

First, get an SSE token (required because browsers cannot send `Authorization` headers with `EventSource`):

```bash
SSE_TOKEN=$(curl -s -X POST http://localhost:8000/auth/sse-token \
  -H "Authorization: Bearer $TOKEN" | jq -r .sse_token)
```

Then open the SSE stream:

```bash
curl "http://localhost:8000/eva_response?sse_token=$SSE_TOKEN&conversation_id=conv_abc123"
```

JavaScript:
```javascript
// Step 1: exchange JWT for SSE token
const { sse_token } = await fetch('/auth/sse-token', {
  method: 'POST',
  headers: { Authorization: `Bearer ${jwt}` }
}).then(r => r.json());

// Step 2: open stream
const es = new EventSource(`/eva_response?sse_token=${sse_token}&conversation_id=${convId}`);

es.addEventListener('text',   e => process.stdout.write(JSON.parse(e.data).content));
es.addEventListener('done',   () => es.close());
es.addEventListener('error',  e => { console.error(e); es.close(); });
```

---

## Installation

### Prerequisites

- Python 3.10+
- **No Redis required**
- **No Docker required** — ChromaDB (RAG vector store) is pure-Python, embedded in-process
- **No separate worker process required**
- **No PyTorch required** — embeddings use ONNX Runtime (CPU); ~50 MB vs ~446 MB for torch

### Setup

```bash
# Clone the repository and enter the backend directory
git clone https://github.com/othermind-tech/eva.git
cd eva/backend

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate          # Linux/macOS
venv\Scripts\activate             # Windows

# Install dependencies
pip install -r requirements.txt
```

### ChromaDB (for RAG)

ChromaDB is included in `requirements.txt` — no separate install, no Docker, no server. It opens a persistent database at `~/eva_workspace/chroma_db` automatically on first use.

The path can be overridden in `eva.env`:

```bash
CHROMA_PATH=/custom/path/to/chroma_db
CHROMA_COLLECTION=eva_rag   # default collection name
```

RAG is non-fatal — if ChromaDB fails to initialise, all other EVA features continue to work normally.

### Embedding Model (for RAG)

EVA uses `all-MiniLM-L6-v2` for document embeddings via **ONNX Runtime** — no PyTorch or sentence-transformers required. The model files (~24 MB) download automatically from HuggingFace on the first RAG operation and are cached locally at `~/.cache/huggingface/hub/`. All subsequent uses are fully offline.

The embedding stack (`onnxruntime`, `tokenizers`, `huggingface_hub`) is included in `requirements.txt`. The model download only happens once — on first use of `POST /rag/ingest` or `POST /rag/search`.

### Environment Configuration

Create an `eva.env` file in the project root (EVA reads this file on startup via `config/env_loader.py` — not `.env`).

> **Secret management**: LLM provider API keys are stored in **Windows Credential Manager** (via `keyring`), not in `eva.env`. Set them via `POST /secrets` or directly in Windows Credential Manager under service name `"eva"`. The `eva.env` file is for non-secret config only (database URL, workspace paths, tuning knobs).

> **V6.6:** every non-secret setting documented in this file — not just the ones with their own `Settings` class in `config/settings.py` — is genuinely `eva.env`-overridable. `load_eva_env()` copies every key from `eva.env` into the process environment at startup (except provider API keys and `SECRET_KEY`, which only ever come from Windows Credential Manager), without overwriting a value already set by a real OS/system environment variable — so a real env var always takes precedence over `eva.env` if both are set for the same key. This runs before any other import in `main.py`, `entrypoint.py` (transitively), and `alembic/env.py` (needed for a standalone `alembic upgrade` outside the running server). A full app restart is required for `eva.env` changes to take effect — nothing re-reads the file after startup.

```bash
# LLM Provider API Keys — DO NOT put these here.
# Store via POST /secrets or Windows Credential Manager (service: "eva").
# See Provider API Keys section in Configuration Reference.

# Database (SQLite default — no server needed)
DATABASE_URL=sqlite+aiosqlite:///./eva.db
# PostgreSQL (production):
# DATABASE_URL=postgresql+asyncpg://user:pass@localhost/eva_db

# ChromaDB (RAG vector store — embedded, no server needed)
# CHROMA_PATH=~/eva_workspace/chroma_db   # default
# CHROMA_COLLECTION=eva_rag               # default

# Workspace
# EVA_WORKSPACE_ROOT=~/eva_workspace   # default

# Security (REQUIRED — change before deploying)
SECRET_KEY=change-me-in-production

# Execution tuning (all optional — defaults shown)
MAX_REACT_STEPS=8
EXECUTION_HEARTBEAT_SECONDS=30
WATCHDOG_INTERVAL_SECONDS=60
WATCHDOG_RUNNING_TIMEOUT_SECONDS=900
WATCHDOG_PENDING_TIMEOUT_SECONDS=1800
WATCHDOG_APPROVAL_TIMEOUT_SECONDS=14400
WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS=14400
WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS=14400
PLAN_APPROVAL_TIMEOUT_SECONDS=3600

# Provider fallback chain (V6.5 — all optional, defaults shown)
# Same-provider retry-with-backoff before failing over (Tier A errors only:
# rate_limit / timeout / provider_down — see Provider Fallback Chain below).
FALLBACK_RETRY_BASE_DELAY_S=0.5
FALLBACK_RETRY_MAX_DELAY_S=8.0
FALLBACK_MAX_RETRIES_RATE_LIMIT=2
FALLBACK_MAX_RETRIES_TIMEOUT=1
FALLBACK_MAX_RETRIES_PROVIDER_DOWN=2
# Cross-execution cooldown — once Tier A retries are exhausted for a
# (user, provider, model), skip it without attempting for this long on
# every subsequent execution; doubles on each consecutive failure.
FALLBACK_COOLDOWN_BASE_SECONDS=30.0
FALLBACK_COOLDOWN_MAX_SECONDS=900.0
FALLBACK_COOLDOWN_STREAK_TTL_SECONDS=3600

# Logging (V6.1 — all optional)
# EVA_LOG_DIR=/path/to/logs         # defaults to <project_root>/logs/
# EVA_LOG_SERVER_MAX_BYTES=52428800  # 50 MB per server.log before rotation
# EVA_LOG_OBS_PROPAGATE=false        # true = also copy OBS_EVENTs to server.log

# Workspace lifecycle cleanup
# EVA_WORKSPACE_TTL_DAYS=90          # days before conversation scratch dirs are deleted
# EVA_CLEANUP_INTERVAL_SECONDS=86400 # how often the cleanup task runs (24h)

# CORS (production)
# CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
```

All tunable constants and their environment variable names are documented in `config/settings.py`.

---

## Running EVA

EVA is a **single process**. One command starts everything: the API server, the execution worker, the daemon scheduler, and the broker.

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API is available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

### What Starts at Startup (lifespan sequence)

When the server starts, the `lifespan` context manager in `main.py` runs the following in order:

1. **Alembic migrations** — `alembic upgrade head` (idempotent; safe on every restart)
2. **LocalBroker** — starts the in-process asyncio broker (`core/broker/local_broker.py`)
3. **ChromaDB collection** — ensures the RAG collection exists (embedded, no external service)
4. **Orphaned execution recovery** — marks any executions that were `RUNNING` at last shutdown as `ABORTED`
5. **Capability registry refresh** — seeds the provider/model registry from LiteLLM
6. **ExecutionWorker** — starts as an `asyncio.create_task()` — begins draining the job queue
7. **DaemonScheduler** — starts as an `asyncio.create_task()` — begins polling daemon schedules
8. **Event subscriptions** — registers event-bus handlers for event-triggered daemons
9. **Workspace cleanup task** — starts as an `asyncio.create_task()` — runs every 24 hours, deletes conversation scratch directories not accessed in `WORKSPACE_TTL_DAYS` days (default 90)

All of this happens before the first request is served.

### Health Checks

```bash
# Liveness — is the process up?
curl http://localhost:8000/health

# Readiness — are all dependencies healthy? (requires JWT)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/health/ready
```

`GET /health/ready` checks DB, LocalBroker, ChromaDB, and worker heartbeat. Returns `503` if any subsystem is degraded.

### `run_worker.py`

`run_worker.py` still exists as a standalone entrypoint, but it is **not needed** in normal operation — the worker starts automatically inside the server process. It is retained for diagnostic use and future multi-process configurations.

---

## API Reference

All endpoints require `Authorization: Bearer <access_token>` unless noted.

---

### Authentication

#### POST /auth/signup

Create a new user account.

**Request:**
```json
{
  "username": "alice",
  "email": "alice@example.com",
  "password": "secure_password"
}
```

**Response (201):**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400,
  "refresh_token": "rft_abc123"
}
```

Status codes: `201 Created` | `400 Bad Request`

---

#### POST /auth/login

Authenticate and obtain tokens. Uses `application/x-www-form-urlencoded` (OAuth2 form).

**Request (form-encoded):**
```
username=alice&password=secret123
```

**Response (200):**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400,
  "refresh_token": "rft_abc123"
}
```

Status codes: `200 OK` | `401 Unauthorized`

---

#### POST /auth/refresh

Exchange a refresh token for a new access token (rotating tokens).

**Request:**
```json
{ "refresh_token": "rft_abc123" }
```

**Response (200):**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400,
  "refresh_token": "rft_def456"
}
```

Status codes: `200 OK` | `401 Unauthorized`

---

#### POST /auth/logout

Revoke the current session.

**Response (200):** `{ "message": "Logged out" }`

---

#### POST /auth/sse-token

Exchange a JWT for a short-lived (30 s), single-use SSE token. Required by browser `EventSource` which cannot send custom headers.

**Response (200):**
```json
{ "sse_token": "uuid-string", "expires_in": 30 }
```

---

#### GET /auth/me

Return the authenticated user's profile.

**Response (200):**
```json
{
  "user_id":     "usr_abc",
  "username":    "alice",
  "email":       "alice@example.com",
  "full_name":   "Alice Smith",
  "description": "Software engineer, Python enthusiast"
}
```

---

#### PATCH /auth/profile

Update the user's profile fields. All fields are optional; omit any you don't want to change.

**Request:**
```json
{
  "full_name":      "Alice Smith",
  "description":    "Software engineer, Python enthusiast",
  "preferred_name": "Ali"
}
```

- `full_name` — stored in the DB user record **and** locked into `user.full_name` memory (source=`profile` — not overwritable by LLM)
- `description` — stored in the DB user record **and** locked into `user.about` memory (source=`profile`)
- `preferred_name` — sets `user.name` memory fact (LLM-editable casual name). If empty string is sent, the entry is cleared. If omitted, the first word of `full_name` is used if `user.name` has never been set.

**Response (200):** Updated `UserProfileResponse` (same shape as `GET /auth/me`)

---

#### POST /auth/change-password

Change the authenticated user's password. Requires the current password for verification.

**Request:**
```json
{
  "current_password": "old_password",
  "new_password":     "new_secure_password"
}
```

- `new_password` must be at least 8 characters.

**Response (200):** `{ "message": "Password updated successfully" }`

Status codes: `200 OK` | `400 Bad Request` (wrong current password or weak new password)

---

### Chat & Execution

#### POST /chat

Submit a message and create an execution. Returns immediately — the actual AI response is streamed via `GET /eva_response`.

**Request:**
```json
{
  "messages": [{"role": "user", "content": "Summarise the files in my workspace"}],
  "conversation_id": "optional-existing-id"
}
```

**Response (201):**
```json
{
  "conversation_id": "conv_abc",
  "execution_id": "exec_xyz",
  "status": "accepted",
  "execution_mode": "plan_execute"
}
```

Status codes: `201 Created` | `409 Conflict` (execution already running for conversation)

---

#### GET /eva_response

SSE stream for an execution. Authenticate with `?sse_token=` (browser) or `Authorization: Bearer` header (API clients).

**Query params:** `conversation_id` (required), `sse_token` (optional)

Connects to the `LocalShadowBuffer` for the execution. All previously buffered chunks are replayed from the start — reconnects are idempotent.

See [Streaming Protocol](#streaming-protocol) for the full event catalog.

---

#### POST /cancel

Abort by conversation ID. Sets a cancel token on the in-process execution object; the worker detects it at the next safe checkpoint.

**Query params:** `conversation_id` (required)

**Response (200):** `{ "status": "cancelled" }`

---

### Session Configuration

#### GET /session

Returns the current session configuration. `provider`/`model` are `null` if the user hasn't called `POST /set-session` yet (V6.22) — this is a real, expected state for a fresh signup, not an error.

#### POST /set-session

Update session configuration. Any combination of fields may be sent.

```json
{
  "provider": "anthropic",
  "model": "claude-opus-4-5",
  "reasoning": "medium",
  "tools": ["read_file", "shell", "web_search", "rag_search"],
  "agent_mode": "auto",
  "tool_approval_mode": "always_ask",
  "fallback_chain": [
    { "provider": "openai",  "model": "gpt-4o" },
    { "provider": "ollama",  "model": "llama3" }
  ],
  "user_memory_enabled": true,
  "agent_memory_enabled": true,
  "agent_scope_key": "my-project",
  "require_plan_approval": false,
  "skills_mode": "auto",
  "shell_readonly": false,
  "user_timezone": "Asia/Kolkata",
  "user_location": "Mumbai, India",
  "user_language": "en-IN",
  "pii_redaction_enabled": false,
  "pev_lookahead_skip_enabled": true
}
```

`fallback_chain` is capped at 5 entries and deduplicated at save time — see [Provider Fallback Chain](#provider-fallback-chain-v65) for the full validation and retry/failover behaviour.

Session notes:
- `system_prompt` is **per-conversation** (set when creating/continuing a conversation), not a session field.
- **Streaming is always on.** There is no `stream` session flag — every execution streams over SSE. The frontend should always open `GET /eva_response` after `POST /chat`.
- `user_timezone`, `user_location`, `user_language` (V5.5 environment context) — free-text strings injected into the system prompt header on every execution to give the model situational awareness. These complement the `user.timezone` and `user.location` memory facts but operate independently of memory extraction.
- `pii_redaction_enabled` (S-03, off by default) — when true, the user's own chat message is scrubbed of PII (email, phone, SSN, credit card, bank/passport/license numbers — see `core/security/pii_redactor.py`'s `DEFAULT_PII_ENTITIES`) via an offline NLP engine (Microsoft Presidio) before it is persisted, journaled, or sent to the LLM. Deliberately does not redact names, locations, or dates — EVA needs those for normal function. A short system-prompt notice is injected whenever this is on, so the model treats a placeholder like `<EMAIL_ADDRESS>` as intentional rather than missing data. Tool-output redaction is separate: opt in per tool_hooks entry with `"handler": "redact_pii"` (same shape as the existing `redact_env_vars` handler).
- `pev_lookahead_skip_enabled` (V6.55, A-21, **on by default** — opt-out, unlike most of the booleans here) — when true, a plan/execute run's per-step verifier also checks whether a step's own evidence already satisfies one or more of the steps planned immediately after it, and the runner skips invoking the agent for those steps entirely (marked `status: "skipped"` in the plan, never silently dropped). Never skips a plan's own final step regardless of this setting. A hard, deployer-only ceiling (`PEV_LOOKAHEAD_MAX_SKIP`) additionally caps how many steps one verification can skip in a single jump.
- **No default provider/model (V6.22).** `provider` and `model` have no fallback value — a session that has never called this endpoint has both unset. Any endpoint that needs to make an LLM call (`/chat`, `/prompts/enhance`, `PATCH /session/permissions`) rejects with `424` and `{"error_code": "provider_not_configured"}` until this endpoint is called at least once. `fallback_chain` entries are only ever consulted once a primary is configured — EVA does not run on a fallback chain alone.
- **`reasoning` (V6.7) is a level string, not a boolean** — one of `"minimal"` / `"low"` / `"medium"` / `"high"` / `"xhigh"`, or `null`/`"off"` for disabled. A legacy `true`/`false` boolean is still accepted as a transition shim (`true` → `"medium"`, `false` → `null`) but is not a permanent contract. If the selected model doesn't support the requested level (or doesn't support reasoning at all, or — for a small set of the newest Claude models — can't be disabled at all), `POST /set-session` silently adjusts it to the nearest valid value and returns a warning explaining what changed, rather than rejecting the request. `GET /providers/{provider}/models/{model_id}/capabilities` exposes `reasoning_levels` (the model's valid vocabulary, in weakest→strongest order) and `reasoning_can_disable` so a client can build the right control before the user ever picks an invalid value.

```json
```

#### GET /session/permissions

Returns current tool authority levels and approval mode.

#### PATCH /session/permissions

Update `tool_approval_mode` or individual tool overrides.

> **Developer note — partial upsert by design:** This endpoint writes only the permission-related subset of `UserPreference` columns (provider, model, reasoning, tools, fallback_chain, tool_approval_mode, allowed_authorities, agent_mode). It intentionally omits: `require_plan_approval`, `user_memory_enabled`, `agent_scope_key`, `agent_memory_enabled`, `skills_mode`, `tool_hooks`, `shell_readonly`, `user_timezone`, `user_location`, `user_language`, `pii_redaction_enabled`, `pev_lookahead_skip_enabled`. Those fields are managed by `POST /set-session`. Any new `UserPreference` column must either have a SQLAlchemy-level default (so the partial INSERT path never violates a NOT NULL constraint), or be added to **both** the `/set-session` upsert and this endpoint.

---

### Conversations & Messages

| Endpoint | Description |
|----------|-------------|
| `GET /conversations` | List conversations (paginated) |
| `PATCH /conversations/{id}` | Update conversation metadata (title, system prompt, project link) |
| `GET /conversations/{id}` | Get conversation detail + message history |
| `DELETE /conversations/{id}` | Delete conversation and all messages |
| `DELETE /conversations` | Bulk-delete all conversations for the user |
| `GET /conversations/{id}/messages` | List messages (paginated) |
| `GET /conversations/{id}/messages/{message_id}` | Get a specific message by ID |
| `DELETE /conversations/{id}/messages?from_message_id={id}` | Truncate message history from a point (for edit/retry) |
| `GET /conversations/{id}/export` | Export conversation as JSON or Markdown |
| `POST /conversations/{id}/promote` | Move conversation files to a project's workspace |
| `POST /conversations/{id}/attachments` | Attach a library file, conversation reference, or execution reference to a conversation |
| `GET /conversations/{id}/attachments` | List conversation attachments |
| `DELETE /conversations/{id}/attachments/{ca_id}` | Remove a conversation attachment |

---

### Projects

| Endpoint | Description |
|----------|-------------|
| `POST /projects` | Create a project |
| `GET /projects` | List all projects |
| `GET /projects/{id}` | Get project detail |
| `PATCH /projects/{id}` | Update project metadata (name, description, status) |
| `DELETE /projects/{id}` | Delete project |
| `POST /projects/{id}/activate` | Set project as active (pauses all others — only one active project per user at a time) |
| `POST /projects/{id}/sync` | Manually re-trigger this project's sync daemon (the same one created automatically on project creation) |
| `GET /projects/{id}/context` | Get all six .eva workspace file contents |
| `GET /projects/{id}/artifacts/{filename}` | Get contents of a specific .eva artifact file |
| `POST /projects/{id}/files` | Upload a document (auto-indexed into RAG, async 202) |
| `GET /projects/{id}/files` | List project documents |
| `DELETE /projects/{id}/files/{file_id}` | Remove document + RAG entries |
| `GET /projects/{id}/conversations` | List conversations linked to this project |
| `GET /projects/{id}/agents` | List agents scoped to this project |
| `GET /projects/{id}/daemons` | List daemons scoped to this project |
| `GET /projects/{id}/metrics` | Aggregated project metrics: executions, tokens, cost, provider breakdown, conversation count, daemon health, open task count |

**Project status values:** `active` | `paused` | `archived`

Activating a project (`POST /projects/{id}/activate`) automatically pauses all other projects for that user. Archived projects cannot receive new messages.

---

### Agents (Subagents)

| Endpoint | Description |
|----------|-------------|
| `GET /agents` | List all agents (pass `?project_id=<id>` to filter to a project; includes both default and custom agents) |
| `POST /agents` | Create a subagent config (upsert — posting an existing name replaces it) |
| `GET /agents/{name}` | Get agent detail |
| `PUT /agents/{name}` | Update agent config in full |
| `DELETE /agents/{name}` | Delete agent |
| `POST /agents/{name}/clone` | Clone an agent (default or custom) under a new name — the clone has `is_default=false` and is fully editable |
| `POST /agents/{name}/reset` | Reset a default agent's config back to its shipped YAML definition |

Agent config fields: `name`, `description`, `system_prompt`, `provider`, `model`, `tools`, `max_steps`, `project_id` (optional scope), `is_default`

**Default agents** — EVA ships 12 built-in agents defined in YAML files (`default_agents/`):

| Name | Role |
|------|------|
| `deep-research` | Deep research with web search |
| `code-review-agent` | Code quality and security review |
| `coding-agent` | Implementation and refactoring |
| `testing-agent` | Test generation and validation |
| `git-agent` | Git operations and branch management |
| `file-system-analyst` | Workspace navigation and analysis |
| `documentation-writer` | Technical writing and docs generation |
| `security-reviewer` | Security audits and vulnerability detection |
| `data-analyst` | Data analysis and visualization |
| `error-diagnostician` | Error investigation and root-cause analysis |
| `deployment-agent` | Deployment automation and ops |
| `self-improvement-agent` | Meta-agent for improving other agents and skills |

Default agents have `is_default=true` — their tool lists cannot be directly modified. Use `POST /agents/{name}/clone` to get a fully editable copy, or `POST /agents/{name}/reset` to restore the original definition after customisation. Default agents cannot be assigned to a project scope — clone first.

**V6.23: subagents can also be built conversationally**, without touching this API at all — a user
can just ask EVA to create/edit/delete a subagent in normal chat. Backed by the `agent-builder`
skill (auto-seeded for every user) plus four default tools
(`create_subagent_config`/`get_subagent_config`/`edit_subagent_config`/`delete_subagent_config`)
that write to the same `SubagentRegistry` this REST API uses — both paths produce identical rows.

---

### Daemons

| Endpoint | Description |
|----------|-------------|
| `GET /daemons` | List all daemons |
| `POST /daemons` | Create a daemon |
| `GET /daemons/{name}` | Get daemon config detail |
| `PUT /daemons/{name}` | Update daemon config in full |
| `DELETE /daemons/{name}` | Delete daemon |
| `POST /daemons/{name}/clone` | Clone a daemon under a new name |
| `POST /daemons/{name}/trigger` | Manually trigger a daemon run |
| `GET /daemons/{name}/status` | Lifecycle status: `{state, last_run_at, next_run_at, failure_count}` |
| `POST /daemons/{name}/pause` | Pause daemon scheduling |
| `POST /daemons/{name}/resume` | Resume a paused daemon |
| `POST /daemons/{name}/reset-failures` | Reset failure count and re-enable a permanently-failed daemon |
| `POST /daemons/{name}/cancel` | Cancel a currently-running daemon execution |
| `GET /daemons/{name}/executions` | Recent execution history |
| `POST /daemons/{name}/webhook` | Create a webhook token for the daemon |
| `GET /daemons/{name}/webhook` | Get webhook token detail |
| `DELETE /daemons/{name}/webhook` | Delete webhook token |
| `POST /webhooks/{token}` | Public webhook endpoint — trigger daemon by token (no auth required; rate-limited) |

**V6.23: daemons can also be built conversationally**, the same way as subagents above — backed by the `daemon-builder` skill and four default tools (`create_daemon_config`/`get_daemon_config`/`edit_daemon_config`/`delete_daemon_config`) writing to the same `DaemonRegistry` this REST API uses. Live-verified end-to-end: a real conversation creating a daemon, confirmed via a direct `GET`, then deleting it, confirmed gone via `404`. Note EVA's daemons remain interval-only (`run_interval_seconds` since last completion) — the builder skill is explicitly instructed not to silently map a wall-clock request like "every morning at 9am" onto that without flagging the difference.

**V6.24: `event_subscriptions` (event-triggered daemons) are now project-scoped.** A daemon with
`project_id` set only fires when the *triggering* execution belongs to the same project — previously
any enabled daemon subscribed to `execution.completed`/`execution.failed` fired on every matching
execution across the whole account, regardless of which project either belonged to. A daemon with no
`project_id` (global) is unaffected — it still fires on any trigger. Found during a gap-tracker
verification sweep, not a user report.

---

### Memory

| Endpoint | Description |
|----------|-------------|
| `GET /memory/chats` | List recent chat summaries (Scope 1) |
| `DELETE /memory/chats/{id}` | Delete a chat summary |
| `GET /memory/user-facts` | List all user fact entries (Scope 2) |
| `GET /memory/user-facts/summary` | Get the rendered `facts.md` summary text (pass `?refresh=true` to force rebuild) |
| `POST /memory/user-facts` | Create a user fact entry manually |
| `PUT /memory/user-facts/{fact_id}` | Update a user fact (fact text, slot_key, confidence, source, etc.) |
| `DELETE /memory/user-facts/{fact_id}` | Delete a single user fact |
| `DELETE /memory/user-facts` | Delete all user facts (bulk wipe) |
| `GET /memory/agent` | List agent memory scope keys for the user (`[{scope_key, entry_count}]`) |
| `GET /memory/agent/{scope_key}` | List all entries within a scope |
| `POST /memory/agent/{scope_key}` | Add an entry to a scope |
| `PUT /memory/agent/{scope_key}/{entry_id}` | Update an agent memory entry |
| `DELETE /memory/agent/{scope_key}/{entry_id}` | Delete a specific entry |
| `POST /memory/agent/{scope_key}/{entry_id}/grant-access` | Grant access to a sensitive memory entry for the current session |
| `GET /memory/agent/{scope_key}/{entry_id}/grant-access` | Check whether an access grant is active |
| `DELETE /memory/agent/{scope_key}/{entry_id}/grant-access` | Revoke a sensitive memory access grant |
| `DELETE /memory/agent/{scope_key}` | Delete an entire scope |
| `GET /memory/summary` | Diagnostic overview of all memory scopes and entry counts |

---

### RAG

| Endpoint | Description |
|----------|-------------|
| `POST /rag/ingest` | Upload and index a document |
| `POST /rag/search` | Semantic search |
| `GET /rag/files` | List indexed files (`{ "files": [...] }`) |
| `DELETE /rag/files/{file_id}` | Remove file and its vectors |

---

### Tools

| Endpoint | Description |
|----------|-------------|
| `GET /tools` | Returns `[]` array of all available tool manifests |
| `GET /tools/{name}` | Get tool detail (name, description, schema, authority) |

---

### Providers

| Endpoint | Description |
|----------|-------------|
| `GET /providers` | Returns `[]` array of all provider/model entries |
| `GET /providers/{provider}` | List models for a specific provider |
| `POST /providers/refresh` | Force-refresh the capability registry from LiteLLM |

---

### MCP Servers

MCP server configs are identified by their database `config_id` (UUID), not by name.

| Endpoint | Description |
|----------|-------------|
| `GET /mcp/servers` | List all configured MCP server configs |
| `POST /mcp/servers` | Register a new MCP server config |
| `GET /mcp/servers/{config_id}` | Get server config detail |
| `PUT /mcp/servers/{config_id}` | Update server config |
| `DELETE /mcp/servers/{config_id}` | Remove server config |
| `POST /mcp/servers/{config_id}/sync` | Force sync tools from the server (V6.50, B-100: a connection failure now returns `200` with `sync_error` set, like the enable/update path already did, instead of an unhandled `500`) |
| `GET /mcp/servers/{config_id}/health` | Check server connectivity |
| `GET /mcp/servers/{config_id}/resources` | List available MCP resources |
| `GET /mcp/servers/{config_id}/resources/read?uri=<uri>` | Read a specific MCP resource by URI |
| `GET /mcp/servers/{config_id}/prompts` | List available MCP prompts |
| `POST /mcp/servers/{config_id}/prompts/{prompt_name}` | Invoke an MCP prompt by name |
| `GET /mcp/{config_id}/tools` | Return cached tool metadata for a config from DB (last-synced state — no live connection) |

There is also a global `GET /mcp/health` endpoint that returns connectivity status for all configured MCP servers at once.

**CDP-embedded preview pane (V6.56).** Three endpoints let EVA's own Electron
app register a live browser CDP endpoint so the agent's `playwright` MCP
attaches to a window embedded in the app itself, instead of spawning its own
separate OS window — the user watches the agent navigate/click/test in real
time, in-app. Backend-only change; the Electron pane, its CDP relay, and the
chat-thumbnail rendering are built separately.

| Endpoint | Description |
|----------|-------------|
| `POST /session/preview-endpoints` | Upsert a named CDP endpoint (`{"name": "preview", "cdp_endpoint": "ws://127.0.0.1:<port>/<path>"}`). Called once by Electron right after its relay comes up. |
| `DELETE /session/preview-endpoints/{name}` | Remove a named registration. Idempotent — deleting an already-absent name is not an error. |
| `GET /session/preview-endpoints` | List current registrations (debugging only; not load-bearing). |

In-memory only (`core/mcp/preview_registry.py`), keyed by name, never
persisted — the relay's port differs every launch, so nothing here should
survive a restart. Deliberately separate from `SessionConfig`: it's
infrastructure plumbing set once by Electron's own relay, not a user
preference, and doesn't ride along on `GET`/`POST /session`. Hardcoded
relationship for v1: registering/unregistering `"preview"` always targets the
`"playwright"` MCP config specifically (not generic dependency tracking —
there's only one consumer today).

When a `"preview"` endpoint is registered and `playwright` is enabled, the
config is resynced immediately: the pooled connection is force-closed right
now (`bump_generation()` + `discard()`, the same active-close pair `DELETE
/mcp/{id}` already uses — plain `bump_generation()` alone is lazy and only
takes effect on the *next* tool call, which would leave an already-spawned
standalone browser window orphaned if no further tool call happens to
trigger the swap) and its tools are re-synced against the freshly-resolved
args. The same resync fires on `DELETE` too, so a CDP-attached connection
doesn't sit pointed at a relay that just closed.

Args resolution happens at actual connection time (`resolve_connection_args()`
in `core/mcp/preview_registry.py`), not baked in once at seed time like
`{workspace}`/`{browser_channel}` — checked at both real connection-construction
sites (`core/mcp/session_pool.py`'s pooled tool-call path, `core/mcp/manager.py`'s
sync/health path), so a pane opening or closing mid-session takes effect on the
very next connection attempt. Not a `{cdp_endpoint}` string substitution: the
two modes are entirely different, mutually exclusive arg lists (`--browser`/
`--isolated` are meaningless once attached to an existing browser via
`--cdp-endpoint`), so it's a whole-list swap, not a token patch.

**Default MCPs (M-01/M-02, V6.50):** `default_mcps/*.yaml` are seeded into every new user's account at signup, and into an existing user's on demand via `POST /mcp/seed-defaults` (insert-only and name-keyed — an MCP the user already has is skipped, never overwritten; the response lists any whose required executable, e.g. `npx`, is missing under `mcps_pending_install`). Seeded configs are created **disabled** and never connect to their server at seed time; enabling one (the existing toggle / `PUT /mcp/{id}`) syncs its tools. The first default is **`playwright`** — browser automation (`@playwright/mcp`, pinned; Edge on Windows, Chrome elsewhere; in-memory profile; screenshots written to `<workspace>/_browser_output` rather than inlined). It gives the agent a way to open the app it built, read the page snapshot, console errors and failed network requests, and click/type — the runtime feedback the build-test-fix loop needs. To hide the browser window, add `--headless` to the config's `args`. An MCP tool result containing an image (e.g. a screenshot) is replaced by a short placeholder in the model's context **unless** the current model is a confirmed vision-capable one (M-07, V6.50 — see "Vision" below), in which case it is now sent as real image content instead.

**Vision (M-07, V6.50).** A real image now reaches a model instead of a text placeholder, but only for a model with a *confirmed* `supports_vision: True` in `core/providers/capability_registry.py` (`EVA_VISION_ENABLED` is a global kill-switch on top of that, default on) — missing/unknown capability data, the overwhelming majority of models today, still gets the old placeholder, unchanged. Two entry points: an MCP tool result image (above), and a chat-attached image — `POST /conversations/{id}/attachments` already rejected attaching an image to a non-vision-capable session (`422`); the attachment is now actually reconstructed from `Attachment.blob_path` and sent as real content at request time, not just a `"[IMAGE ATTACHMENT] ... refer to it"` note. Neither path changes what's stored in the DB — `Message.content` stays plain text; the real bytes are assembled only when a request is built for the provider, then discarded. An image over `IMAGE_INLINE_MAX_BYTES` (5 MB default) falls back to the existing placeholder rather than being sent or dropped. `core/providers/multimodal.py` is the shared implementation (OpenAI's own content-part list as EVA's canonical multimodal shape, since `OpenAIHandler`/`OllamaCloudHandler` already pass `messages` straight through unchanged to their SDK); `AnthropicHandler._convert_messages` is the only handler that needed a real translation layer, into Anthropic's own image-block shape, plus a general fix so adjacent same-role turns (a tool-result turn immediately followed by the new image message, both `role="user"`) merge into one — Anthropic requires strict alternation. Built and unit-tested (74 tests); **not yet live-verified against a real vision-capable model** — none is funded/configured on this account as of writing.

**S-11 (V6.48):** any key in `env`/`headers` that looks like a credential (name matching `KEY`/`TOKEN`/`SECRET`/`PASSWORD`/`AUTH`/`SESSION`/`CREDENTIAL`/`COOKIE`/`AUTHORIZATION`, case-insensitive) is automatically extracted to the OS keyring at create/update time and replaced in the stored config with a `"••••••••"` placeholder — the real value is never persisted to the DB and never returned by any read endpoint. This runs on the exact `env`/`headers` payload already submitted today; there's no separate `secrets` field to fill in. The real value is resolved from keyring only at actual connection time (health check, resource/prompt calls, and the pooled tool-call path).

**V6.16:** synced MCP tools now actually reach the model. Previously, `dynamic_registry` (the object
holding synced MCP tools) and `UserToolRegistry` (the object that builds the model-facing tool list/
manifest) were completely separate — sync/registration worked, but nothing ever told the model an
MCP tool's name existed, so it could never organically call one. MCP tools are now listed in the
system-prompt manifest and resolved through `describe_tool()` on demand, exactly like every other
tool — manifest-listed, not pre-seeded, so an unbounded number of user-configured MCP tools doesn't
reintroduce the context-bloat problem progressive disclosure exists to prevent. Subagents and
daemons were affected too (they had zero MCP access at all, not even the by-name fallback) and are
now fixed: subagents/parallel-delegate children inherit the parent's synced MCP tools gated by their
own `cfg.tools` allow-list; daemons (no parent execution to inherit from) sync their own MCP tools
independently.

**V6.17:** MCP connections are now reliable, not just reachable. Every tool call used to open a
fresh connection (200-800ms cold start for stdio servers) with no reconnect path and no way for a
confirmed-dead server's tools to stop being offered to the model. Four pieces, in dependency order:
**M-04** (reconnect-once-on-failure, later superseded), **P-06a** (tool registration is now a pure
DB-cache read — no live connection just to build a manifest), **P-06b** (`core/mcp/session_pool.py`
— a process-local, per-`config.id` connection pool: lazy-opened, reused across calls, pre-call
`send_ping()` liveness check, evict-and-retry-once on failure, config-change eviction via generation
counter, idle-TTL reaper — mirrors `core/broker/local_broker.py`'s singleton+reaper shape), and
**pruning** (a confirmed-dead server's tools are removed from the active registry with a clear "now
unavailable, do not retry" message, instead of staying listed forever). Live-verified end to end
against a real MCP server: call succeeds → break the server → next call detects it, tries to
reconnect, fails, prunes, and tells the model exactly that.

**V6.18:** every pooled MCP connection is now owned by exactly one
dedicated `asyncio.Task` (`core/mcp/session_pool.py`'s `_OwnedConnection`)
-- that task, and only that task, ever opens or closes it; every other
caller talks to it through a queue, never the connection object directly.
This is a structural guarantee against a class of bug EVA hit once before
(`RuntimeError: Attempted to exit cancel scope in a different task than it
was entered in`)
that the persistent pool could in principle reintroduce via cross-execution
reuse; tested directly against real stdio/SSE/streamable-HTTP servers and
didn't reproduce it, but hardened anyway rather than relying on that
holding forever. Also fixed two real bugs found while investigating:
`stop()` now closes pooled connections concurrently instead of
sequentially (sequential closing risked exceeding the desktop app's
Electron SIGTERM grace window with several servers pooled), and
`discard()` (`DELETE /mcp/{id}`) now actually closes the connection before
dropping it -- previously leaked on every deletion. Automated crash-orphan
process tracking was considered and deliberately not built (evaluated as
race-prone for the actual severity involved).

**V6.19:** MCP tool schemas with real structure -- nested objects, `$ref`, `anyOf`/`oneOf`, `allOf`
-- now convert correctly instead of collapsing to an opaque object or, worse, silently mistyping the
field as a plain string. `core/tools/tool_adapter.py`'s JSON-Schema-to-Pydantic converter now
recurses properly; `core/tools/builder.py` (schema sent to the LLM provider) and
`core/tools/dispatcher.py` (argument validation) needed zero changes, since both already just trust
whatever Pydantic model a tool has -- the bug was entirely in that one converter building a shallow
model in the first place. Verified against a real schema captured live from a FastMCP test server
with a Pydantic-`BaseModel`-typed parameter, not just hand-crafted test cases. Also audited EVA's
own native tools for an equivalent bug (they can't have one -- they're written directly as Pydantic
classes, never needing the JSON→Pydantic conversion MCP tools require) and confirmed via
`parallel_delegate_tool.py`'s genuinely nested `tasks: List[ParallelTask]` field, the one native
tool with comparable structure.

**V6.39:** MCP tool naming convention changed from `"<config_name>.<tool_name>"` to
`"<config_name>__<tool_name>"` (double underscore) -- the dot broke every major provider's tool-name
validation (Anthropic: `^[a-zA-Z0-9_-]{1,128}$`, no dots) the moment a synced MCP tool got promoted
into the model's active toolkit, live-confirmed as a real (previously unnoticed) end-to-end failure,
not a theoretical one. `core/mcp/naming.py` is now the single source of truth for building/parsing
this compound name -- previously duplicated as raw string logic in four places
(`core/mcp/manager.py`, `api/tools.py`, `core/mcp/runtime.py` x2). MCP config names containing `__`
are now rejected at creation/update time (`POST /mcp/`, `PUT /mcp/{id}`) so the split stays
unambiguous by construction.

---

### Hook Scripts

Hook scripts run at defined points in the execution lifecycle (`pre_execution`, `post_execution`, `pre_tool`, `post_tool`). They receive the execution context as environment variables and can modify behavior.

| Endpoint | Description |
|----------|-------------|
| `POST /hooks` | Upload a hook script |
| `GET /hooks` | List all hooks |
| `GET /hooks/{name}` | Get hook detail |
| `PATCH /hooks/{name}` | Update hook |
| `DELETE /hooks/{name}` | Delete hook |

---

### Skills

Skills are named, versioned capability bundles stored in `~/eva_workspace/{user_id}/skills/`. Each skill is a directory containing a `SKILL.md` file (YAML frontmatter + markdown body) and optional supporting files. Skills are injected as a compact menu into the system prompt (controlled by `skills_mode` in session config) — an untriggered skill is always listed; a skill with a `keyword`/`task` trigger is listed only when the current message matches; a skill with a `path` trigger is never listed here (P-14).

| Endpoint | Description |
|----------|-------------|
| `GET /skills` | List all skills for the user |
| `POST /skills` | Create a new skill (inline definition) |
| `POST /skills/install` | Install a skill from a URL or package |
| `POST /skills/reload` | Force reload all skills from disk |
| `DELETE /skills/{name}` | Delete a skill and its files |
| `GET /skills/{name}/files` | List files inside a skill directory |
| `GET /skills/{name}/files/{filepath}` | Get content of a specific file in the skill |
| `PUT /skills/{name}/files/{filepath}` | Write or update a file in the skill directory |
| `DELETE /skills/{name}/files/{filepath}` | Delete a file from the skill directory |


**Shipped skills** (`skills/`, copied into each user's folder at signup and back-filled at boot without overwriting edits) include `playwright-browser-testing` (V6.50): how to use the Playwright MCP's `playwright__browser_*` tools with `background_process` to debug a running web app — the console/network/snapshot loop, and the parameter mistakes models made in live runs (`filename`/`all` on the console tool, `ref` instead of `target`, `create` instead of `new` for tabs). It carries a `keyword` trigger, so it is only listed for messages about browsers, pages, sites, dev servers and the like.

Three things to know when editing skills:
- `allowed-tools` in the front matter is informational (nothing enforces it) but must name real tools. Guarded by `tests/test_prompt_tool_references_unit.py`, which also checks every default agent's `tools:` list and forbids instructions that tell a model to call an EVA REST endpoint (it cannot).
- A `skills/<name>.skill` ZIP bundle is auto-extracted over `skills/<name>/` every time that directory is loaded, silently undoing edits to the folder. Keep bundle and folder identical (also tested); never load the real `skills/` directory from a test — copy it without `*.skill` first.
- The skills menu tells the model to open a skill with the `read_file` tool.

---

### Attachments

| Endpoint | Description |
|----------|-------------|
| `POST /attachments` | Upload a file to the attachment library |
| `GET /attachments` | List all attachments |
| `DELETE /attachments/{id}` | Delete attachment |

---

### Secrets

| Endpoint | Description |
|----------|-------------|
| `POST /secrets` | Store a named secret in Windows Credential Manager |
| `GET /secrets` | List secret names and metadata (values are never returned) |
| `POST /secrets/{name}/rotate` | Replace a secret's value while preserving its name |
| `DELETE /secrets/{name}` | Delete a secret |

Secrets (including all LLM provider API keys) are stored in **Windows Credential Manager** via the `keyring` library under the service name `"eva"`. They are protected by DPAPI (Windows Data Protection API) — encrypted at rest using the OS user's credentials, not an application-level key. Values are never written to disk in plaintext and never returned by the API. They are decrypted at runtime and injected into tool execution environments as environment variables when referenced by name.

---

### Approvals (Human-in-the-Loop)

| Endpoint | Description |
|----------|-------------|
| `POST /executions/{id}/approve` | Approve a pending tool call |
| `POST /executions/{id}/reject` | Reject a pending tool call (execution cancelled) |
| `POST /executions/{id}/approve-plan` | Approve a PEV plan (when `require_plan_approval=true`) |
| `POST /executions/{id}/cancel` | Cancel a specific execution by ID |
| `POST /executions/{id}/retry` | Retry a failed or cancelled execution with the same inputs |
| `GET /executions/{id}/status` | Lightweight status check `{execution_id, status, conversation_id}` |
| `GET /executions/{id}/metrics` | Per-execution performance metrics (tokens, cost, duration, tool stats) |
| `GET /executions/{id}/trace` | Full execution trace (all steps, tool calls, and results) |
| `GET /executions/{id}/plan` | Retrieve the PEV plan for a `plan_execute` execution |

**V6.40 — Execution gate crash recovery (B2-1.7/4.3).** The three blocking HitL gates
(`gate_approval`, `_gate_plan_approval`, `gate_step_extension`) previously had no restart-safety:
all of their state lived in-memory (broker keys + the suspended coroutine itself), so a server crash
while paused left the execution stuck forever in an unresolvable `awaiting_*` state. Fixed with a
file-based liveness marker (`core/execution/gate_markers.py`, one small JSON file per
currently-paused execution under `EXECUTION_RUNTIME_DIR` — derived from Electron's
`EVA_CONFIG_DIR`/`app.getPath('userData')` in the packaged build, `.runtime/executions/` in dev)
rather than a new DB table, since SQLite here is deliberately scoped to record only *completed*
executions. A boot-time scan (`LocalBroker._recover_from_gate_markers()`, folded into the existing
`recover_orphaned_executions()`) finds any marker left over from a crash, writes exactly one
terminal `execution_metrics` row (`status=aborted`, `termination_reason=process_restart`), and
deletes the marker — never becomes durable history. `GET /executions/{id}/status` also gained a
fallback to `execution_metrics` when the in-memory registry is empty (e.g. after a restart) —
previously 404'd for *any* execution predating the current process, not just recovered ones.

---

### Events

| Endpoint | Description |
|----------|-------------|
| `GET /events/stream` | SSE stream of user-facing events (execution completed/failed/cancelled, daemon completed/failed, conversation titled). Authenticate with `?sse_token=` or `Authorization: Bearer`. Heartbeat every 30 s. |

Event payload shape:
```json
{
  "event":        "execution.completed",
  "execution_id": "exec_abc",
  "user_id":      "usr_xyz",
  "daemon_name":  null,
  "summary":      "Task finished in 4.2 s",
  "ts":           1717000000.0
}
```

Supported event types: `execution.completed` | `execution.failed` | `execution.cancelled` | `daemon.completed` | `daemon.failed` | `conversation.titled`

> **V6.10:** each SSE frame is tagged with an `event: <name>` line (the
> payload's own `"event"` field), not just `data:` — so
> `EventSource.addEventListener(name, ...)` fires for the correct named
> type client-side instead of only the generic `"message"` type. Previously
> only `data:` was sent, which silently broke every named listener (this
> is what caused the conversation-title-bar bug).

---

### Metrics

| Endpoint | Description |
|----------|-------------|
| `GET /metrics/session?conversation_id=<id>` | Aggregate all execution metrics for one conversation (tokens, cost, duration, provider breakdown) |
| `GET /metrics/usage/summary` | All-time aggregate metrics for the user across every conversation and daemon execution |
| `GET /metrics/usage?start=<iso>&end=<iso>` | Time-windowed aggregate (both params optional — omit either for open-ended window) |

---

### Prompt Enhancer

| Endpoint | Description |
|----------|-------------|
| `POST /prompts/enhance` | Enhance a user prompt using an LLM rewrite pass — returns a clarified, more structured version of the input prompt |

---

## Provider Fallback Chain (V6.5 / V6.8)

### The Problem It Solves

Every production AI application faces the same silent failure mode: the LLM provider goes down, hits a rate limit, or throttles a model — and the user gets an error. EVA solves this at the engine level.

A **fallback chain** is an ordered list of provider/model pairs in the user's session. When the primary provider fails, EVA classifies the failure and — depending on what kind of failure it is — either retries the same provider with backoff, fails over to the next candidate in the chain, or stops immediately, all without interrupting the response stream.

**A primary provider/model must always be configured (V6.22)** — `fallback_chain` is strictly optional and is only ever consulted once a primary exists; EVA does not run on a fallback chain alone with no primary set. A proposal to make an active fallback candidate the new "primary" (with automatic restoration once the original recovers) was evaluated and rejected: the existing TTL-based cooldown mechanism below already produces that exact behaviour — a failed candidate is temporarily skipped and the original is retried automatically once its cooldown expires — without a second, mutable "current primary" value that would need to be kept in sync.

### A Three-Tier Failure Model

Not every error should be handled the same way, so EVA classifies every provider failure into one of three tiers before deciding what to do (`core/error_handling.py`):

| Tier | Error types | Behaviour |
|------|-------------|-----------|
| **A — Transient** | `rate_limit`, `timeout`, `provider_down` | Retry the *same* provider first, with exponential backoff + jitter, up to a per-type retry cap. Only fails over once retries are exhausted. |
| **B — Structural** | `invalid_model`, `content_policy` | No retry — this provider will never succeed on this request. Fails over to the next candidate immediately. |
| **B′ — Auth (scoped)** | `auth` | Fallback-eligible like Tier B, but every remaining candidate on the *same* provider is skipped too (they likely share the same credential). A candidate on a different provider is still tried. |
| **C — Hard stop** | `invalid_request`, `internal`, `unknown` | No retry, no fallback — nothing downstream can fix these. The execution ends `FAILED` with the real error surfaced. |

Classification is based on each provider SDK's own structured exception data (`.status_code`, `.type`, exception class) rather than string-matching error text — openai/groq/anthropic (Stainless-generated clients), litellm, and ollama-python are all supported. A fallback is **never** triggered by bad LLM answers, tool errors, or user cancellation.

### Cross-Execution Cooldown (Circuit Breaker)

Once a `(user, provider, model)` candidate exhausts its Tier A retries in one execution, it's put into a short cooldown (default 30s, doubling on repeated failures up to 900s) — every *other* execution that reaches that candidate skips it without even attempting it until the cooldown expires. This is in-memory only (no external Redis — see `core/broker/local_broker.py`), resets on server restart, and is fully eva.env-overridable (see `FALLBACK_COOLDOWN_*` in [Installation](#installation)).

### Configuring the Chain

```http
POST /set-session
{
  "provider": "anthropic",
  "model": "claude-opus-4-5",
  "fallback_chain": [
    { "provider": "openai",   "model": "gpt-4o" },
    { "provider": "groq",     "model": "llama-3.3-70b-versatile" },
    { "provider": "ollama",   "model": "llama3" }
  ]
}
```

An empty `fallback_chain` means no fallback — if the primary fails, the execution fails. This is the default for new users.

- **Capped at 5 entries** (primary + up to 5 fallbacks) — a 6th entry in the request body is rejected with `422`.
- **Duplicates are dropped, not rejected** — an entry matching the primary, or an earlier entry already in the chain, is silently dropped with a warning in the response (first occurrence wins; chain order is priority order).
- **Credentials are validated at save time** — each entry needs a resolvable API key (its own `api_key`, or the provider's env var) *when the session is saved*, not when it's eventually used. An entry with no resolvable key is dropped with a warning rather than saved; the primary provider with no key is rejected outright (`424`).

> **Schema note (for developers):** `fallback_chain` is stored as JSON and is `nullable=True` in the database by design. A `NULL` value is semantically identical to an empty list — the user has simply not configured a fallback chain. Every read site in the codebase normalises this with `prefs.fallback_chain or []`. If you add code that reads this column, apply the same pattern; do not treat `NULL` as an error or an unexpected state.

### Transparency

- **The stream is never interrupted** — a failure surfaces as a normal typed `ErrorChunk`/`event: error` SSE frame mid-stream, never a dropped connection; the fallback/retry loop happens transparently before any chunk reaches the client.
- **A live, minimal SSE notice on switch** — `event: provider_fallback` (`{"provider": ..., "model": ...}`) fires once, only for whichever candidate actually ends up serving the response — covers both an active in-turn failure and a candidate silently skipped via cooldown. Deliberately minimal (no failure detail) to keep the experience calm rather than alarming; full per-attempt detail is in metrics (below). Backend-only today — not yet rendered by the frontend.
- **Metrics record the actual provider** — `GET /executions/{id}/metrics`'s `provider_used`/`model_used` reflect whichever candidate actually served the response (not the originally-configured primary), `fallbacks_triggered` and `fallback_details` give the full per-attempt history, and a fully-exhausted chain correctly reports `status: "failed"` with a real error message rather than a silent empty success.
- **The session is not mutated** — a fallback is per-request only.

See [Diagram 4 — Provider Fallback](#4-provider-fallback-v65) for the internal execution flow.

### Beyond the Chat Turn (V6.8)

V6.5 hardened `AgentEngine`'s main chat/daemon path. V6.8 extends the same
retry/fallback/cooldown machinery to every other place EVA calls an LLM —
title generation, the prompt enhancer, memory extraction/summarization,
project artifact updates, multi-agent coordination planning, and the
Verifier/Planner/IntentRouter trio. Two access patterns, chosen per call
site based on whether a failure gets a natural second attempt from
something outside itself:

- **Resolve-Attempt-Record** — title generator, prompt enhancer, memory
  extractor, conversation-ref attachment linking. Tries the one best
  (cooldown-filtered) candidate, and on failure records it and stops — the
  *next* invocation (a user retrying, the next conversation turn) resolves
  to a different candidate automatically, because it reads the same
  cross-execution cooldown store the failed attempt just wrote to.
- **Reactive Loop** — coordination planning/synthesis, conversation
  summarization, project artifact updates. Walks the whole candidate list
  within one call, same Tier A/B/C decision logic as the main chat path,
  just adapted for a single non-streaming call instead of a stream.

The underlying cooldown store (`core/broker/local_broker.py`, per `(user_id, provider, model)`) was
already shared process-wide — V6.8's actual work was extracting the access functions out of
`AgentEngine` into `core/providers/fallback_state.py` so every call site can read and write the same
state the main chat path already does, instead of each rediscovering a dead provider independently.

---

## Streaming Protocol

All events follow SSE format: `event: <type>\ndata: <JSON>\n\n`

| Event | Payload | Notes |
|-------|---------|-------|
| `init` | `{ conversation_id }` | Connection handshake |
| `text` | `{ type, content, timestamp, conversation_id }` | Text chunk from model |
| `reasoning` | `{ type, content, timestamp }` | Model reasoning (extended thinking) |
| `tool_call` | `{ type, call_id, tool_name, arguments, timestamp }` | Tool invocation. `call_id` uniquely identifies this call within the execution |
| `tool_result` | `{ type, call_id, tool_name, result, timestamp }` | Tool result. Match to `tool_call` via `call_id` |
| `step_progress` | `{ type, step_index, step_title, total_steps, timestamp }` | PEV — step starting |
| `approval_required` | `{ type, execution_id, tool_name, authority, args_preview, approval_token }` | HitL gate — call approve or reject |
| `plan_approval_required` | `{ type, execution_id, plan_approval_token, plan }` | PEV plan awaiting approval |
| `subagent` | `{ type, agent_name, chunk }` | Live progress from delegated subagent |
| `usage` | `{ type, completion_tokens, prompt_tokens, timestamp }` | Token usage |
| `provider_fallback` | `{ type, provider, model }` | V6.5 — fires once when a fallback candidate takes over; see [Provider Fallback Chain](#provider-fallback-chain-v65) |
| `error` | `{ type, error_type, message, timestamp }` | Non-fatal or fatal error |
| `ping` | `{}` | Keepalive heartbeat (every 10 s) |
| `finish` | `{ status }` | Execution finished (`completed`, `cancelled`, `aborted`, `failed`) |
| `done` | `{}` | Stream closed |

Clients should reconnect on disconnect — the `LocalShadowBuffer` ensures all prior chunks are replayed from the beginning.

---

## Execution States

See [Diagram 9 — Execution State Machine](#9-execution-state-machine) for the full state diagram.

| State | Meaning |
|-------|---------|
| `PENDING` | Queued in `asyncio.Queue`, not yet started by worker |
| `PLANNING` | PEV loop generating step plan via LLM |
| `AWAITING_PLAN_APPROVAL` | Plan generated; waiting for user approval before execution |
| `RUNNING` | Executing (ReAct loop or PEV step) |
| `AWAITING_APPROVAL` | Paused at HitL tool gate; waiting for user approve/reject |
| `COMPLETED` | Finished successfully |
| `CANCELLED` | Stopped by `POST /executions/{id}/cancel`, rejected/timed-out HitL approval, or pre-queue cancel |
| `ABORTED` | Stopped by `POST /cancel` (by conversation ID); worker detected cancel token at safe checkpoint. Also used by boot-time crash recovery (V6.40) for an execution that was paused at a HitL gate when the process last died (`termination_reason=process_restart`) |
| `FAILED` | Terminated due to unrecoverable error |

---

## Memory System

EVA manages three memory scopes. Scopes 2 and 3 are injected into every execution's system prompt. Scope 1 (cross-conversation summaries) is produced but **not** auto-injected (see below).

### Scope 1 — Chat Memory (produced, not injected)
`ConversationSummary` rows are generated after long conversations for in-conversation context compression (overflow path). They are also used by the `conversation_ref` attachment endpoint for user-initiated context pulls.

**Cross-conversation auto-injection is intentionally disabled (V4.40).** Injecting summaries from past conversations caused models to continue old tasks unprompted. The `chat_memory_enabled` and `chat_memory_lookback` session flags are retained for backward compatibility but are now no-ops for system prompt injection.

### Scope 2 — User Memory
Persistent facts about the user (name, occupation, skills, preferences, style, projects). Facts are extracted automatically from conversations by `MemoryExtractor` and stored in the `user_memory_facts` table. Rendered into `~/eva_workspace/{user_id}/memory/facts.md` — injected as a compact markdown block. The file is kept in sync via a debounced 3-second rebuild on every write.

Controlled by `user_memory_enabled`.

**Memory recall tool interaction:** When the `memory_recall` tool is in the active tools list, fact injection switches to *hint mode* — the system prompt receives a one-line pointer instead of the full fact block. This forces the model to call `memory_recall` to retrieve facts, making the tool useful rather than redundant. This behaviour is automatic and requires no session configuration.

Slot vocabulary:

- **Singular** (one value per user):

  | Slot | Notes |
  |------|-------|
  | `user.name` | Casual/preferred name — LLM-editable. Set via `preferred_name` in `PATCH /auth/profile`. |
  | `user.full_name` | Legal full name — **profile-locked** (source=`profile`). Set via `full_name` in `PATCH /auth/profile`. LLM extractor cannot overwrite. |
  | `user.about` | Short bio — **profile-locked** (source=`profile`). Set via `description` in `PATCH /auth/profile`. LLM extractor cannot overwrite. |
  | `user.occupation` | Job/role — LLM-extracted |
  | `user.location` | City/region — LLM-extracted |
  | `user.timezone` | IANA tz string — LLM-extracted |
  | `user.experience_level` | Skill level — LLM-extracted |
  | `user.primary_language` | BCP-47 language code — LLM-extracted |

- **Plural** (multiple values allowed): `user.skill`, `user.tool`, `user.preference`, `user.style`, `user.project`, `user.misc`

**Profile-locked fields** (`user.full_name`, `user.about`) have `source="profile"` — the memory extractor's singular-slot enforcement skips them. This means a user's explicitly-set full name and bio are never silently overwritten by conversational extraction. `user.name` (preferred name) uses `source="manual"` and remains LLM-editable so that if a user says "call me Ali" mid-conversation, the model can update it.

### Scope 3 — Agent Memory
Cross-conversation persistent entries keyed by a `scope_key` string. Used for storing agent decisions, errors, and artifacts that should persist across executions. Controlled by `agent_scope_key` and `agent_memory_enabled`.

**Sensitive entries** (marked `sensitive=True`) are never injected into the system prompt — they are only accessible via `memory_recall` with an explicit user grant.

**Important:** Scope 3 extraction only runs in `plan_execute` mode. In `conversational` mode extraction is skipped to prevent trivial chat noise from polluting agent memory.

**V6.21:** the profile-lock guarantee described above (`user.full_name`/ `user.about` "LLM extractor
cannot overwrite") is now actually true on both write paths. `core/memory/extractor.py`'s `"new"`
branch already checked `source == "profile"` before overwriting a matched slot; the `"supersedes"`
branch — which updates a fact by ID directly, based on an ID the LLM is shown as part of normal
operation — had no equivalent check at all, so a user re-mentioning their profile-set name in
conversation gave the model both the visible ID and a natural reason to classify it as
`"supersedes"`, which the code would have silently honored. Same guard, both branches now. The
agent-memory extractor can also now self-flag an entry as `sensitive` (the schema had no such field
before — only a human via `POST /memory/agent` could ever set it).

**V6.43:** Scope 3 extraction now dedupes against existing entries the same way Scope 2 (user facts)
has since B-041 — every extraction call is shown the scope's existing entries and classifies each
candidate `new`/ `redundant`/`supersedes`, with a hard rule that `state` is singular per scope (a
new state entry always supersedes the old one, never duplicates it). A `supersedes` update also
refreshes `source_conversation_id`/ `source_execution_id` to the confirming session. Newly-inserted
`error`-type entries get a 90-day default expiry (`AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS`) — every other
type, and all user facts, stay permanent; a new daily cleanup pass purges only rows with an
explicit, already-past expiry. A one-time reconciliation backfill for redundant history predating
this fix ships as `scripts/reconcile_memory.py` (a standalone CLI, not an API endpoint — an earlier
4-endpoint draft was found to duplicate existing DELETE endpoints for a one-time-use feature and was
removed). Also fixes two pre-existing bugs found during live verification, unrelated to the above:
`max_tokens` was never set on the extractor's own LLM call, letting a reasoning-mandatory model
silently exhaust its budget on hidden chain-of-thought (same class of bug already fixed for
`VerifierEngine`/`PlannerEngine`); and `_parse_json_list()` used a non-greedy regex that truncated
at the nested `"tags": [...]` array's own closing bracket instead of the real outer array's,
silently discarding every real extraction result — the actual reason live verification kept showing
zero stored entries, live-confirmed and fixed with `find("[")`/ `rfind("]")`, matching
`core/execution/verifier.py`'s already-correct `_extract_json()` pattern.

---

## Workspace Layout

All user data lives under a single per-user root:

```
~/eva_workspace/
└── {user_id}/
    ├── workspace/              ← free-form file sandbox
    │                             default CWD for shell execution and file tools
    │
    ├── projects/
    │   └── {project_id}/
    │       ├── .eva/           ← project workspace files
    │       │   ├── project.eva
    │       │   ├── policies.eva
    │       │   ├── tasks.eva
    │       │   ├── knowledge.eva
    │       │   ├── journal.eva
    │       │   └── errors.eva
    │       ├── files/          ← uploaded documents (RAG-indexed per project)
    │       └── conversations/  ← scratch dirs for project-scoped conversations
    │           └── {conv_id}/
    │               └── subagents/
    │
    ├── conversations/          ← scratch dirs for non-project conversations
    │   └── {conv_id}/          ← per-conversation working directory
    │       └── subagents/      ← per-subagent working dirs within the conversation
    │
    ├── rag/                    ← user-level RAG document blobs
    │
    ├── skills/                 ← user's personal skill library
    │   └── {skill_name}/       ← each skill is a directory
    │       ├── skill.yaml      ← manifest (name, description, version, usage)
    │       └── ...             ← supporting files (scripts, templates, data)
    │
    ├── hooks/                  ← user-uploaded hook scripts (chmod +x on upload)
    │
    └── memory/
        └── facts.md            ← auto-generated user fact summary (Scope 2)
```

The root path defaults to `~/eva_workspace` and can be overridden with `EVA_WORKSPACE_ROOT`.

**Conversation scratch directories** are cleaned up automatically by the workspace cleanup task. Any conversation directory not accessed in `WORKSPACE_TTL_DAYS` days (default 90) is deleted on the next 24-hour cleanup pass. The cleanup task **never** touches `workspace/`, `projects/`, `rag/`, `memory/`, `skills/`, or `hooks/`.

When a conversation is **promoted** to a project (`POST /conversations/{id}/promote`), its scratch directory is relocated from `conversations/{conv_id}/` to `projects/{project_id}/conversations/{conv_id}/`.

---

## Tool Development Guide

### Creating a Tool

```python
# tools/custom/my_tool.py
from pydantic import BaseModel, Field
from core.tools.base import ToolBase, ToolResult, ToolAuthority

class MyToolArgs(BaseModel):
    path: str = Field(..., description="File path to process")
    mode: str = Field(default="summary", description="'summary' or 'full'")

class MyTool(ToolBase):
    """Process a file and return insights."""

    name        = "my_tool"
    description = "Process a file and return structured insights."
    args_schema = MyToolArgs
    authority   = ToolAuthority.READ_ONLY   # READ_ONLY | MUTATING | DESTRUCTIVE

    async def run(self, **kwargs) -> ToolResult:
        path = kwargs["path"]
        mode = kwargs.get("mode", "summary")
        try:
            result = f"Processed {path} in {mode} mode"
            return ToolResult(ok=True, result=result)
        except Exception as e:
            return ToolResult(ok=False, error=str(e))
```

### Registering the Tool

Add it to `tools/custom/__init__.py`:
```python
from .my_tool import MyTool
__all__ = ["MyTool"]
```

### Tool Authority

The `authority` attribute controls HitL gate behaviour:

| Authority | Default behaviour | With `tool_approval_mode=always_ask` |
|-----------|-------------------|--------------------------------------|
| `READ_ONLY` | Executes immediately | Executes immediately |
| `MUTATING` | Executes immediately | Pauses for approval |
| `DESTRUCTIVE` | Executes immediately | Pauses for approval |

---

## Configuration Reference

All configuration lives in `config/settings.py`. Every setting below can be overridden either by a real OS/system environment variable, or by a line in `eva.env` (see [Installation](#installation)) — a real env var wins if both are set for the same key. `eva.env` changes need a full app restart to take effect; nothing re-reads the file live.

### Environment Variables

#### Provider API Keys

API keys are stored in **Windows Credential Manager** (via `keyring`) under the service name `"eva"` — not in `eva.env`. Use `POST /secrets` to set them, or write them directly via the Windows Credential Manager UI. The env variable names below are the canonical key names used internally; they map 1:1 to the keyring credential names.

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key |
| `GROQ_API_KEY` | Groq API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `TOGETHERAI_API_KEY` | Together AI API key |
| `PERPLEXITY_API_KEY` | Perplexity API key |
| `GEMINI_API_KEY` | Google Gemini API key |
| `VERTEX_AI_API_KEY` | Google Vertex AI API key |
| `BEDROCK_API_KEY` | AWS Bedrock API key |
| `AZURE_API_KEY` | Azure OpenAI API key |
| `HUGGINGFACE_API_KEY` | Hugging Face API key |
| `LITELLM_API_KEY` | LiteLLM proxy key |
| `OLLAMA_HOST` | Ollama local server base URL (default: `http://localhost:11434`) — not a secret, can live in `eva.env` |
| `OLLAMA_CLOUD_API_KEY` | Ollama Cloud hosted service API key |
| `OLLAMA_CLOUD_BASE_URL` | Ollama Cloud API base URL (default: `https://ollama.com/v1`) |

#### Core Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./eva.db` | SQLAlchemy async database URL. Must use an async driver: `aiosqlite` for SQLite, `asyncpg` for PostgreSQL |
| `SECRET_KEY` | *(dev placeholder)* | JWT signing secret — **must be overridden in production** |
| `TOKEN_EXPIRE_MINUTES` | `1440` (24 h) | Access token lifetime in minutes |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime in days |
| `EVA_VERSION` | `5.0.0-local` | Version string injected into startup logs |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated allowed CORS origins. In Electron builds, set to the frontend HTTP origin (e.g. `http://127.0.0.1:5174`) — not `file://` since EVA uses `loadURL()` |

#### Storage & Workspace

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROMA_PATH` | `~/eva_workspace/chroma_db` | ChromaDB persistent storage directory |
| `CHROMA_COLLECTION` | `eva_rag` | ChromaDB collection name |
| `EVA_WORKSPACE_ROOT` | `~/eva_workspace` | Root path for all user workspace files |
| `EVA_WORKSPACE_TTL_DAYS` | `90` | Days before a conversation scratch dir is eligible for cleanup |
| `EVA_CLEANUP_INTERVAL_SECONDS` | `86400` | How often the workspace cleanup task runs (seconds) |

#### Logging (V6.1)

| Variable | Default | Description |
|----------|---------|-------------|
| `EVA_LOG_DIR` | `<project_root>/logs/` | Directory for all log files. Set to OS user-data path in Electron deployments. |
| `EVA_LOG_SERVER_MAX_BYTES` | `52428800` (50 MB) | Per-file size cap before a mid-day rotation is triggered on `server.log` |
| `EVA_LOG_OBS_MAX_BYTES` | `104857600` (100 MB) | Per-file size cap for `observability.log` |
| `EVA_LOG_ERROR_MAX_BYTES` | `10485760` (10 MB) | Per-file size cap for `errors.log` |
| `EVA_LOG_SERVER_BACKUP_COUNT` | `30` | Number of rotated `server.log` files to retain |
| `EVA_LOG_OBS_BACKUP_COUNT` | `60` | Number of rotated `observability.log` files to retain |
| `EVA_LOG_ERROR_BACKUP_COUNT` | `90` | Number of rotated `errors.log` files to retain |
| `EVA_LOG_OBS_PROPAGATE` | `false` | Set to `true` to duplicate OBS_EVENTs in `server.log` (local debugging only) |

#### Agent Engine & Streaming

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_REACT_STEPS` | `8` | Maximum ReAct loop iterations per execution |
| `EVA_FIRST_CHUNK_TIMEOUT_S` | `120` | Seconds to wait for the first token. Set high for extended-thinking models (Claude, DeepSeek-R1, o3) that can be silent for 60–90 s. |
| `EVA_INTER_CHUNK_TIMEOUT_S` | `30` | Seconds of silence between consecutive tokens before treating as TCP stall and triggering fallback |
| `INTENT_CLASSIFIER_MAX_TOKENS` | `5` | Max tokens in LLM intent classifier response (Layer 2 routing) |
| `INTENT_CLASSIFIER_TEMPERATURE` | `0.0` | Temperature for LLM intent classifier (0 = deterministic) |

#### Worker

| Variable | Default | Description |
|----------|---------|-------------|
| `EXECUTION_HEARTBEAT_SECONDS` | `30` | How often the runner writes a heartbeat (used by watchdog) |
| `EXECUTION_TTL_SECONDS` | `86400` | How long completed execution records are retained in the broker |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `15` | How often the worker refreshes its liveness beacon |
| `WORKER_HEARTBEAT_KEY_TTL_SECONDS` | `30` | TTL for the `worker:heartbeat` key in the broker |
| `WORKER_LIVENESS_TTL_SECONDS` | `90` | TTL for the per-worker liveness beacon |

#### Watchdog

| Variable | Default | Description |
|----------|---------|-------------|
| `WATCHDOG_INTERVAL_SECONDS` | `60` | How often the watchdog scans for stuck executions |
| `WATCHDOG_RUNNING_TIMEOUT_SECONDS` | `900` | Max seconds an execution can be RUNNING/PLANNING before abort |
| `WATCHDOG_PENDING_TIMEOUT_SECONDS` | `1800` | Max seconds an execution can be PENDING before abort |
| `WATCHDOG_APPROVAL_TIMEOUT_SECONDS` | `14400` | Watchdog safety net for `AWAITING_APPROVAL` (a tool call waiting on allow/deny) (4 h) |
| `WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS` | `14400` | Watchdog safety net for `AWAITING_PLAN_APPROVAL` (a plan waiting on approve/edit/reject) (4 h) |
| `WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS` | `14400` | Watchdog safety net for `AWAITING_STEP_EXTENSION` (V6.12/V6.54 — a step-limit prompt waiting on continue/stop) (4 h) |

#### Human-in-the-Loop Approval Gate

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOL_APPROVAL_TIMEOUT_SECONDS` | `3600` | Max seconds the gate waits before auto-cancelling |
| `APPROVAL_HEARTBEAT_INTERVAL_SECONDS` | `60` | How often the gate refreshes its broker key while blocking |
| `PER_CONV_APPROVAL_TTL_SECONDS` | `604800` (7 d) | TTL for per-conversation auto-approval state |
| `APPROVAL_ARGS_PREVIEW_MAX_LEN` | `120` | Max characters of argument values shown in the approval preview |

#### PEV Runner

| Variable | Default | Description |
|----------|---------|-------------|
| `PLAN_APPROVAL_TIMEOUT_SECONDS` | `3600` | Max seconds to wait for plan approval |
| `PLAN_APPROVAL_HEARTBEAT_SECONDS` | `60` | How often the gate refreshes its key while waiting for plan approval |
| `MAX_REPLAN_ATTEMPTS` | `3` | Maximum adaptive re-planning attempts per execution |
| `PLAN_REVISION_MAX_ROUNDS` | `2` | Max times a rejected-with-feedback plan is revised and re-shown before a further reject cancels (UX-01 Phase 2) |
| `PEV_STEP_RECORD_TEXT_CHARS` | `1200` | Max characters of an earlier step's own text carried into later steps (V6.50, A-14) |
| `PEV_STEP_RECORD_MAX_TOOL_LINES` | `25` | Max tool-call lines kept per step record; older ones are counted, not listed |
| `PEV_STEP_RECORD_TARGET_CHARS` | `90` | Max characters of the "what it acted on" part of a tool-call line |
| `PEV_STEP_RECORD_ERROR_CHARS` | `120` | Max characters of an error message in a tool-call line |
| `PEV_PRIOR_STEPS_MAX_CHARS` | `6000` | Max size of the "work already done" block added to a step's prompt; on a long plan the oldest steps are cut to one line first |
| `PEV_PLAN_VERIFIER_TRAIL_MAX_CHARS` | `20000` | Max size of the same "work already done" block for `PlanVerifierEngine`'s one-time end-of-plan check (V6.53, A-20) — larger than `PEV_PRIOR_STEPS_MAX_CHARS` since there's no next step's own prompt headroom to protect, but still capped, not unlimited |
| `PEV_LOOKAHEAD_MAX_SKIP` | `2` | Deployer-level hard ceiling (V6.55, A-21) on how many upcoming steps a single verifier call can mark skipped in one jump, regardless of what `also_satisfies_count` claims — global tuning knob, not exposed to users (contrast `pev_lookahead_skip_enabled`, a per-user session preference — see "Session & Preferences") |
| `PEV_LOOKAHEAD_STEPS_CHARS` | `2000` | Max size of the "steps planned after this one" block shown to the per-step verifier for A-21's look-ahead judgment |

#### Tool Execution

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOL_EXECUTION_TIMEOUT_SECONDS` | `300` | Max wall-clock seconds for a single tool invocation's first attempt. Each retry gets a progressively larger window (2x per attempt, capped at 4x this value). |
| `TOOL_MAX_RETRY_ATTEMPTS` | `3` | Retry attempts for transient tool failures |
| `DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS` | `1800` | Base timeout for `delegate_to_agent` specifically — a nested `AgentEngine` already has its own step ceiling and per-call idle timeouts, so this is a generous backstop, not the primary control |
| `SEARCH_TOOL_MAX_RESULTS` | `200` | Max matches returned by `search_files`/`find_files` before truncating |

#### Shell Tool

| Variable | Default | Description |
|----------|---------|-------------|
| `SHELL_DEFAULT_TIMEOUT_SECONDS` | `30` | Default timeout per shell command |
| `SHELL_MAX_OUTPUT_BYTES` | `524288` (512 KB) | Max stdout/stderr captured per command |
| `SHELL_SESSION_TTL_SECONDS` | `86400` | Shell session (CWD + env vars) TTL in broker |
| `SHELL_SESSION_MAX_HISTORY` | `50` | Max command history entries per session |

#### HTTP Tool

| Variable | Default | Description |
|----------|---------|-------------|
| `HTTP_TOOL_MAX_RESPONSE_BYTES` | `10485760` (10 MB) | Max response body size accepted |
| `HTTP_TOOL_MAX_PAYLOAD_BYTES` | `1048576` (1 MB) | Max request body size for http_post |
| `HTTP_TOOL_MAX_TIMEOUT_SECONDS` | `120` | Hard ceiling on user-supplied request timeouts |

#### Hook Scripts

| Variable | Default | Description |
|----------|---------|-------------|
| `HOOK_SCRIPT_MAX_BYTES` | `1048576` (1 MB) | Max file size for an uploaded hook script |
| `HOOK_COMMAND_TIMEOUT_SECONDS` | `30` | Execution timeout for shell hook scripts |
| `HOOK_HTTP_TIMEOUT_SECONDS` | `10` | Timeout for HTTP hook calls |

#### Memory

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORY_MIN_MESSAGES_FOR_EXTRACTION` | `3` | Minimum messages in a conversation before extraction runs |
| `MEMORY_MAX_FACTS_PER_EXTRACTION` | `5` | Max user facts the LLM may return per extraction call |
| `MEMORY_MAX_ENTRIES_PER_EXTRACTION` | `5` | Max agent memory entries per extraction call |
| `MEMORY_SENSITIVE_GRANT_TTL_SECONDS` | `3600` | TTL for sensitive memory access grants |

#### Multi-Agent Tools

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_PARALLEL_TASKS` | `5` | Max concurrent subagents in `parallel_delegate` |
| `MAX_COORDINATE_PHASES` | `5` | Max phases the `coordinate` tool may plan |
| `MAX_COORDINATE_TASKS_PER_PHASE` | `5` | Max subagent tasks per phase in `coordinate` |
| `MAX_COORDINATE_DYNAMIC_AGENTS` | `3` | Max distinct *invented* (non-registered) agent personas `coordinate` may synthesize in one call (A-05) |

#### SSE & Event Streaming

| Variable | Default | Description |
|----------|---------|-------------|
| `SSE_HEARTBEAT_SECONDS` | `30` | How often SSE streams send a keepalive comment |
| `SSE_STALE_POLL_SECONDS` | `30` | Seconds before SSE gives up on a missing execution |

#### Blackboard

| Variable | Default | Description |
|----------|---------|-------------|
| `BLACKBOARD_TTL_SECONDS` | `7200` | Default TTL for execution blackboard keys |
| `BLACKBOARD_ENCRYPT` | `false` | Enable AES-GCM encryption of blackboard values at rest |

#### Daemon Scheduler

| Variable | Default | Description |
|----------|---------|-------------|
| `DAEMON_SCHEDULER_POLL_SECONDS` | `60` | How often the scheduler checks for due daemons |

#### Webhooks

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBHOOK_RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding window for webhook rate limiting |
| `WEBHOOK_RATE_LIMIT_MAX_CALLS` | `10` | Max webhook calls per token per window |

#### Providers

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama local server base URL |
| `OLLAMA_DEFAULT_MODEL` | `llama3.2:latest` | Default Ollama model |
| `ENABLE_MCP` | `true` | Enable MCP server integration |
| `PROVIDER_MODEL_CACHE_TTL_SECONDS` | `300` | TTL for provider model listing cache |
| `LITELLM_COST_MAP_REFRESH_HOURS` | `24` | Hours before the LiteLLM cost map is considered stale |
| `PROVIDER_CAPABILITY_REFRESH_TTL_SECONDS` | `3600` | TTL for non-forced provider capability refreshes |

#### Vision (M-07, V6.50)

| Variable | Default | Description |
|----------|---------|-------------|
| `EVA_VISION_ENABLED` | `true` | Global kill-switch for sending real image content to a model — independent of the per-model capability check below |
| `IMAGE_INLINE_MAX_BYTES` | `5000000` (5 MB) | Max size of a single image inlined as real content; over this, the existing text-placeholder behaviour is used instead |

#### RAG

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_JOB_TTL_SECONDS` | `3600` | TTL for RAG ingestion job status keys |

#### MCP

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_CONNECT_TIMEOUT_SECONDS` | `60` | Timeout for opening MCP transport connection |
| `MCP_INIT_TIMEOUT_SECONDS` | `60` | Timeout for the MCP initialize handshake |
| `MCP_CALL_TIMEOUT_SECONDS` | `60` | Timeout for MCP tool list / tool call operations |

---

## Deployment Notes

### Single-Process Constraints

EVA Local runs in one process. This means:

| Constraint | Impact |
|-----------|--------|
| Single process | Cannot scale horizontally with multiple API instances |
| In-memory broker | Broker state (job queue, shadow buffers, approval mailboxes) is lost on server restart |
| Active execution loss on restart | Any execution running at restart will be marked `ABORTED` on next startup |
| Memory-bound concurrency | Very high request volume will contend for the same asyncio event loop |

These constraints are acceptable for **local / personal / single-user deployments**. For production multi-user or multi-instance scenarios, the broker layer would need to be replaced with an external service.

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Running in Production (single instance)

```bash
uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --loop uvicorn \
  --access-log
```

> **Note:** Do not use `--workers N` with `N > 1`. Multiple worker processes do not share the in-process `LocalBroker` — each process would have its own isolated job queue and shadow buffers, which breaks SSE streaming and execution coordination. Use a single worker (`--workers 1`, the default) or leave `--workers` unset.

### CORS and SSE

The SSE connection (`GET /eva_response`) uses `fetch()` rather than native `EventSource`, which sends a `Cache-Control: no-cache` header as part of the SSE protocol. This is a non-simple header and triggers a CORS preflight.

`Cache-Control` must be present in `allow_headers` in `main.py` (it is, as of V6.2). If it is removed, the preflight will fail and the SSE stream will never connect. Symptoms: chat messages are processed and saved to the DB correctly (the `POST /chat` call does not send `Cache-Control`), but the response never appears in the UI until the user navigates away and back — because the response is only fetched via REST history, never via the live SSE stream. The status bar will also show `†undefined · †undefined` since token counts only arrive via the SSE `usage` event.

---

## Monitoring & Observability

### Health Endpoints

```bash
GET /health        # { "status": "healthy" }
GET /health/ready  # { "status": "ready|degraded", "checks": { "db": "ok", "broker": "ok", "vector_store": "ok", "worker": "ok" } }
```

`/health/ready` returns `503` if any subsystem is degraded. The `worker` check verifies the worker has sent a heartbeat to the broker within the last 30 seconds.

### Execution Metrics

Per-execution performance is collected by `MetricsCollector` (event-bus subscriber) and flushed to the `execution_metrics` table on completion:
- Token counts (prompt, completion, reasoning)
- Computed cost in USD
- Duration (ms)
- Total ReAct steps
- Fallback count
- Per-tool call/success/failure/latency stats
- Termination reason

Query via `GET /executions/{id}/metrics`.

### Event Bus

EVA uses a synchronous in-process event bus (`core/event_bus.py`) for decoupled internal observability. Subscribers:
- `observability/logger.py` — structured logging
- `core/metrics/collector.py` — per-execution metric aggregation

Events emitted: `ExecutionStarted`, `ExecutionCompleted`, `ExecutionFailed`, `ExecutionCancelled`, `ToolCalled`, `ToolResult`, `ToolPermissionDenied`, `StepCompleted`, `FallbackTriggered`, `ProviderFallbackTriggered`, `ToolApprovalRequested`, `ToolApprovalResolved`

### Logging (V6.1)

Three production log streams, all using `HybridRotatingFileHandler` (daily midnight rotation + mid-day size-cap overflow):

| File | Logger | Level | Max Size | Retention |
|------|--------|-------|----------|-----------|
| `logs/server.log` | root | INFO+ | 50 MB | 30 files |
| `logs/errors.log` | root | WARNING+ | 10 MB | 90 files |
| `logs/observability.log` | `eva.observability` | INFO+ | 100 MB | 60 files |

**Log format:**
```
2026-05-29 12:34:56,789 | INFO | a3f7c1b9 | uvicorn.error | Started server
                                  ^^^^^^^^
                              8-char server session ID
```

**Option B — Rotate on restart:** Each log file is rotated at startup if it already contains data. Every server launch begins with a clean, empty file. Archived files are named `server.log.2026-05-29`, with `.2`, `.3` suffixes for same-day size overflows.

**Option C — Session ID per line:** A UUID4 (`SERVER_SESSION_ID_FULL`) is generated once at module import time. The 8-character short form (`SERVER_SESSION_ID`) is stamped on every log line by `_SessionFilter`. This allows filtering by session: `grep a3f7c1b9 logs/server.log`. Exported from `config.logging` for use in health endpoints and Electron crash reporting.

**OBS isolation:** `eva.observability` logger propagation is `false` by default — OBS_EVENTs (execution records, tool calls, metrics) are written only to `observability.log`, not `server.log`. Set `EVA_LOG_OBS_PROPAGATE=true` to enable duplication (local debugging only).

Configure log directory with `EVA_LOG_DIR`. All size/retention thresholds are configurable — see [Configuration Reference](#configuration-reference).

---

## Troubleshooting

### Worker not processing jobs

The `ExecutionWorker` starts automatically inside the server process. Check `GET /health/ready` — if `"worker": "stale"` or `"worker": "error"`, the worker task may have crashed. Check `logs/server.log` for `[WORKER]` entries. Restart the server.

### ChromaDB unavailable

ChromaDB is embedded — there is no server to check. If it fails:
- Verify the `CHROMA_PATH` directory is writable (default: `~/eva_workspace/chroma_db`)
- Check `logs/server.log` for `[chroma]` entries
- Delete and recreate the directory if the database is corrupted

All non-RAG EVA features continue to work if ChromaDB fails to initialise.

### 409 Conflict on POST /chat

An execution is already active for that conversation. Wait for it to complete, cancel it with `POST /cancel`, or use a different `conversation_id`.

### Chat messages missing after edit/retry

Use `DELETE /conversations/{id}/messages?from_message_id={id}` to truncate the DB to match the UI before resubmitting.

### SSE stream stalls or shows no events

- Verify the `execution_id` returned by `/chat` is the one the SSE endpoint is connecting to (via `conversation_id` lookup)
- Check `GET /executions/{id}/status` to see the current execution state
- If status shows `COMPLETED` but no events arrived, the execution may have completed before the SSE connection opened — the `LocalShadowBuffer` will replay all chunks on reconnect

### SSE stream returns 404

No execution found for that `conversation_id`. The conversation lookup key expires after the execution completes. Re-open SSE immediately after posting to `/chat`.

### File ingestion slow or failing

- Check that the file format is PDF, DOCX, or TXT
- Workspace storage must have sufficient disk space at `EVA_WORKSPACE_ROOT`

### Authentication token expired

Tokens expire after 24 hours. Use `POST /auth/refresh` with the refresh token to get a new access token without re-logging in.

### "No running execution to cancel" (404)

The execution already reached a terminal state. No action needed.

### Execution stuck in PENDING

The worker may not be running. Check `GET /health/ready`. If `"worker": "stale"`, restart the server — it will automatically abort orphaned PENDING executions on startup.

---

## Error Reference

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| `200 OK` | Request successful |
| `201 Created` | Resource created |
| `202 Accepted` | Request accepted for async processing |
| `400 Bad Request` | Invalid request format or parameters |
| `401 Unauthorized` | Missing or invalid authentication token |
| `403 Forbidden` | Authenticated but not authorised for this resource |
| `404 Not Found` | Resource does not exist or belongs to another user |
| `409 Conflict` | Active execution already running for this conversation |
| `422 Unprocessable Entity` | Request body failed validation |
| `503 Service Unavailable` | A required dependency (DB, ChromaDB) is not healthy |

### EVA Error Envelope

All error responses use a consistent envelope:
```json
{ "code": "EVA_NOT_FOUND", "detail": "..." }
```

### EVA Error Types (in SSE `error` events)

Provider-level types below are classified by `core/error_handling.py` (see [Provider Fallback Chain](#provider-fallback-chain-v65) for the full tier model) — "Triggers Fallback" reflects `ErrorPolicy.fallback_eligible`, not just whether a retry happens first.

| Error Type | Cause | Triggers Fallback |
|-----------|-------|-------------------|
| `RATE_LIMIT` | Provider rate limit exceeded | Yes — same provider retried with backoff first (Tier A) |
| `PROVIDER_DOWN` | Provider service unavailable | Yes — same provider retried with backoff first (Tier A) |
| `TIMEOUT` | Provider TCP stall or silent hang | Yes — same provider retried with backoff first (Tier A) |
| `INVALID_MODEL` | Model not found for provider | Yes — no retry, immediate failover (Tier B) |
| `CONTENT_POLICY` | This provider's moderation refused the request | Yes — no retry, immediate failover (Tier B) |
| `AUTH` | Provider authentication failed | Yes, but scoped — every remaining candidate on the *same* provider is skipped too (Tier B′) |
| `INVALID_REQUEST` | Malformed provider request | No — hard stop (Tier C) |
| `EXECUTION_ERROR` | Tool execution error | No |
| `MEMORY_EXHAUSTED` | Context window exceeded | No |
| `INTERNAL` | EVA internal error | No — hard stop (Tier C) |
| `UNKNOWN` | Unclassified error | No — hard stop (Tier C); deliberately conservative rather than guessing |

---

## File & Directory Reference

### Root

| File/Dir | Purpose |
|----------|---------|
| `main.py` | FastAPI app entrypoint — registers routers, runs lifespan (migrations, broker start, worker task, daemon scheduler task) |
| `entrypoint.py` | PyInstaller-only entry point for the Electron packaged build. Sets up `sys._MEIPASS`, imports `app` directly to avoid frozen-binary module lookup issues. Has no effect in normal development. |
| `requirements.txt` | Python package dependencies |
| `eva.db` | SQLite database (development; use PostgreSQL in production) |
| `eva.env` | Local environment configuration (not committed) |
| `eva.env.example` | Tracked template — copy to `eva.env` and uncomment what you need (V6.33) |
| `alembic.ini` | Alembic migration configuration |

### api/

| File | Purpose |
|------|---------|
| `chat.py` | `POST /chat` — persists user message, enforces serialisation guard, enqueues job to LocalBroker |
| `eva_response.py` | `GET /eva_response` — SSE stream from `LocalShadowBuffer`; replay-safe on reconnect; normalises `ChunkType` enums to plain strings |
| `session.py` | `GET /session`, `POST /set-session`, `GET/PATCH /session/permissions` |
| `auth.py` | Signup, login, logout, refresh, SSE token; `GET /auth/me`, `PATCH /auth/profile`, `POST /auth/change-password` (V6.0) |
| `cancel.py` | `POST /cancel` — abort by conversation ID |
| `executions.py` | Execution status, metrics, trace, plan, retry, `POST /executions/{id}/cancel`, `GET /executions/running` |
| `approval.py` | `POST /executions/{id}/approve`, `/reject`, `/approve-plan` |
| `conversation_management.py` | Conversation and message CRUD + truncation |
| `projects.py` | Project CRUD, context, artifacts, file upload, linked resources |
| `agents.py` | Subagent config CRUD |
| `daemons.py` | Daemon config CRUD + manual trigger |
| `webhooks.py` | Public webhook endpoint for daemon triggering |
| `memory.py` | All memory scope endpoints (chat, user facts, agent entries) |
| `rag.py` | Document ingest, semantic search, file list, delete |
| `tools.py` | Tool manifest endpoints |
| `providers.py` | Provider capability registry endpoints |
| `mcp.py` | MCP server config CRUD, sync, health, resources, prompts, tools |
| `hooks.py` | Hook script CRUD |
| `attachments.py` | Attachment library + conversation attachments |
| `secrets.py` | Secrets / API key management |
| `events.py` | `GET /events/stream` — SSE feed for user-facing events (execution/daemon completed/failed, conversation titled) |
| `skills.py` | Skills management — per-user skill CRUD + file management |
| `prompts.py` | `POST /prompts/enhance` — LLM prompt rewrite/enhancer |
| `metrics.py` | `GET /metrics/session`, `/metrics/usage/summary`, `/metrics/usage` — aggregated token/cost/duration metrics (B-073) |
| `wakeups.py` | P-19 Tier A — `GET /wakeups`, `POST /wakeups/{id}/resume`, `/cancel` — manual pause/resume/cancel for scheduled wakeups |
| `processes.py` | V6.49 (background-process-supervision Phase 3) — `POST /processes/{process_id}/stop`, the human-facing counterpart to the `background_process` tool's stop action. `PATCH /processes/{process_id}/keep-alive` (V6.58 Phase 2) — the Activity panel's "keep running after EVA closes" toggle |
| `internal.py` | V6.58 Phase 3 — `POST /internal/shutdown`, localhost-only (no JWT), called by Electron at app-quit: stops every running, non-`keep_alive_on_app_shutdown` background process across all users concurrently, then raises a self-directed `SIGINT` (`signal.raise_signal()`, not `os.kill()` — the latter hard-kills on Windows) to trigger the normal ASGI graceful shutdown |
| `deps.py` | Shared FastAPI dependencies (e.g. `get_db()`) — single source of truth, previously copy-pasted across routers |
| `_etag.py` | ETag helper for stable-data list endpoints (B-020) |
| `schemas/` | Pydantic request/response schemas |

### core/broker/  *(new in V0.1)*

| File | Purpose |
|------|---------|
| `__init__.py` | Exports `local_broker` singleton |
| `local_broker.py` | `LocalBroker` class — owns `LocalRedisAdapter` and the `LocalShadowBuffer` registry. `get_shadow_buffer(execution_id)` returns (or creates) the per-execution buffer. `start()` / `stop()` called by `lifespan`. |
| `shadow_buffer.py` | `LocalShadowBuffer` — per-execution list of chunk dicts with `asyncio.Event` wakeup. Methods: `append()`, `chunks_from(index)`, `wait_for_more(min_count, timeout)`, `is_closed()`, `clear()` |
| `exceptions.py` | Broker-specific exception types |

### core/execution/

| File | Purpose |
|------|---------|
| `runner.py` | Conversational execution — drives AgentEngine ReAct loop, appends chunks to `LocalShadowBuffer` |
| `pev_runner.py` | Plan-Execute-Verify execution — planner → step runner → verifier loop |
| `worker.py` | `ExecutionWorker` asyncio task — dequeues jobs from LocalBroker, dispatches to runner or pev_runner; `_process_execution()` orchestrates session/tool-building, system-prompt assembly, and AgentEngine/history construction as private methods, then calls into `worker_finalize.py` |
| `worker_context.py` | Pure prompt-building helpers used by `worker.py`'s system-prompt assembly: `_build_manifest`, `_detect_shell`, `_build_environment_context` (V6.50: also states which folder relative paths land in — the *kind*, never a local path) |
| `worker_finalize.py` | Post-execution finalize (`_finalize_execution`) — rollback, memory extraction, title generation, project artifact update, metrics flush (`_flush_metrics`, which now also logs `promoted_count`, `describe_tool_calls` and token counts, V6.50), stop hooks |
| `registry.py` | In-memory execution reference manager (for SSE + cancel) |
| `redis_registry.py` | `LocalRedisAdapter`-backed execution metadata store (name kept for compatibility) |
| `models.py` | `ConversationExecution`, `ExecutionStatus`, `CancelToken`; `final_answer_override` (V6.53, A-19) — set by `pev_runner.py` to the real closing answer, read by `run_pev_execution()` in place of the naive all-steps concatenation |
| `intent_router.py` | Three-layer intent routing (session override → regex → LLM classifier) |
| `planner.py` | PEV planner — generates structured step plans including `success_criteria` (V6.53, A-20; permissive fallback via `_normalise_success_criteria()`, shared with the strict user-edit path); `replan()` keeps the rest of the goal (V6.50: prompt + `_keep_remaining_steps` backstop) and uses a `max_tokens=2000` budget (V6.52, A-18 — empirically raised from 900, which still truncated a reasoning-mandatory model's JSON) |
| `verifier.py` | PEV per-step verifier — validates one step's own result; shown the step's own tool-call lines (V6.50, A-13) *and* the same earlier-steps record the step itself got (V6.51, A-16), so a step with no tool calls of its own can still pass when an earlier step's real evidence already covers it |
| `plan_verifier.py` | `PlanVerifierEngine` (V6.53, A-20) — one call per FINISHED plan, judging the whole thing against its `goal`/`success_criteria`, distinct from the per-step verifier above. Tri-state verdict (`success`/`pending`/`not_successful`), fail-closed to `pending` on any infra error. No tools — deliberately not a sub-agent, a single tightly-scoped judgment call over evidence `step_record.py` already gathered |
| `step_record.py` | V6.50 — what a plan step is told about earlier steps and what the verifier is told about a step's tools: `StepToolLedger` (one line per call), `StepRecord`, `build_prior_steps_block`, `active_tool_names` / `build_active_tools_line`. Pure functions, no I/O, size-capped by settings. The same `build_prior_steps_block()` output now also reaches the per-step verifier (V6.51) and, at a larger cap, `PlanVerifierEngine` (V6.53) |
| `state_machine.py` | `ExecutionStateMachine` — validates allowed state transitions |
| `transitions.py` | `TERMINAL_STATES` and the allowed-transition table `state_machine.py` validates against |
| `errors.py` | `ExecutionStateError`/`IllegalStateTransition` — raised by `state_machine.py` |
| `step_extension.py` | V6.12 — ReAct step-limit "ask user to continue" gate, invoked after the forced-best-effort-answer attempt (V6.9/V6.11) has already run |
| `title_generator.py` | Auto-title generator (B-058) — after the first exchange, generates a short 3-6 word conversation title and publishes `conversation.titled` |
| `wakeup_scheduler.py` | P-19 — `WakeupScheduler`, a lightweight async background loop (same shape as `core/daemons/scheduler.py`) that resumes executions paused via `schedule_wakeup` once their due time arrives |

### core/

| File/Dir | Purpose |
|----------|---------|
| `agent_engine.py` | Stateless ReAct loop engine — receives messages, yields chunks, manages provider fallback |
| `api.py` | Shared API utilities |
| `blackboard.py` | Shared key-value store for agent coordination (LocalRedisAdapter-backed) |
| `event_bus.py` | In-process synchronous pub/sub for observability events |
| `events.py` | Typed event dataclasses |
| `events_publisher.py` | Helper to publish typed events to the bus |
| `error_handling.py` | Centralized error classification |
| `factory.py` | Component factory for execution setup |
| `rate_limit.py` | Shared in-process sliding-window rate limiter — no external Redis needed, state lives in-process like the rest of EVA's broker architecture |
| `stream_buffer.py` | `StreamBuffer` — streaming chunk accumulator used by runners |
| `types.py` | Shared type aliases |
| `utils.py` | General utilities |
| `agents/` | SubagentRegistry — loads and caches subagent configs. `dynamic.py` builds an ephemeral (non-DB) config for an on-the-fly agent, shared by `delegate_to_agent`, `parallel_delegate`, and `coordinate` |
| `auth/` | JWT security, session validation, request dependencies |
| `daemons/` | `DaemonRegistry`, `DaemonScheduler`, `run_daemon()` |
| `db/` | SQLAlchemy models, repository, async session factory |
| `defaults/` | `seeder.py` — seeds default agents, skills and MCP configs (`default_mcps/*.yaml`) for a user at signup / on demand |
| `files/` | File parsing (PDF, DOCX, TXT) |
| `hooks/` | Hook script runner |
| `logging/` | `message_logger.py` — persists the final aggregated message plus usage/tool info once an execution completes (intentionally does not persist streaming chunks) |
| `mcp/` | MCP client connection pool and runtime |
| `memory/` | Memory engine, extractors, summarizer, providers, long-term store |
| `metrics/` | MetricsCollector (event-bus subscriber) |
| `project/` | Project manager, .eva artifact updater |
| `providers/` | Provider capability registry, resolver, API registry |
| `rag/` | ChromaDB client (embedded), ingestion, embedding, retrieval. `embed.py` uses ONNX Runtime — no torch dependency |
| `react/` | ReAct context helpers |
| `shell/` | `ShellSession` (broker-backed state), `ShellExecutor` (V6.50: a timed-out or cancelled foreground command now takes its whole process tree with it; a relative `cwd` resolves against the project/conversation folder like the file tools), `safety.py` (V6.50, S-14: the path-traversal check now recognises Windows CLI switches like `/c`/`/s` as options rather than paths — Windows-only, no false positives — and separately catches real Windows drive-letter and UNC paths outside the workspace, excluding `C:\Windows\`/`C:\Program Files\`/`C:\ProgramData\` the same way the POSIX side excludes `/usr/bin`; also fails closed on any path-resolution error, not just `ValueError`) |
| `skills/` | Per-user skill loader and registry |
| `tasks/` | `cleanup.py` — workspace cleanup background task (runs every 24 h, deletes stale conversation scratch dirs) |
| `tools/` | Tool base class, dispatcher, loader, approval gate, registries |

### tools/default/

| File | Purpose |
|------|---------|
| `shell_tool.py` | Execute shell commands with per-conversation CWD persistence |
| `file_tools.py` | Read, write, list, delete files in user workspace. `list_files` lists subfolders (trailing `/`) by default since V6.50; `include_dirs=false` for files only |
| `git_tool.py` | Git operations |
| `web_search.py` | Web search via DDGS (DuckDuckGo) — consolidated from five separate text/images/videos/news/books tools into one with a `search_type` parameter |
| `search_tools.py` | (T-02) Recursive file/content search — `search_files`/`find_files`, the recursive counterparts to `file_tools.py`'s deliberately non-recursive `list_files`. `find_files` matches file *names* only, never paths or folders |
| `http_tool.py` | HTTP GET/POST requests |
| `rag_tool.py` | Semantic document search |
| `memory_tools.py` | Read/write long-term memory entries |
| `blackboard_tools.py` | Read/write shared blackboard (agent coordination) |
| `delegate_tool.py` | Delegate task to a named subagent |
| `parallel_delegate_tool.py` | Delegate multiple independent tasks concurrently |
| `coordinate_tool.py` | Coordinate multi-agent workflows across phases |
| `daemon_tools.py` | Create/trigger daemons from within an execution |
| `daemon_config_tools.py` | Daemon configuration builder tools — create/read/edit/delete persistent `DaemonConfig` rows directly from conversation |
| `subagent_config_tools.py` | Subagent configuration builder tools — create/read/edit/delete persistent `SubagentConfig` rows directly from conversation |
| `project_tools.py` | Phase B — `ProjectArtifactWriteTool`, lets an agent record findings directly into a project's `.eva` artifacts without going through the message-based `ProjectArtifactUpdater` pipeline |
| `process_tools.py` | `background_process` tool (V6.37) — start/list/tail/stop a detached, DB-tracked background process (e.g. a dev server), the sanctioned alternative to `shell_tool.py`'s always-blocking-or-killed execution model. `tail` takes an optional `wait_seconds` (max 60, V6.50) that blocks until new output or exit instead of polling; a process outlives the run that started it (only `stop`, the Activity panel's Stop button, or the daily cleanup ends it); starting the same command in the same folder again returns the live process (`already_running: true`) instead of a duplicate (V6.50). `start` takes an optional `keep_alive_on_app_shutdown` (default `false`, V6.58 Phase 2) — session-scoped by default (stopped when EVA quits), opt-in to survive |
| `wakeup_tools.py` | P-19 — `schedule_wakeup`, a persisted "resume me later" tool distinct from `trigger_daemon(wait=True)`'s live coroutine block |
| `messaging_tools.py` | Inter-agent messaging |
| `describe_tool.py` | Progressive Tool Disclosure — meta-tool that returns the full schema of any named tool on demand (reduces token cost; detailed schemas are only sent when the model explicitly requests them). Also resolves MCP-backed tools (falls back to the user's synced MCP tool registry when a name isn't a built-in/custom tool). A model that calls a tool it never described is no longer handed a "not yet active" stub to retry (V6.50): the dispatcher promotes the tool and runs the call, flagged `metadata.auto_activated`; authority policy, hooks and approval still apply |

### providers/

| File/Dir | Purpose |
|----------|---------|
| `handlers/litellm_handler.py` | LiteLLM unified streaming handler |
| `handlers/openai_handler.py` | OpenAI streaming handler |
| `handlers/anthropic_handler.py` | Native Anthropic (Claude) handler — uses the `anthropic` Python SDK directly rather than routing through LiteLLM |
| `handlers/groq_handler.py` | Groq streaming handler |
| `handlers/ollama_handler.py` | Ollama local model handler |
| `handlers/ollama_cloud_handler.py` | Ollama Cloud handler — targets Ollama's OpenAI-compatible API at `https://ollama.com/v1/` |
| `capability/` | Per-provider model lists and capability metadata caches |

### config/

| File | Purpose |
|------|---------|
| `settings.py` | All runtime constants with env-var overrides and defaults |
| `env_loader.py` | Loads `eva.env` — strict provider key isolation (Windows Credential Manager is authoritative, never `eva.env`/system env vars), plus (V6.6) rehydrates every other `eva.env` key into `os.environ` so `config/settings.py`'s bare `os.getenv()` constants are genuinely overridable, not just its pydantic `Settings` classes |
| `logging.py` | `configure_logging()` — three-stream hybrid rotating logging (V6.1): `HybridRotatingFileHandler` (midnight + size cap), `_SessionFilter` (stamps `SERVER_SESSION_ID` on every `LogRecord`), rotate-on-restart (Option B), per-stream size/retention config. Exports `SERVER_SESSION_ID` and `SERVER_SESSION_ID_FULL`. |

### observability/

| File | Purpose |
|------|---------|
| `logger.py` | Event-bus subscriber that writes structured log lines |
| `base.py` | Base observability types |
| `null.py` | No-op observer for testing |

### prompts/

| File | Purpose |
|------|---------|
| `coordinator.py` | Orchestrator system prompt |
| `planner.py` | PEV planner prompt — includes `success_criteria` schema field/rule/examples (V6.53, A-20) |
| `verifier.py` | PEV per-step verifier prompt — `{prior_steps_section}` and rule 9 (V6.51, A-16) |
| `plan_verifier.py` | `PlanVerifierEngine`'s whole-plan verdict prompt (V6.53, A-20) — tri-state verdict rules, evidence-grounding rule mirrored from `verifier.py`, "the plan's own closing summary is a claim, not evidence" framing |
| `memory.py` | Memory injection prompt fragments |
| `project.py` | Project context injection prompt fragments |
| `soul.py` | EVA Platform Identity — Soul Layer (V5.1); platform-level identity/operating-principles preamble prepended to every interactive conversation execution |

### alembic/

| File | Purpose |
|------|---------|
| `env.py` | Alembic async migration environment |
| `versions/0001_initial_schema.py` | V0 clean baseline — creates all 25 tables from scratch. Single source of truth for the full DB schema. |
| `versions/0002_reasoning_level.py` | V6.7 — `user_preferences.reasoning`: `Boolean` → `String` (level) |
| `versions/0003_max_step_extensions.py` | V6.8 — `user_preferences.max_step_extensions` |
| `versions/0004_scheduled_wakeups.py` | P-19 Phase 1 — new `scheduled_wakeups` table |
| `versions/0005_background_processes.py` | V6.37 — new `background_processes` table (registry for detached processes started via `process_tools.py`) |
| `versions/0006_daemon_kind.py` | V6.38 — `daemon_configs.kind` column (standard/system/supervisor) |
| `versions/0007_auth_retention_indexes.py` | B2-3.2/B2-3.3 — indexes on columns the daily auth-retention purge filters on |
| `versions/0008_drop_user_is_active_and_auth_audit_event_metadata.py` | B1-2.1/B1-2.2 — drops two never-enforced/never-populated columns |
| `versions/0009_drop_sessions_table.py` | DB-09 — drops the dead `sessions` table (`SessionModel`), repoints `conversations.session_id`'s FK to `auth_sessions.id` (what it actually stores) |
| `versions/0010_drop_usage_logs_table.py` | DB-08 — drops `usage_logs`, confirmed fully redundant with `execution_metrics` (same finalizer, same `usage` dict, zero readers anywhere) |
| `versions/0011_drop_tool_call_dead_columns.py` | DB-10 (phase 1) — drops `tool_calls.result`/`.call_id` and `messages.tool_call_id`, all confirmed write-only. `tool_calls.tool_name`/`.arguments` deliberately left in place — still the only surviving copy of per-call data for multi-simultaneous-tool-call steps |
| `versions/0012_add_pii_redaction_enabled.py` | S-03 — adds `user_preferences.pii_redaction_enabled` (boolean, default `false`) |
| `versions/0013_add_mcp_credential_refs.py` | S-11 — adds `mcp_configs.credential_refs` (nullable JSON, no default — `NULL` means "not yet migrated", `[]` means "migrated, no secrets found") |
| `versions/0014_add_execution_plan_success_criteria.py` | A-20 (V6.53) — adds `execution_plans.success_criteria` (nullable JSON) |
| `versions/0015_add_execution_plan_goal_verification.py` | A-20 (V6.53) — adds `execution_plans.goal_verdict`/`.goal_verification_message`/`.goal_criteria_results` (all nullable — `NULL` means the check never ran for that row, never "ran and found nothing") |
| `versions/0016_add_pev_lookahead_skip_enabled.py` | A-21 (V6.55) — adds `user_preferences.pev_lookahead_skip_enabled` (boolean, default `true` — opt-out, not opt-in) |

### default_agents/

13 built-in agent YAML definitions shipped with EVA and seeded into every new user account at signup. Each YAML specifies `name`, `description`, `system_prompt`, `model`, `provider`, `tools`, and `max_steps`.

| File | Agent |
|------|-------|
| `sa-01-deep-research.yaml` | Deep research |
| `sa-02-code-review-agent.yaml` | Code review |
| `sa-03-coding-agent.yaml` | Implementation |
| `sa-04-testing-agent.yaml` | Test generation |
| `sa-05-git-agent.yaml` | Git operations |
| `sa-06-file-system-analyst.yaml` | File system analysis |
| `sa-07-documentation-writer.yaml` | Documentation |
| `sa-08-security-reviewer.yaml` | Security audit |
| `sa-09-data-analyst.yaml` | Data analysis |
| `sa-10-error-diagnostician.yaml` | Error diagnosis |
| `sa-11-deployment-agent.yaml` | Deployment automation |
| `sa-12-self-improvement-agent.yaml` | Meta / self-improvement |
| `sa-13-skill-runner.yaml` | Skill execution — identifies and faithfully executes a Skill on behalf of a delegating caller (typically a daemon, or reachable via `delegate_to_agent`); deliberately generic, carries no project-specific knowledge itself |

### default_mcps/

Default MCP server definitions, seeded per user by `core/defaults/seeder.py` (see "Default MCPs" under the MCP API above). One YAML per server: `name`, `description`, `transport`, `command`/`args`/`env`/`cwd` (stdio) or `url`/`headers` (http/sse), `enabled` (default `false`), and `requires` (executables that must be on `PATH`). `{workspace}` and `{browser_channel}` placeholders are resolved per user. Bundled into the packaged build via `eva.spec`.

| File | MCP |
|------|-----|
| `playwright.yaml` | Browser automation (Playwright) — navigate, snapshot, console/network inspection, click/type, screenshots |

### tests/

`test_apis.py`, `test_behaviour.py`, `test_ollama.py`, `test_mcp_credential_live.py`,
`test_pii_redaction_live.py`, `test_background_process_live.py`, `test_playwright_mcp_live.py`, and
`test_browser_agent_loop_live.py` are standalone
scripts meant to be run directly against a live server (`python tests/test_apis.py`,
etc.), not pytest suites in the usual sense — they have no `test_*` functions for pytest
to collect and each does a module-level live-server preflight check. `conftest.py`
excludes all eight from automatic `pytest`/`pytest tests/` collection via `collect_ignore`
(a real gap found and fixed during the `command`-field addendum below — the background-
process live script was left off this list when first added, which crashed a full
`pytest tests/` run with an `INTERNALERROR` on its module-level `sys.exit()`); direct
invocation is unaffected. Everything else in this directory is a real pytest unit-test
file, mock-driven and safe to run offline (`pytest tests/`).

| File | Purpose |
|------|---------|
| `test_apis.py` | End-to-end integration test suite — 95 sections covering every API endpoint in sequence (auth, session, chat/streaming, tools, memory, RAG, agents, daemons, approvals, hooks, metrics, MCP, webhooks, user profile, app-close task cancellation, and more). Requires a running server at `http://127.0.0.1:8000`. Run from the project root: `python tests/test_apis.py`. API-contract only (CRUD/health/sync) — does not make real LLM calls. |
| `test_behaviour.py` | Behavioural/integration test suite — real LLM-in-the-loop checks against a running server (intent routing, SSE stream structure, tool invocation, cancellation, PEV loop, RAG, memory, HitL approval, progressive disclosure, and MCP tool discovery/execution end-to-end). Slower than `test_apis.py` by design. Run: `python tests/test_behaviour.py`. |
| `test_ollama.py` | Standalone Ollama-provider check (Section 24 only, not the full suite) — `GET /providers`/`/providers/ollama/models` shape, `/set-session` + `/session` round-trip for `provider=ollama`, and the unauth-401 guard. Requires a running server; a local Ollama host is optional — model-list/registration checks degrade to informational when Ollama itself is down. Run: `python tests/test_ollama.py`. |
| `test_features.py` | Unit test suite — covers RAG, memory, worker, shell, execution, hooks, multi-agent, and more |
| `test_mcp_unit.py` | Unit tests for MCP connection reliability — session pool lifecycle/eviction/concurrency/idle-reaper, the owner-task-per-connection pattern (cross-task open/evict/reuse/reap), registration-from-cache (P-06a), and dead-server tool pruning. Mocked `MCPConnection`, no real server needed |
| `test_tool_adapter_unit.py` | Unit tests for MCP tool schema conversion (M-06) — nested objects, `$ref`/`$defs`/`definitions`, `anyOf`/`oneOf`/`allOf`, arrays of any of the above, schema round-trip fidelity through `.model_json_schema()`, and (V6.50) defaulted fields listed under `required` being treated as optional |
| `test_appclose_cancellation_unit.py` | Unit tests for the app-close task-cancellation backend (V6.20) — `GET /executions/running`, pre-generated execution IDs, per-conversation history trim, and an end-to-end proof (real `ExecutionRegistry`/`LocalBroker`) that cancelling a daemon actually flips its running `cancel_token`, not just a broker status label |
| `test_duplicate_tool_call_unit.py` | Unit tests for the duplicate-tool-call guard: a first call that activates an undescribed tool is a real, counted call (V6.50 — the V6.20 stub exemption was removed along with the stub; a genuine repeat is still caught, two different tools never collide) plus the V6.26 consecutive-streak/tiered-threshold redesign (an interleaved repeat pattern never trips the guard, a `destructive` tool trips at the lowest threshold, a `read_only` tool tolerates more back-to-back repeats, int/str arguments no longer collide, and a fingerprinting error no longer merges unrelated calls into one bucket) |
| `test_prompt_audit_fixes_unit.py` | Unit tests for the prompt-file accuracy audit (V6.21) — the S-10 profile-lock guard on both extraction branches, agent-entry `sensitive`-flag wiring end to end, the coordinator's dynamic per-phase task cap, and text regression guards for the remaining prompt-text-only fixes |
| `test_background_process_unit.py` | Unit tests for background process supervision (V6.37, plus V6.49 Phase 3) — `ShellExecutor.start_background()`/`stop_background()`/`tail_background()` against real spawned subprocesses (not mocked), Windows-correct PID liveness (`GetExitCodeProcess`, not just `OpenProcess`), process-tree kill, offset-tracked log tailing, max-lifetime force-stop, terminal-row retention purge, `GET /executions/running` including background-process rows (incl. the `command` display-string field added for the frontend's Activity panel), and the `POST /processes/{id}/stop` endpoint (ownership, 404/409 handling, `process.stopped` event publish) |
| `test_background_process_live.py` | Standalone live-server script (V6.49 Phase 3) — real `ShellExecutor.start_background()` against the running server's own `eva.db`, `GET /executions/running` visibility (incl. `command`), `POST /processes/{id}/stop` killing the real OS process (confirmed via independent `tasklist`), a live `process.stopped` SSE event, and 404/409/cross-user-ownership handling. Run: `python tests/test_background_process_live.py` |
| `test_daemon_kind_unit.py` | Unit tests for the daemon `kind` field (V6.38, standard/system/supervisor) — registry upsert read/write, the `system`-kind exclusion from the artifact-updater hook, the API's rejection of a client-supplied `kind="system"`, and the clone-endpoint kind demotion |
| `test_default_mcps_unit.py` | Unit tests for the default MCP seeder and Playwright default (V6.50) — the shipped `playwright.yaml` (valid, disabled, pinned, screenshots-to-file, identical with/without PyYAML), placeholder resolution, insert-only/idempotent/per-user seeding, `pending_install` reporting, invalid-definition rejection, seeding never connecting to the server, `POST /mcp/seed-defaults`, and MCP result normalization (image blocks become a placeholder, never base64) |
| `test_playwright_mcp_live.py` | Standalone live-server script (V6.50) — signup seeds the disabled config, `POST /mcp/seed-defaults` is a no-op re-seed, enabling launches the real Playwright MCP and syncs its tools, then real browser tool calls through `MCPRuntime` against a local page (snapshot, console error, failed request, screenshot as a workspace file, no base64). Run: `python tests/test_playwright_mcp_live.py` |
| `test_browser_agent_loop_live.py` | Standalone live-server script **with a real LLM** (V6.50) — one plain-language request; the agent must write a page with a deliberate JS bug, serve it via `background_process`, open it with the Playwright MCP, read the real console error, fix it, reload and stop the server. Checks the trace, the file on disk and the port rather than the model's summary, and answers the step-limit gate over SSE. Non-deterministic; `EVA_LIVE_MODEL` overrides the model (default `ollama_cloud/gpt-oss:120b`). Run: `python tests/test_browser_agent_loop_live.py` |
| `test_prompt_tool_references_unit.py` | Guards against instructions that name tools or behaviour that don't exist (V6.50) — a denylist of historically wrong tool names across tool descriptions, prompts, skills and the skills menu; every skill `allowed-tools` entry and default agent tool list names a real tool; no tool text (descriptions, argument docs, runtime error strings) tells a model to call an EVA REST endpoint; skill `.skill` bundles equal their folders; and `skills/playwright-browser-testing` matches the pinned Playwright MCP (tool names, worked JSON examples' parameter names, version) |
| `test_progressive_gate_unit.py` | The progressive-disclosure gates in `ToolDispatcher` (V6.50) — a first call to an undescribed tool is promoted and executed (not stubbed), only that call is flagged `auto_activated`, and it is no bypass: bad arguments still fail validation, an unknown tool still fails, a read-only session still refuses a mutating tool |
| `test_pev_provider_failure_unit.py` | A plan/execute run whose provider fails for good (e.g. exhausted API credit) ends `failed` with the provider's message instead of `completed` (V6.50); runs that produced any output are unchanged |
| `test_session_hydration_unit.py` | `build_prefs_dict()` carries every stored preference into the session (V6.50) — `max_step_extensions` was silently reset to 2 at every login and execution start — plus a guard that no future `UserPreference` column is dropped the same way |
| `test_step_record_unit.py` | Step records, verifier evidence and the real `_pev_stream` loop driven with a fake agent/planner/verifier (V6.50, A-13/A-14) — one line per tool call, size caps, earlier steps reach later prompts, the verifier receives the tool lines |
| `test_replan_and_cwd_unit.py` | Re-plan keeps the rest of the goal (`_keep_remaining_steps`, prompt, fallbacks, the live failure reproduced through the real step loop) and relative `cwd` resolution (V6.50, A-15) |
| `test_active_tools_line_unit.py` | The names-only "tools already active" line, the softer manifest / `describe_tool` wording and the new `promoted_count` / `describe_tool_calls` log fields (V6.50) |
| `test_environment_wiring_unit.py` | Calls the REAL `_assemble_system_prompt` — fails if the Environment block is missing or its "injection failed" warning is logged (V6.50 incident guard) |
| `test_no_undefined_names_unit.py` | Static `symtable` check that no function in the modules touched by V6.50 references an undefined name (the class of bug a "non-fatal" `try/except` hides) |
| `test_environment_working_folder_unit.py` | The Environment block names the working folder's kind and never an absolute path (V6.50) |
| `test_list_files_dirs_unit.py` | `list_files` lists subfolders by default; `include_dirs=false` for files only (V6.50) |
| `test_shell_timeout_tree_unit.py` | Real processes: a `shell` command that times out or is cancelled kills its grandchildren too (V6.50) |
| `test_shell_safety_windows_paths_unit.py` | S-14 (V6.50): the path-traversal check's Windows-only switch exemption (`/c`, `/s`, `/MIR`, …), real Windows/UNC path detection, `C:\Windows\`/`C:\Program Files\`/`C:\ProgramData\` exclusions (including the space-truncation fix), and fail-closed-on-any-resolution-error — 27 tests |
| `test_mcp_sync_error_handling_unit.py` | B-100 (V6.50): `POST /mcp/{id}/sync` reports a connection failure via `sync_error` (`200`) instead of an unhandled `500`, mirroring the enable path; 400/404 paths unaffected |
| `test_multimodal_unit.py` | M-07 (V6.50): `core/providers/multimodal.py` — `is_vision_capable()` trusts only a confirmed `True`, the canonical (OpenAI-style) multimodal content builders, and the Anthropic data-URI parser |
| `test_mcp_vision_unit.py` | M-07 (V6.50): an MCP tool result image is kept as real content only for a confirmed vision-capable model; `build_tool_message()` never mutates the tool-result dict it's handed |
| `test_anthropic_vision_unit.py` | M-07 (V6.50): `AnthropicHandler._convert_messages` translates multimodal content into Anthropic's image-block shape and merges adjacent same-role turns (Anthropic requires strict alternation) |
| `test_memory_engine_vision_unit.py` | M-07 (V6.50): `MemoryEngine._image_attachments_for_vision()` reconstructs a chat-attached image from its real blob only for a vision-capable model, with zero extra work otherwise |
| `test_capability_registry_ollama_cloud_unit.py` | A-17 (V6.52): `_resolve_ollama_cloud_model_name()`'s cloud/local/malformed cases, and the real `_merge_litellm()` merge path — Ollama Cloud registers with real context-window data, local Ollama never registers anywhere, other providers unaffected |
| `test_ollama_cloud_capabilities_endpoint_unit.py` | A-17 follow-up (V6.52): `GET /providers/{provider}/models/{model_id}/capabilities`'s split resolution order — local Ollama's registry-skip behaviour proven unchanged, Ollama Cloud reporting real registry data when available and the same optimistic default as local Ollama otherwise |
| `test_pev_final_answer_unit.py` | A-19/A-20 (V6.53): the real `_pev_stream()` loop's final-answer assembly — the last step's synthesis alone on a "success" verdict, the verifier's message appended on "pending"/"not_successful", the `plan_complete` chunk's new verdict fields, and the verdict persistence call |
| `test_plan_verifier_unit.py` | A-20 (V6.53): `PlanVerifierEngine.verify_plan()` — fail-closed-to-"pending" policy on any infra error, the real call path, message-required enforcement on non-"success", criteria-result ordering |
| `test_planner_success_criteria_unit.py` | A-20 (V6.53): the planning prompt's `success_criteria` schema, `_normalise_success_criteria()`'s every edge case, and both the permissive (LLM-output) and strict (user-edit) plan-validation paths |
| `test_execution_plan_success_criteria_persistence_unit.py` | A-20 follow-up (V6.53): real in-memory SQLite DB (not mocks) — `PlanRepo.create()`/`.update_steps()`/`.update_goal_verification()`'s exact column-touching behaviour, including the "don't touch a column when its param is None" guard |
| `eval_shell_routing_live.py` | Standalone tool-routing evaluation with a real model, executing nothing (V6.50) — offers `shell`/`background_process`/`trigger_daemon` with the old and new descriptions and counts which tool is picked first for 6 finite and 6 long-running tasks. Run: `python tests/eval_shell_routing_live.py` |
| `fixtures/` | Test fixtures — `playwright_mcp_tools.py` (the tool list and parameter names of the pinned Playwright MCP, shared by the skill guard and the live plumbing script); `mock_mcp_server.py`, a real MCP stdio server (official `mcp` SDK) used by `test_behaviour.py` to verify MCP tools actually reach and are usable by the model, not just that config CRUD/sync works |
| `conftest.py` | Pytest fixtures + `collect_ignore` for the three live-server scripts above |
