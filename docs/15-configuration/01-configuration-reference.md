# Configuration Reference

EVA has extensive platform fine-tuning available. Every feature has a built-in default — how many steps the agent takes, how long it waits for a model to respond, how aggressively memory extraction runs, how large a shell command's output can be, and dozens more. These defaults work well for most users without any configuration.

A power user can override any of these defaults by placing an `eva.env` file in the right location and adding the values they want to change. EVA reads this file on every startup. Only include the settings you want to override — everything else continues using the built-in default.

> **Provider API keys do not go here.** API keys for Groq, OpenAI, Anthropic, and all other cloud providers are stored in Windows Credential Manager — configured through the EVA UI (Account → API Keys). Never paste an API key into `eva.env`. This file is for non-secret runtime configuration only.

---

## Where to put eva.env

`eva.env` goes in the **EVA application data folder** on your machine. On Windows, open File Explorer, click the address bar, type `%APPDATA%\EVA` and press Enter — that is the folder where the file belongs.

EVA also creates and writes to this file automatically for certain settings you configure through the UI — for example, saving an Ollama host URL writes that value into `eva.env` directly. If the file already exists from that, open it and add your overrides alongside whatever EVA has already written. If it doesn't exist yet, create a new text file named `eva.env` in that folder.

---

## Port overrides

The ports EVA runs on aren't part of `eva.env` — the app window itself needs to know them before it can even start the backend that reads that file. Instead, set these as real environment variables on your system before launching EVA:

| Variable | Default | What it controls |
|----------|---------|-------------------|
| `EVA_BACKEND_PORT` | `8000` | The port the backend server listens on. |
| `EVA_FRONTEND_PORT` | `5174` | The port the app's interface is served on. |

Only needed if the default port is already in use by something else on your machine — EVA's startup failure dialog names the port and suggests this if that happens.

---

## Format

`eva.env` is a plain text file. Each line is `KEY=value`. Lines starting with `#` are comments and are ignored. You do not need to restart EVA between saves if using the template below — a full restart is required for changes to take effect.

---

## Full template

Copy the block below, save it as `eva.env` in the correct location, uncomment the lines you want to change, and adjust the values. Lines left commented will use EVA's built-in defaults.

```bash
# ─── Agent engine ─────────────────────────────────────────────────────────────
# MAX_REACT_STEPS=8
# EVA_FIRST_CHUNK_TIMEOUT_S=120
# EVA_INTER_CHUNK_TIMEOUT_S=30
# CONTEXT_WATCHDOG_TRIGGER_RATIO=0.85
# CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS=8192
# DUPLICATE_TOOL_CALL_THRESHOLD_READ_ONLY=5
# DUPLICATE_TOOL_CALL_THRESHOLD_MUTATING=3
# DUPLICATE_TOOL_CALL_THRESHOLD_DESTRUCTIVE=2

# ─── Fallback chain ───────────────────────────────────────────────────────────
# FALLBACK_RETRY_BASE_DELAY_S=0.5
# FALLBACK_RETRY_MAX_DELAY_S=8.0
# FALLBACK_MAX_RETRIES_RATE_LIMIT=2
# FALLBACK_MAX_RETRIES_TIMEOUT=1
# FALLBACK_MAX_RETRIES_PROVIDER_DOWN=2
# FALLBACK_COOLDOWN_BASE_SECONDS=30
# FALLBACK_COOLDOWN_MAX_SECONDS=900
# FALLBACK_COOLDOWN_STREAK_TTL_SECONDS=3600

# ─── Tool execution ────────────────────────────────────────────────────────────
# TOOL_EXECUTION_TIMEOUT_SECONDS=300
# TOOL_MAX_RETRY_ATTEMPTS=3
# DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS=1800
# SEARCH_TOOL_MAX_RESULTS=200
# SEARCH_TOOL_TIME_BUDGET_SECONDS=10
# SEARCH_TOOL_MAX_FILES_VISITED=50000
# SEARCH_TOOL_MAX_FILE_BYTES=2097152
# SEARCH_TOOL_MAX_LINE_CHARS=20000
# SEARCH_TOOL_SKIP_DIRS=.git,node_modules,.venv,venv,__pycache__,.mypy_cache,.pytest_cache,.tox

# ─── Shell tool ───────────────────────────────────────────────────────────────
# SHELL_DEFAULT_TIMEOUT_SECONDS=30
# SHELL_MAX_OUTPUT_BYTES=524288
# SHELL_SESSION_TTL_SECONDS=86400
# SHELL_SESSION_MAX_HISTORY=50
# SHELL_MAX_TIMEOUT_SECONDS=300

# ─── HTTP tool ────────────────────────────────────────────────────────────────
# HTTP_TOOL_MAX_RESPONSE_BYTES=10485760
# HTTP_TOOL_MAX_PAYLOAD_BYTES=1048576
# HTTP_TOOL_MAX_TIMEOUT_SECONDS=120

# ─── Hook scripts ─────────────────────────────────────────────────────────────
# HOOK_SCRIPT_MAX_BYTES=1048576
# HOOK_COMMAND_TIMEOUT_SECONDS=30
# HOOK_HTTP_TIMEOUT_SECONDS=10

# ─── MCP servers ──────────────────────────────────────────────────────────────
# ENABLE_MCP=true
# MCP_CONNECT_TIMEOUT_SECONDS=60
# MCP_INIT_TIMEOUT_SECONDS=60
# MCP_CALL_TIMEOUT_SECONDS=60

# ─── Memory ───────────────────────────────────────────────────────────────────
# MEMORY_MIN_MESSAGES_FOR_EXTRACTION=3
# MEMORY_MAX_FACTS_PER_EXTRACTION=5
# MEMORY_MAX_ENTRIES_PER_EXTRACTION=5
# MEMORY_SENSITIVE_GRANT_TTL_SECONDS=3600
# AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS=90

# ─── RAG / Knowledge Base ─────────────────────────────────────────────────────
# CHROMA_PATH=~/eva_workspace/chroma_db
# CHROMA_COLLECTION=eva_rag
# RAG_JOB_TTL_SECONDS=3600
# RAG_INGEST_MAX_CHUNKS=100

# ─── Projects ─────────────────────────────────────────────────────────────────
# PROJECT_SYNC_DAEMON_MAX_STEPS=30
# PROJECT_FILES_MAX_WORKSPACE_ENTRIES=50000

# ─── Background work ──────────────────────────────────────────────────────────
# BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS=14400
# BACKGROUND_PROCESS_TAIL_MAX_BYTES=65536
# BACKGROUND_PROCESS_KILL_GRACE_SECONDS=5.0
# BACKGROUND_PROCESS_SHUTDOWN_KILL_GRACE_SECONDS=2.0
# BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS=24
# WAKEUP_SCHEDULER_POLL_SECONDS=30
# WAKEUP_MAX_DELAY_SECONDS=172800
# WAKEUP_MAX_PENDING_PER_USER=15
# SCHEDULED_WAKEUP_RETENTION_DAYS=7

# ─── Subagents ────────────────────────────────────────────────────────────────
# MAX_PARALLEL_TASKS=5
# MAX_COORDINATE_PHASES=5
# MAX_COORDINATE_TASKS_PER_PHASE=5
# MAX_COORDINATE_DYNAMIC_AGENTS=3

# ─── Approval gate ────────────────────────────────────────────────────────────
# TOOL_APPROVAL_TIMEOUT_SECONDS=3600
# PER_CONV_APPROVAL_TTL_SECONDS=604800
# APPROVAL_ARGS_PREVIEW_MAX_LEN=120

# ─── Plan-Execute-Verify runner ───────────────────────────────────────────────
# PLAN_APPROVAL_TIMEOUT_SECONDS=3600
# MAX_REPLAN_ATTEMPTS=3
# STEP_EXTENSION_TIMEOUT_SECONDS=3600
# STEP_EXTENSION_HEARTBEAT_SECONDS=60
# PLAN_REVISION_MAX_ROUNDS=2
# PEV_LOOKAHEAD_MAX_SKIP=2
# PEV_LOOKAHEAD_STEPS_CHARS=2000
# PEV_STEP_RECORD_TEXT_CHARS=1200
# PEV_STEP_RECORD_MAX_TOOL_LINES=25
# PEV_STEP_RECORD_TARGET_CHARS=90
# PEV_STEP_RECORD_ERROR_CHARS=120
# PEV_PRIOR_STEPS_MAX_CHARS=6000
# PEV_PLAN_VERIFIER_TRAIL_MAX_CHARS=20000

# ─── Providers & models ───────────────────────────────────────────────────────
# PROVIDER_CAPABILITY_REFRESH_TTL_SECONDS=3600
# PROVIDER_MODEL_CACHE_TTL_SECONDS=300
# LITELLM_COST_MAP_REFRESH_HOURS=24
# OLLAMA_HOST=http://localhost:11434
# OLLAMA_DEFAULT_MODEL=llama3.2:latest
# OLLAMA_CLOUD_BASE_URL=https://ollama.com/v1
# EVA_VISION_ENABLED=true
# IMAGE_INLINE_MAX_BYTES=5242880

# ─── Authentication & security ────────────────────────────────────────────────
# TOKEN_EXPIRE_MINUTES=1440
# REFRESH_TOKEN_EXPIRE_DAYS=7
# EVA_HOST=127.0.0.1

# ─── Database ─────────────────────────────────────────────────────────────────
# DATABASE_URL=sqlite+aiosqlite:///./eva.db

# ─── Workspace & storage ──────────────────────────────────────────────────────
# EVA_WORKSPACE_ROOT=~/eva_workspace
# EVA_WORKSPACE_TTL_DAYS=90
# EVA_CLEANUP_INTERVAL_SECONDS=86400

# ─── Authentication retention ──────────────────────────────────────────────────
# AUTH_AUDIT_LOG_RETENTION_DAYS=180

# ─── Authentication rate limiting ──────────────────────────────────────────────
# AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS=900
# AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS=5
# AUTH_SIGNUP_RATE_LIMIT_WINDOW_SECONDS=3600
# AUTH_SIGNUP_RATE_LIMIT_MAX_CALLS=5
# AUTH_REFRESH_RATE_LIMIT_WINDOW_SECONDS=60
# AUTH_REFRESH_RATE_LIMIT_MAX_CALLS=30
# SECRETS_RATE_LIMIT_WINDOW_SECONDS=300
# SECRETS_RATE_LIMIT_MAX_CALLS=10

# ─── Logging ──────────────────────────────────────────────────────────────────
# EVA_LOG_DIR=./logs
# EVA_LOG_SERVER_MAX_BYTES=52428800
# EVA_LOG_OBS_MAX_BYTES=104857600
# EVA_LOG_ERROR_MAX_BYTES=10485760
# EVA_LOG_SERVER_BACKUP_COUNT=30
# EVA_LOG_OBS_BACKUP_COUNT=60
# EVA_LOG_ERROR_BACKUP_COUNT=90
# EVA_LOG_OBS_PROPAGATE=false

# ─── Low-level infrastructure (change only if you know what you're doing) ──────
# EXECUTION_TTL_SECONDS=86400
# EXECUTION_HEARTBEAT_SECONDS=30
# WATCHDOG_INTERVAL_SECONDS=60
# WATCHDOG_RUNNING_TIMEOUT_SECONDS=900
# WATCHDOG_PENDING_TIMEOUT_SECONDS=1800
# WATCHDOG_APPROVAL_TIMEOUT_SECONDS=14400
# WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS=14400
# WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS=14400
# WORKER_HEARTBEAT_INTERVAL_SECONDS=15
# WORKER_HEARTBEAT_KEY_TTL_SECONDS=30
# WORKER_LIVENESS_TTL_SECONDS=90
# SSE_HEARTBEAT_SECONDS=30
# SSE_STALE_POLL_SECONDS=30
# BLACKBOARD_TTL_SECONDS=7200
# BLACKBOARD_ENCRYPT=false
# DAEMON_SCHEDULER_POLL_SECONDS=60
# WEBHOOK_RATE_LIMIT_WINDOW_SECONDS=60
# WEBHOOK_RATE_LIMIT_MAX_CALLS=10
# INTENT_CLASSIFIER_MAX_TOKENS=100
# INTENT_CLASSIFIER_TEMPERATURE=0.0
# APPROVAL_HEARTBEAT_INTERVAL_SECONDS=60
# PLAN_APPROVAL_HEARTBEAT_SECONDS=60
# CLEANUP_RETRY_MAX_ATTEMPTS=3
# CLEANUP_RETRY_BASE_DELAY_SECONDS=60
# CLEANUP_RETRY_MAX_DELAY_SECONDS=300
```

Restart the EVA app for changes to take effect.

> The groupings below are for documentation clarity — all settings are part of a single flat configuration space with no enforced internal separation.

---

## Default session

These are EVA's hardcoded starting-point values, defined in `config/settings.py`. They are **not** configurable via `eva.env` — they are code defaults. Users change them through the EVA UI (Session panel, Providers tab, etc.), and EVA saves those choices to the database and remembers them from that point forward. This table is here for reference only — if you need to change a default for a deployment, edit `config/settings.py` directly.

| Key | Default | Description |
|-----|---------|-------------|
| `provider` | `groq` | Default AI provider for new sessions. |
| `model` | `openai/gpt-oss-20b` | Default model. Must be valid for the chosen provider. |
| `reasoning` | `null` | Default reasoning level. `null` = off. Accepts a level string (`"minimal"` / `"low"` / `"medium"` / `"high"` / `"xhigh"`) or `null`. Stored as a string column in the database since V6.7. |
| `tool_approval_mode` | `auto` | When the Human-in-the-Loop approval gate fires. `auto` = never ask; `always_ask` = ask before every mutating/destructive call; `per_conversation` = ask once per conversation then auto-approve the rest. |
| `allowed_authorities` | `read_only,mutating,destructive` | Which tool authority levels are permitted. Remove levels to restrict what the agent can do system-wide. |
| `agent_mode` | `auto` | How the intent router classifies messages. `auto` = decide dynamically; `chat` = always conversational; `plan_execute` = always run the Plan→Execute→Verify loop. |
| `require_plan_approval` | `false` | When `true`, the PEV runner pauses after generating a plan and waits for a user decision — approve as generated, edit and approve, or reject — before executing any steps. |
| `pii_redaction_enabled` | `false` | When `true`, emails, phone numbers, SSNs and card, bank, passport and licence numbers in your messages are replaced with placeholders (e.g. `<EMAIL_ADDRESS>`) before the message is stored, journaled or sent to the model. Names, locations and dates are deliberately left alone. Detection runs entirely on your machine. See [Privacy & Security](../16-appendix/02-privacy-and-security.md). |
| `pev_lookahead_skip_enabled` | `true` | When `true`, a Plan-Execute-Verify step whose work an earlier step already accomplished is skipped instead of re-run. Never skips the plan's final step. Shown as **Skip completed steps** in Session settings. |
| `max_step_extensions` | `2` | Number of times a PEV step's reasoning loop may be extended past `MAX_REACT_STEPS` when you choose "Continue" at the step-limit gate. `0` disables the gate — EVA always stops at the limit instead of asking. |
| `user_memory_enabled` | `true` | Whether EVA extracts and injects user-level memory facts. |
| `agent_memory_enabled` | `true` | Whether EVA uses shared agent-scope memory across conversations. |
| `agent_scope_key` | `null` | Default scope key for agent memory. `null` = disabled until set. Automatically linked to the active project when one is open. |
| `skills_mode` | `auto` | Controls skills catalogue injection. `auto`/`enabled` = inject when skills are loaded; `disabled` = never inject (zero token overhead). |
| `tool_hooks` | `[]` | Default hook definitions applied to tool calls. Empty by default — all tools run unfiltered. |
| `shell_readonly` | `false` | When `true`, the shell tool blocks all non-read-only commands. Useful for review/inspection sessions. |
| `user_timezone` | `null` | IANA timezone string injected into every system prompt (e.g. `Asia/Kolkata`). `null` = not injected. |
| `user_location` | `null` | Free-text location injected into every system prompt (e.g. `Mumbai, India`). `null` = not injected. |
| `user_language` | `null` | BCP-47 language tag injected into every system prompt (e.g. `en-IN`). `null` = not injected. |

---

## Agent engine

Controls the core agent loop and provider timeout behaviour.

| Key | Default | Description |
|-----|---------|-------------|
| `MAX_REACT_STEPS` | `8` | Maximum iterations the ReAct loop may run per execution. Increase for complex multi-step tasks; lower to prevent runaway loops on simpler workloads. In PEV mode, reaching this limit can trigger the step-limit "Continue?" gate — see `max_step_extensions` above. |
| `EVA_FIRST_CHUNK_TIMEOUT_S` | `120` | Seconds to wait for the very first token from a provider before treating it as a timeout. Reasoning models (Claude extended-thinking, DeepSeek-R1, o3) can be silent for 60–90 s while thinking — 120 s is the safe default. |
| `EVA_INTER_CHUNK_TIMEOUT_S` | `30` | Seconds allowed between consecutive tokens once streaming has started. A 30 s gap almost always means a TCP stall — this fires the fallback chain. |
| `CONTEXT_WATCHDOG_TRIGGER_RATIO` | `0.85` | Once the running conversation reaches this fraction of the model's context window, EVA compresses older tool-call history in place to free up room, keeping the most recent exchange intact. |
| `CONTEXT_WATCHDOG_FALLBACK_MAX_TOKENS` | `8192` | Context-window size assumed for models EVA has no capability data for (used by both the compression trigger above and per-tool-result truncation). A conservative estimate rather than skipping the protection entirely. |
| `DUPLICATE_TOOL_CALL_THRESHOLD_READ_ONLY` | `5` | How many times in a row a read-only tool (e.g. `read_file`, `web_search`) may be called with identical arguments before EVA aborts the step as a stuck loop. Generous, since re-checking or re-polling something read-only is cheap and often legitimate. |
| `DUPLICATE_TOOL_CALL_THRESHOLD_MUTATING` | `3` | Same guard for mutating tools (e.g. `write_file`, `memory_store`). |
| `DUPLICATE_TOOL_CALL_THRESHOLD_DESTRUCTIVE` | `2` | Same guard for destructive tools (e.g. `delete_file`, `git_push`) — the least tolerance, since a repeated identical call here is the most costly to get wrong. The count only tracks *consecutive* repeats of the exact same call; a different call in between resets it, so a legitimate "check, fix, check again" pattern doesn't trip the guard. |

---

## Provider fallback chain

When a provider call fails, EVA retries the same provider a configurable number of times (with exponential backoff) before moving to the next entry in the fallback chain. After enough consecutive failures, that provider enters a cooldown period and is skipped automatically by subsequent executions until it recovers.

**Retry-before-failover** — for rate limits, timeouts, and provider outages:

| Key | Default | Description |
|-----|---------|-------------|
| `FALLBACK_RETRY_BASE_DELAY_S` | `0.5` | Seconds before the first same-provider retry. Doubles with each attempt (exponential backoff). |
| `FALLBACK_RETRY_MAX_DELAY_S` | `8.0` | Ceiling on the backoff delay regardless of retry count. |
| `FALLBACK_MAX_RETRIES_RATE_LIMIT` | `2` | Same-provider retries for a rate-limit (429) response before falling back to the next chain entry. |
| `FALLBACK_MAX_RETRIES_TIMEOUT` | `1` | Same-provider retries for a stalled-connection timeout. |
| `FALLBACK_MAX_RETRIES_PROVIDER_DOWN` | `2` | Same-provider retries for a 5xx or connection-refused response. |

**Cross-execution cooldown** — after retries are exhausted, the provider is put into cooldown and skipped by all subsequent executions until it recovers:

| Key | Default | Description |
|-----|---------|-------------|
| `FALLBACK_COOLDOWN_BASE_SECONDS` | `30` | Initial cooldown duration in seconds. Doubles on each consecutive cooldown-triggering failure for the same provider. |
| `FALLBACK_COOLDOWN_MAX_SECONDS` | `900` (15 min) | Maximum cooldown duration regardless of failure streak length. |
| `FALLBACK_COOLDOWN_STREAK_TTL_SECONDS` | `3600` (1 h) | How long the consecutive-failure streak counter survives before resetting. Ensures a provider that recovers and then fails again starts at the base cooldown rather than escalating from the previous streak. |

---

## Tool execution

| Key | Default | Description |
|-----|---------|-------------|
| `TOOL_EXECUTION_TIMEOUT_SECONDS` | `300` | Maximum wall-clock seconds any single tool invocation may run before it is cancelled and retried. Each retry gets a progressively longer window (doubling, capped at 4× this value), so a tool that's genuinely close to finishing isn't cancelled repeatedly at the exact same timeout. |
| `TOOL_MAX_RETRY_ATTEMPTS` | `3` | Number of retry attempts for transient tool failures before giving up and returning the error to the agent. |
| `DELEGATE_TOOL_EXECUTION_TIMEOUT_SECONDS` | `1800` (30 min) | Timeout override specifically for `delegate_to_agent`, which wraps an entire nested subagent run and needs far more room than a typical single-shot tool. The subagent's own step limit and connection timeouts are the real bound on its work; this is just a generous backstop. |
| `SEARCH_TOOL_MAX_RESULTS` | `200` | Maximum results `search_files`/`find_files` return before truncating with a "narrow your pattern" message, rather than hanging or dumping an unbounded result set. |
| `SEARCH_TOOL_TIME_BUDGET_SECONDS` | `10` | Wall-clock budget for a single file search. When it runs out, the search stops and returns what it found so far, marked as incomplete, so the agent can narrow the path. Searches run on a separate worker, so a slow one never freezes the rest of EVA. |
| `SEARCH_TOOL_MAX_FILES_VISITED` | `50000` | The most files one search will look at before stopping and reporting that its result may be incomplete. |
| `SEARCH_TOOL_MAX_FILE_BYTES` | `2097152` (2 MB) | `search_files` doesn't read inside files larger than this (logs, dumps, databases). |
| `SEARCH_TOOL_MAX_LINE_CHARS` | `20000` | Only the first part of a very long line (such as a minified file) is matched and returned. |
| `SEARCH_TOOL_SKIP_DIRS` | `.git, node_modules, .venv, venv, __pycache__, .mypy_cache, .pytest_cache, .tox` | Comma-separated folder names a search never descends into. This applies only *below* the path the search is pointed at — pointing a search straight at one of these folders still searches it. |

---

## Shell tool

| Key | Default | Description |
|-----|---------|-------------|
| `SHELL_DEFAULT_TIMEOUT_SECONDS` | `30` | Default timeout for individual shell commands. Users may request lower values; they cannot exceed this ceiling. |
| `SHELL_MAX_OUTPUT_BYTES` | `524288` (512 KB) | Maximum stdout/stderr bytes captured from a single shell command. Prevents memory exhaustion from commands like `cat largefile`. |
| `SHELL_SESSION_TTL_SECONDS` | `86400` (24 h) | TTL for the shell session state (current directory, environment variables) stored between tool calls in a conversation. |
| `SHELL_SESSION_MAX_HISTORY` | `50` | Maximum number of command strings retained in the shell session history. |
| `SHELL_MAX_TIMEOUT_SECONDS` | `300` (5 min) | Hard upper bound on how long any single shell command may run, regardless of the timeout requested. Anything meant to run longer belongs in the `background_process` tool instead. |

---

## HTTP tool

| Key | Default | Description |
|-----|---------|-------------|
| `HTTP_TOOL_MAX_RESPONSE_BYTES` | `10485760` (10 MB) | Maximum response body size accepted by the `http_get` / `http_post` tools. Prevents memory exhaustion on large responses. |
| `HTTP_TOOL_MAX_PAYLOAD_BYTES` | `1048576` (1 MB) | Maximum request body size for `http_post` payloads. |
| `HTTP_TOOL_MAX_TIMEOUT_SECONDS` | `120` | Hard ceiling on user-supplied HTTP request timeouts. Users may specify any value; it is silently capped here. |

---

## Hook scripts

| Key | Default | Description |
|-----|---------|-------------|
| `HOOK_SCRIPT_MAX_BYTES` | `1048576` (1 MB) | Maximum file size accepted for an uploaded hook script. |
| `HOOK_COMMAND_TIMEOUT_SECONDS` | `30` | Timeout for shell-script hook execution. |
| `HOOK_HTTP_TIMEOUT_SECONDS` | `10` | Timeout for HTTP hook calls. |

---

## MCP servers

| Key | Default | Description |
|-----|---------|-------------|
| `ENABLE_MCP` | `true` | Master switch for the MCP subsystem. Set to `false` to disable all MCP server connections entirely. |
| `MCP_CONNECT_TIMEOUT_SECONDS` | `60` | Timeout for opening an MCP transport connection. |
| `MCP_INIT_TIMEOUT_SECONDS` | `60` | Timeout for the MCP initialise handshake after the transport connects. |
| `MCP_CALL_TIMEOUT_SECONDS` | `60` | Timeout for MCP tool list fetches and individual tool call operations. |

---

## Memory

| Key | Default | Description |
|-----|---------|-------------|
| `MEMORY_MIN_MESSAGES_FOR_EXTRACTION` | `3` | Minimum number of messages in a conversation before the memory extractor runs. Skips trivial "hello" conversations. |
| `MEMORY_MAX_FACTS_PER_EXTRACTION` | `5` | Maximum number of user facts the model may return in a single extraction call. |
| `MEMORY_MAX_ENTRIES_PER_EXTRACTION` | `5` | Maximum number of agent memory entries the model may return in a single extraction call. |
| `MEMORY_SENSITIVE_GRANT_TTL_SECONDS` | `3600` (1 h) | TTL for sensitive-memory access grants. After this window, access to sensitive memory requires re-approval. |
| `AGENT_MEMORY_ERROR_ENTRY_TTL_DAYS` | `90` | How long an agent-memory entry of type `error` is kept before it expires. Errors are the one kind of memory that goes stale without ever being revisited, so they expire; the other types are kept indefinitely. |

---

## RAG / Knowledge Base

| Key | Default | Description |
|-----|---------|-------------|
| `CHROMA_PATH` | `~/eva_workspace/chroma_db` | Filesystem path for the ChromaDB persistent vector database. Override to store the database on a different drive or volume. |
| `CHROMA_COLLECTION` | `eva_rag` | ChromaDB collection name. Changing this after ingesting documents means existing documents will not be found. |
| `RAG_JOB_TTL_SECONDS` | `3600` (1 h) | TTL for RAG ingestion job status keys. After this window, status for a completed job is no longer queryable. |
| `RAG_INGEST_MAX_CHUNKS` | `100` | Maximum chunks a single `rag_ingest` tool call may index. EVA can add content to your Knowledge Base mid-conversation (not just via manual upload) — this caps how much one call can index at once, so it can't balloon a single step's latency. |

---

## Projects

| Key | Default | Description |
|-----|---------|-------------|
| `PROJECT_SYNC_DAEMON_MAX_STEPS` | `30` | Step budget for the sync daemon EVA automatically creates for every project (see [What are Projects?](../05-projects/01-what-are-projects.md)). This daemon explores your project's workspace and keeps `knowledge.eva` current — automatically at creation, or on demand via **Sync now**. |
| `PROJECT_FILES_MAX_WORKSPACE_ENTRIES` | `50000` | The most workspace files a project's Files tab will list. A project backed by a very large codebase shows the first files in path order, with a notice that the list was cut short. Knowledge and uploaded files are never cut. Ordinary projects never reach this. |

---

## Background work

Settings for background processes (`background_process`) and paused tasks (`schedule_wakeup`). See [Background work](../03-tools/01-what-are-tools.md#background-work).

| Key | Default | Description |
|-----|---------|-------------|
| `BACKGROUND_PROCESS_MAX_LIFETIME_SECONDS` | `14400` (4 h) | A background process still running after this long is stopped automatically — a hard ceiling so nothing runs unattended indefinitely. |
| `BACKGROUND_PROCESS_TAIL_MAX_BYTES` | `65536` (64 KB) | Maximum output returned by one read of a process's log. |
| `BACKGROUND_PROCESS_KILL_GRACE_SECONDS` | `5.0` | How long EVA waits after asking a process to stop before force-killing it. |
| `BACKGROUND_PROCESS_SHUTDOWN_KILL_GRACE_SECONDS` | `2.0` | The same grace period, used specifically when EVA itself is quitting rather than when you click Stop. Shorter, since the whole app is closing anyway. |
| `BACKGROUND_PROCESS_TERMINAL_RETENTION_HOURS` | `24` | How long the record and log of a stopped, crashed or finished process are kept before the daily cleanup deletes them. Running processes are never removed by this. |
| `WAKEUP_SCHEDULER_POLL_SECONDS` | `30` | How often EVA checks whether a paused task is due to resume, or the background task it's watching has finished. |
| `WAKEUP_MAX_DELAY_SECONDS` | `172800` (48 h) | Longest a task may pause for in a single wait. Also the fallback limit when a task waits on another task with no delay of its own, so a wait can never hang forever. |
| `WAKEUP_MAX_PENDING_PER_USER` | `15` | Maximum paused tasks you can have at once. |
| `SCHEDULED_WAKEUP_RETENTION_DAYS` | `7` | How long the record of a resumed or cancelled pause is kept. |

---

## Subagents (multi-agent)

| Key | Default | Description |
|-----|---------|-------------|
| `MAX_PARALLEL_TASKS` | `5` | Maximum subagents that can run concurrently in a `parallel_delegate` call. Increase for heavier parallelism; be mindful of provider rate limits. |
| `MAX_COORDINATE_PHASES` | `5` | Maximum number of phases the `coordinate` tool may plan across. |
| `MAX_COORDINATE_TASKS_PER_PHASE` | `5` | Maximum subagent tasks per phase in the `coordinate` tool. |
| `MAX_COORDINATE_DYNAMIC_AGENTS` | `3` | Maximum temporary agent personas the `coordinate` tool may invent in a single plan when nothing in your registry fits. Separate from the two limits above: they bound how much work is planned, this bounds how many never-before-seen agents one unreviewed planning step can create. |

---

## Human-in-the-loop approval gate

| Key | Default | Description |
|-----|---------|-------------|
| `TOOL_APPROVAL_TIMEOUT_SECONDS` | `3600` (1 h) | Maximum seconds the approval gate waits for user approve/reject before auto-cancelling the execution. |
| `PER_CONV_APPROVAL_TTL_SECONDS` | `604800` (7 days) | TTL for the per-conversation auto-approval record (`per_conversation` mode). Once a user approves within a conversation, this controls how long that approval remains valid. |
| `APPROVAL_ARGS_PREVIEW_MAX_LEN` | `120` | Maximum character length of an argument value shown in the approval UI preview. Longer values are truncated with `…<truncated>`. |

---

## Plan-Execute-Verify runner

| Key | Default | Description |
|-----|---------|-------------|
| `PLAN_APPROVAL_TIMEOUT_SECONDS` | `3600` (1 h) | Seconds to wait for a user decision (approve/edit/reject) on a generated plan before auto-cancelling the execution. |
| `MAX_REPLAN_ATTEMPTS` | `3` | Maximum adaptive re-planning attempts per execution. If verification keeps failing after this many replans, the runner accepts the failure rather than looping indefinitely. |
| `STEP_EXTENSION_TIMEOUT_SECONDS` | `3600` (1 h) | Seconds to wait for a Continue/Stop decision at the step-limit gate before automatically stopping the execution. |
| `STEP_EXTENSION_HEARTBEAT_SECONDS` | `60` | How often the step-limit gate refreshes its TTL while waiting for your decision. |
| `PLAN_REVISION_MAX_ROUNDS` | `2` | Maximum times a plan you reject with feedback is revised and re-shown for approval before a further rejection cancels the execution instead. |
| `PEV_LOOKAHEAD_MAX_SKIP` | `2` | Most upcoming steps EVA may skip in one go when a step's work already covers them (the **Skip completed steps** feature). A hard ceiling regardless of what the verifier reports. |
| `PEV_LOOKAHEAD_STEPS_CHARS` | `2000` | Size cap for the list of upcoming steps shown to the verifier when it checks for skippable work. |
| `PEV_STEP_RECORD_TEXT_CHARS` | `1200` | Maximum characters of an earlier step's own text carried forward into later steps. |
| `PEV_STEP_RECORD_MAX_TOOL_LINES` | `25` | Maximum tool-call lines kept per step in that record (the most recent; older ones are counted, not listed). |
| `PEV_STEP_RECORD_TARGET_CHARS` | `90` | Maximum characters of "what the tool acted on" in each tool-call line. |
| `PEV_STEP_RECORD_ERROR_CHARS` | `120` | Maximum characters of an error message in each tool-call line. |
| `PEV_PRIOR_STEPS_MAX_CHARS` | `6000` | Size cap for the whole "work already done" block added to each step. When a plan is long, older steps are cut down to a title and one line first, so the newest stay complete. |
| `PEV_PLAN_VERIFIER_TRAIL_MAX_CHARS` | `20000` | Same block, for the one-time final check of the finished plan. Larger, since nothing follows it that needs prompt room. |

---

## Providers & models

| Key | Default | Description |
|-----|---------|-------------|
| `PROVIDER_CAPABILITY_REFRESH_TTL_SECONDS` | `3600` (1 h) | How often EVA refreshes its capability database from the LiteLLM pricing dataset. |
| `PROVIDER_MODEL_CACHE_TTL_SECONDS` | `300` (5 min) | TTL for the live model-listing cache. After this window, the next model list request fetches fresh from the provider API. |
| `LITELLM_COST_MAP_REFRESH_HOURS` | `24` | How many hours before the LiteLLM cost map is considered stale and re-fetched. |
| `OLLAMA_HOST` | `http://localhost:11434` | Base URL for the local Ollama server. Override if Ollama runs on a different port or host. (Can also be set through the EVA UI in Account → Local Models.) |
| `OLLAMA_DEFAULT_MODEL` | `llama3.2:latest` | Default Ollama model used when no model is explicitly selected. |
| `OLLAMA_CLOUD_BASE_URL` | `https://ollama.com/v1` | Base URL for the Ollama Cloud API. Only change if Ollama changes their API endpoint. |
| `EVA_VISION_ENABLED` | `true` | Master switch for sending images to the model as real images (screenshots from the browser tools, images you attach). Only takes effect for models EVA has confirmed can read images. Set to `false` to turn it off everywhere — for a cost or privacy reason, say — and images fall back to a text placeholder. |
| `IMAGE_INLINE_MAX_BYTES` | `5242880` (5 MB) | Largest single image sent to the model as a real image. Larger ones fall back to the text placeholder rather than bloating the request. |

---

## Authentication & security

| Key | Default | Description |
|-----|---------|-------------|
| `TOKEN_EXPIRE_MINUTES` | `1440` (24 h) | Access token lifetime in minutes. Lower this for stricter session security (users will need to log in more often). |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime in days. After this period, the user must log in again. |
| `EVA_HOST` | `127.0.0.1` | Network address the EVA server listens on. The default accepts connections from this computer only. Set to `0.0.0.0` only if you deliberately want other devices on your network to be able to reach it — see [Privacy & Security](../16-appendix/02-privacy-and-security.md). |

---

## Database

| Key | Default | Description |
|-----|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./eva.db` | SQLAlchemy async database URL. Defaults to local SQLite, which is appropriate for single-user desktop use. For multi-user deployments, override with a PostgreSQL connection string: `postgresql+asyncpg://user:pass@localhost/eva_db`. |

---

## Workspace & storage

| Key | Default | Description |
|-----|---------|-------------|
| `EVA_WORKSPACE_ROOT` | `~/eva_workspace` | Root directory for all user workspace files (projects, skills, hooks, RAG uploads, conversation scratch directories). Override to place the workspace on a different drive. |
| `EVA_WORKSPACE_TTL_DAYS` | `90` | Number of days a conversation scratch directory must be untouched before the daily cleanup task may delete it. Only affects `conversations/{id}/` subdirectories — never the main workspace, projects, RAG, memory, skills, or hooks. |
| `EVA_CLEANUP_INTERVAL_SECONDS` | `86400` (24 h) | How often the workspace cleanup background loop runs. |

---

## Authentication retention

| Key | Default | Description |
|-----|---------|-------------|
| `AUTH_AUDIT_LOG_RETENTION_DAYS` | `180` | Days the login/logout/failed-attempt audit log is kept before the daily cleanup task deletes old rows. Auth sessions themselves are purged as soon as they're actually dead (revoked or past their refresh-token expiry), independent of this setting — this only controls the security audit trail's own retention window. No regulatory requirement applies to EVA; adjust to your own policy. |

---

## Authentication rate limiting

Applies to the unauthenticated endpoints — `POST /auth/login`, `POST /auth/signup`, `POST /auth/refresh`. EVA binds to all network interfaces by default (see [Network Exposure](../16-appendix/02-privacy-and-security.md) for why this matters even for a single-user install), so these are reachable by any device on the same network, not just your own machine. `/chat` and `/execute` already require a valid login token and are not separately rate-limited.

Login uses a dual-key limiter — both your IP address and your account name are tracked separately, and **only failed attempts count**. A successful login clears both counters, so mistyping your password a few times before getting it right never locks you out. The lockout is temporary, not permanent, and lifts automatically once the window passes.

| Key | Default | Description |
|-----|---------|-------------|
| `AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `900` (15 min) | Sliding window for counting failed login attempts. |
| `AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS` | `5` | Failed attempts allowed within the window (per IP, and separately per account) before a temporary lockout. |
| `AUTH_SIGNUP_RATE_LIMIT_WINDOW_SECONDS` | `3600` (1 h) | Sliding window for the signup limiter (per IP, every call counts). |
| `AUTH_SIGNUP_RATE_LIMIT_MAX_CALLS` | `5` | Signup calls allowed per IP within the window. |
| `AUTH_REFRESH_RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding window for the token-refresh limiter (per IP, every call counts). |
| `AUTH_REFRESH_RATE_LIMIT_MAX_CALLS` | `30` | Refresh calls allowed per IP within the window. Deliberately generous — refresh tokens are high-entropy and not realistically guessable, so this is an anti-hammering backstop rather than brute-force protection, and shouldn't trip during normal multi-tab/multi-device use. |
| `SECRETS_RATE_LIMIT_WINDOW_SECONDS` | `300` (5 min) | Sliding window for saving provider API keys. Each save makes a live check against the provider, so this limits how much a stolen login could spend of your provider quota. Counted per account. |
| `SECRETS_RATE_LIMIT_MAX_CALLS` | `10` | Key saves allowed per account within the window — generous for normal key rotation. |

---

## Logging

| Key | Default | Description |
|-----|---------|-------------|
| `EVA_LOG_DIR` | `<project_root>/logs/` | Directory for all server log files. In packaged deployments, set this to a known writable path (e.g. `%APPDATA%\EVA\logs`) before starting the backend. |
| `EVA_LOG_SERVER_MAX_BYTES` | `52428800` (50 MB) | Per-file size cap for the main server log before a mid-day rotation is triggered. |
| `EVA_LOG_OBS_MAX_BYTES` | `104857600` (100 MB) | Per-file size cap for the observability log. |
| `EVA_LOG_ERROR_MAX_BYTES` | `10485760` (10 MB) | Per-file size cap for the error log. |
| `EVA_LOG_SERVER_BACKUP_COUNT` | `30` | Number of rotated server log files to retain. |
| `EVA_LOG_OBS_BACKUP_COUNT` | `60` | Number of rotated observability log files to retain. |
| `EVA_LOG_ERROR_BACKUP_COUNT` | `90` | Number of rotated error log files to retain. |
| `EVA_LOG_OBS_PROPAGATE` | `false` | When `true`, observability events also appear in `server.log`. Useful for local debugging; keep `false` in production for a clean log split. |

---

## Low-level infrastructure

The settings below control internal timing and resource management. The defaults are safe for all normal deployments. Only change these if you have a specific reason — mistuning them can cause executions to be falsely declared stuck or allow runaway processes.

| Key | Default | Description |
|-----|---------|-------------|
| `EXECUTION_TTL_SECONDS` | `86400` (24 h) | How long a completed execution record is retained in the broker before expiry. |
| `EXECUTION_HEARTBEAT_SECONDS` | `30` | How often the runner writes a heartbeat into the execution record. The watchdog uses this to detect stalled executions. |
| `WATCHDOG_INTERVAL_SECONDS` | `60` | How often the watchdog scans all active executions for stuck states. |
| `WATCHDOG_RUNNING_TIMEOUT_SECONDS` | `900` (15 min) | If an execution in RUNNING, PLANNING, or VERIFYING state has not written a heartbeat within this window, it is declared hung and aborted. |
| `WATCHDOG_PENDING_TIMEOUT_SECONDS` | `1800` (30 min) | Timeout for an execution stuck in PENDING (never reached RUNNING). |
| `WATCHDOG_APPROVAL_TIMEOUT_SECONDS` | `14400` (4 h) | Safety-net timeout for executions stuck waiting on a tool approval. This is a backstop — the gate's own `TOOL_APPROVAL_TIMEOUT_SECONDS` fires first. |
| `WATCHDOG_PLAN_APPROVAL_TIMEOUT_SECONDS` | `14400` (4 h) | The same safety net for executions stuck waiting on a plan decision (approve / edit / reject). Backstop for `PLAN_APPROVAL_TIMEOUT_SECONDS`. |
| `WATCHDOG_STEP_EXTENSION_TIMEOUT_SECONDS` | `14400` (4 h) | The same safety net for executions stuck at the step-limit "Continue?" prompt. Backstop for `STEP_EXTENSION_TIMEOUT_SECONDS`. Kept as three separate settings so each can be tuned independently. |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `15` | How often the worker refreshes its liveness beacon. |
| `WORKER_HEARTBEAT_KEY_TTL_SECONDS` | `30` | TTL for the worker heartbeat key. Must be greater than `WORKER_HEARTBEAT_INTERVAL_SECONDS`. |
| `WORKER_LIVENESS_TTL_SECONDS` | `90` | TTL for the worker liveness beacon. Must be greater than 2× `WORKER_HEARTBEAT_INTERVAL_SECONDS`. |
| `SSE_HEARTBEAT_SECONDS` | `30` | How often the SSE event feed sends a keepalive comment to prevent proxy/browser connection timeouts. |
| `SSE_STALE_POLL_SECONDS` | `30` | Maximum seconds the SSE generator waits for an execution that has disappeared from the broker. |
| `BLACKBOARD_TTL_SECONDS` | `7200` (2 h) | Default TTL for the execution blackboard (shared scratchpad between agent and subagents). Extended on every write; deleted on execution completion. |
| `BLACKBOARD_ENCRYPT` | `false` | When `true`, blackboard values are encrypted at rest using AES-GCM. Requires the `cryptography` Python package. |
| `DAEMON_SCHEDULER_POLL_SECONDS` | `60` | How often the daemon scheduler checks for daemons due to run. |
| `WEBHOOK_RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding-window duration for the webhook rate limiter. |
| `WEBHOOK_RATE_LIMIT_MAX_CALLS` | `10` | Maximum webhook calls allowed per token within the rate limit window. |
| `INTENT_CLASSIFIER_MAX_TOKENS` | `100` | Maximum tokens the LLM intent classifier may use in its response. The classifier returns a single classification word, but a budget of 100 is needed to accommodate models with mandatory reasoning that consume tokens before producing their final answer. |
| `INTENT_CLASSIFIER_TEMPERATURE` | `0.0` | Temperature for the intent classifier. `0.0` = fully deterministic. |
| `APPROVAL_HEARTBEAT_INTERVAL_SECONDS` | `60` | How often the approval gate refreshes TTL while blocking. |
| `PLAN_APPROVAL_HEARTBEAT_SECONDS` | `60` | How often the plan approval gate refreshes TTL while blocking. |
| `CLEANUP_RETRY_MAX_ATTEMPTS` | `3` | How many times a failed pass of the daily cleanup (old sessions, logs, processes, and so on) is retried before it's deferred to the next daily run and logged as an error. |
| `CLEANUP_RETRY_BASE_DELAY_SECONDS` | `60` | Delay before the first cleanup retry. Doubles each attempt. |
| `CLEANUP_RETRY_MAX_DELAY_SECONDS` | `300` (5 min) | Ceiling on the cleanup retry delay. |

---

> **Next:** [EVA vs Competitors](../16-appendix/01-eva-vs-competitors.md) — how EVA compares to Claude, ChatGPT, Cursor, and other products in the space.
