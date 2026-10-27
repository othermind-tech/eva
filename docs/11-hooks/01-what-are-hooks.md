# What are Hooks?

Hooks are interception points that run between EVA and its tools. Every time EVA is about to call a tool, send a message, or start a session, hooks fire first — and can allow, block, warn, or quietly mutate what happens next.

They exist at the boundary between EVA's decisions and the outside world. If you want to audit every shell command EVA runs, stop it from writing outside a specific directory, or push every LLM call to an external log, hooks are the mechanism for that.

---

## The hook pipeline

When EVA calls a tool, the execution path looks like this:

```
User prompt
    │
    ▼
[ UserPromptSubmit hooks ] ← blocking; can deny prompt before EVA even sees it
    │
EVA thinks → decides to call shell("rm old-logs/")
    │
    ▼
[ PreToolUse hooks ] ← blocking; can allow / block / rewrite arguments
    │
Tool executes
    │
    ▼
[ PostToolUse hooks ] ← non-blocking by default; can mutate the result
    │
EVA sees result and continues
```

Lifecycle events (`SessionStart`, `Stop`, `ModelCall`) fire outside the tool loop and are always non-blocking — they cannot halt execution.

---

## Events

EVA fires hooks on six events:

| Event | When it fires | Blocking? | Can modify? |
|-------|--------------|-----------|-------------|
| `pre` | Before a tool executes | Yes | Rewrites tool arguments via `updated_input` |
| `post` | After a tool executes (success) | No (default) | Rewrites tool result |
| `prompt_submit` | When user sends a message | Yes | Cannot modify prompt — allow/block only |
| `session_start` | When a session initialises | No | Observation only |
| `stop` | When an execution finishes | No | Observation only |
| `model_call` | Before each LLM API call (EVA extension) | No | Observation only |

**Blocking** means EVA waits for the hook to complete before proceeding. A block decision from a `pre` hook prevents the tool from running entirely. A block from `prompt_submit` prevents EVA from processing the message at all.

**Non-blocking post hooks** can be made fire-and-forget by setting `async: true` in the hook definition. Blocking events (`pre`, `prompt_submit`) always wait regardless of the `async` setting.

---

## Hook types

EVA supports three hook implementations. Each type has a different trade-off between ease of setup, flexibility, and where the logic lives.

---

## Type 1 — Built-in

Python handlers that ship with EVA and run inside the server process — no external process, no file to upload, no language runtime to worry about. They cover the most common interception needs out of the box: auditing, blocking, path restriction, secret scrubbing, and rate limiting. Select a handler from the list in the hook editor, fill in any config fields it needs, and it fires on the next tool call. Built-in handlers are also the fastest option — they run synchronously in-process with no subprocess or network overhead.

Five handlers are available:

**`audit_log`** — pre and post

Records every tool call and its result to the server's structured log. Nothing is blocked or modified — this handler is pure observation.

Pre-event: logs the tool name, which argument keys were passed, and the execution ID. Post-event: logs the tool name and the first 200 characters of the result.

This gives you a timestamped, structured trace of everything EVA touched during a session. It's the right starting point any time you need accountability — security audits, debugging unexpected behaviour, or just knowing what happened.

> **Example:** add an `audit_log` hook on `pre` with tool `*`, and another on `post` with tool `*`. Every tool call in every session will appear in the server log under the tag `[HOOK:audit_log]`.

---

**`deny_always`** — pre only

Blocks a tool call unconditionally before it runs. EVA receives a denial message instead of a result and is forced to work around it or stop. The tool is still enabled and visible to the agent — the hook intercepts it at runtime rather than removing it from the tool set entirely.

Config: `reason` (optional string) — the message shown to EVA when the tool is blocked. Defaults to `"Tool '{name}' is blocked by a deny_always hook policy."` if not set.

> **Example:** you want to allow EVA to use `web_search` in general but disable it for sessions where you need fully offline, reproducible answers. Add a `deny_always` hook on `pre` targeting `web_search`, with `reason` set to `"Web search is disabled — use only local knowledge."` EVA will attempt the search, receive the denial, and respond using only what it already knows.

---

**`path_guard`** — pre only

Blocks any tool call whose arguments contain a path that falls outside a configured list of allowed directories. This prevents EVA from reading, writing, or executing anything outside your project workspace, even if the agent is told to do so.

It inspects every string argument of the tool call. If any value looks like an absolute path (starts with `/`, `~`, or a Windows drive letter like `C:\`) and that path does not begin with one of your allowed prefixes, the call is blocked. `~` is expanded to the real home directory before checking.

Config: `allowed_prefixes` (list of strings) — absolute path prefixes that are permitted. Any path not matching at least one prefix is blocked.

> **Example:** you're running EVA against a client project at `/home/user/client-alpha` and want to make sure it never touches anything in `/home/user/personal` or system directories. Set allowed prefixes to `["/home/user/client-alpha", "/tmp/eva-scratch"]`. If EVA tries to read `/home/user/personal/config.json`, the hook blocks it immediately with a message explaining which prefixes are allowed.

---

**`redact_env_vars`** — post only

Scans the string result returned by a tool and replaces any environment-variable-like value with `***` before EVA sees it. The pattern it looks for is `UPPERCASE_NAME=value` — variable names of three or more uppercase letters or digits, an equals sign, then the value up to the next whitespace or newline.

This only operates on plain string results. Structured JSON objects and lists are passed through untouched to avoid breaking tool outputs that use similar patterns legitimately. No config required.

> **Example:** EVA runs `shell` with the command `env` to inspect the environment. The result contains `DATABASE_URL=postgres://user:s3cret@localhost/db` and `AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE`. With `redact_env_vars` on `post` for the `shell` tool, EVA instead receives `DATABASE_URL=***` and `AWS_SECRET_ACCESS_KEY=***` — the secrets never enter EVA's context.

---

**`rate_limit`** — pre only *(coming soon)*

Limits how many times a tool can be called within a rolling time window. If EVA exceeds the limit, the call is blocked and EVA is told how many seconds to wait before retrying. Limits are per-user and per-tool, so a `rate_limit` on `web_search` applies independently to each user. The counters are held in memory and reset when the server restarts.

Config:
- `window_seconds` (int, default `60`) — the rolling window length in seconds
- `max_calls` (int, default `10`) — maximum calls allowed within that window

> **Example:** EVA is doing a research task and keeps spinning up web searches faster than you'd like. Set `rate_limit` on `web_search` with `window_seconds: 60` and `max_calls: 5`. After five searches in any 60-second window, EVA receives a denial message telling it to wait. It will either pause and retry or proceed with the information it already has.

---

## Type 2 — Script

An executable file you upload to EVA's Script Library. EVA runs the script as a subprocess, writes a JSON payload to its stdin, and reads the decision from its stdout (or exit code).

Any executable is valid: shell script, Python, Node.js, compiled binary. The only requirement is the stdin/stdout contract described below.

---

## Type 3 — HTTP

EVA `POST`s the JSON payload to a URL you control. The endpoint returns the same JSON decision structure as a script. Use this to connect hooks to external systems — audit databases, policy engines, webhook relay services — without uploading any code to EVA.

---

## The stdin/stdout contract

For script and HTTP hooks, the protocol is the same in both directions.

### What EVA sends (stdin / POST body)

A JSON object with these fields:

```json
{
  "hook_event_name": "PreToolUse",
  "session_id": "conv_abc123",
  "user_id": "user_xyz",
  "cwd": "/home/user/my-project",

  // PreToolUse / PostToolUse only:
  "tool_name": "shell",
  "tool_input": { "command": "rm -rf /tmp/old" },
  "tool_use_id": "tu_abc",

  // PostToolUse only:
  "tool_response": { "result": "..." },

  // UserPromptSubmit only:
  "prompt": "Delete the old log files",

  // ModelCall only (EVA extension):
  "model": "claude-sonnet-4-6",
  "provider": "anthropic",
  "turn_number": 3,
  "token_estimate": 4200
}
```

Fields not relevant to the current event are omitted. The field names match Claude Code's hook schema — scripts written for Claude Code hooks work here without modification.

### What EVA reads (stdout / response body)

A JSON object:

```json
{
  "decision": "allow",
  "reason": "",
  "updated_input": { "command": "rm -rf /tmp/old-safe" },
  "additional_context": "Path was sanitised by hook."
}
```

| Field | Values | Effect |
|-------|--------|--------|
| `decision` | `"allow"`, `"block"`, `"warn"` | Allow continues; block halts; warn continues but logs |
| `reason` | string | Shown to the user when blocked or warned |
| `updated_input` | object | Replaces the tool's arguments (`pre` hooks only) |
| `additional_context` | string | Injected into EVA's next context block |

### Exit code shorthand

For simple scripts that don't need to return JSON, exit code alone is enough:

```
exit 0   → allow
exit 2   → block (read stderr as reason)
exit N   → allow (logged as non-standard exit)
```

This is the same contract Claude Code uses. A minimal block script is just:

```bash
#!/bin/bash
# Block all shell calls unconditionally
echo "Shell is disabled by policy" >&2
exit 2
```

---

## Configuring hooks in EVA

Hooks are configured per-session in the **Hooks** panel (accessible from the sidebar). The panel has two tabs.

### Tool Hooks tab

This is the list of active hook definitions. Each entry specifies:

- **Event** — which event triggers this hook
- **Tool** — which tool to intercept (`*` for all tools, or an exact name like `shell`)
- **Type** — built-in, script, or HTTP
- **Handler / Script / URL** — what runs

Click **New Hook** to open the hook editor:

1. Select the **Event** from the dropdown.
2. For `pre` and `post` events, set the **Tool name** — use `*` to match all tools, or enter a specific tool name (`shell`, `file_read`, `web_search`, etc.).
3. Select the **Type** (Built-in / Script / HTTP).
4. Configure the handler:
   - **Built-in**: choose a handler. `path_guard` shows a path list field. `deny_always` shows an optional reason.
   - **Script**: select an uploaded script from the dropdown. Set a timeout (default 30s). For non-blocking events, toggle **Async** on for fire-and-forget execution.
   - **HTTP**: enter the URL, optional headers (one `Key: Value` per line), and timeout.
5. Click **Add Hook**.

Changes auto-save 800 ms after any modification.

Hooks run in the order listed. For `pre` events, the first block decision short-circuits — remaining hooks are skipped. Warnings from multiple hooks accumulate and all run.

### Script Library tab

Manages the executable files available for script hooks. Scripts are stored at:

```
~/eva_workspace/{user_id}/hooks/{script_name}
```

EVA automatically marks every uploaded file executable (`chmod +x`). An advisory safety scan runs at upload — dangerous patterns are logged as warnings but never blocked.

**Uploading a script:**

1. Click **Upload Script**.
2. Enter a **name** — this is what appears in the hook editor's script dropdown and what goes in `HookDefinition.command`. Avoid spaces and special characters (/ \\ : * ? " < > | are disallowed).
3. Add an optional description.
4. Choose the file and click **Upload**.

**Updating a script:** click the edit icon on any script card and upload a replacement file. The name stays the same; the content is replaced.

**Viewing a script:** click the eye icon to view the raw content. A Download button in the viewer saves a local copy.

---

## Hook evaluation order

When multiple hooks match an event, they run sequentially in the order they appear in the list:

1. First hook runs; result processed
2. If `updated_input` was returned, the next hook receives the *modified* input (not the original)
3. If any hook blocks, remaining hooks are skipped and the block reason is returned immediately
4. Warnings accumulate — all hooks run even if one warns

For `post` hooks, `updated_input` carries the mutated tool result. The final result passed back to EVA is the output of the last hook that returned `updated_input`, or the original if none did.

---

## Common patterns

**Audit everything**

Add a `pre` built-in hook with handler `audit_log` and tool `*`. Every tool call is logged to server output.

**Restrict file access to a project directory**

Add a `pre` built-in hook with handler `path_guard`, tool `*`, and allowed prefixes set to `/home/user/my-project` and `/tmp/eva-safe`. Any tool call targeting a path outside those prefixes is blocked.

**Block a specific tool entirely**

Add a `pre` built-in hook with handler `deny_always` and tool `shell`. All shell calls are rejected with your custom message.

**Custom approval logic via script**

Write a script that reads `tool_name` and `tool_input` from stdin and exits `2` for any command that matches a deny list. Upload it to the Script Library, then add a `pre` hook pointing to it with tool `*`.

**Send every LLM call to an audit endpoint**

Add an HTTP hook on the `model_call` event. EVA will POST metadata for every LLM turn to your endpoint. This is an EVA-specific event with no Claude Code equivalent.

---

## Troubleshooting

**Hook script not found** — the script name in the hook definition must exactly match the name you gave at upload. The name is case-sensitive. Check the Script Library tab to confirm the name.

**Script exits 2 but hook allows** — check that stdout is empty if you're relying on exit code alone. If the script writes any text to stdout that happens to be valid JSON with `"decision": "allow"`, the JSON takes precedence over the exit code.

**Pre hook blocks unexpectedly** — hooks run in order. If an early hook blocks, later ones don't run. Temporarily reorder your hooks to isolate which one is triggering.

**Async hook not running** — `async` is only available for non-blocking events (`post`, `session_start`, `stop`, `model_call`). The `async` toggle is disabled in the UI for `pre` and `prompt_submit` hooks, which are always blocking.

**HTTP hook errors treated as allow** — EVA never blocks execution due to a failing HTTP hook. If the endpoint returns HTTP 4xx/5xx or is unreachable, the hook is treated as `allow` and a warning is logged.

---

> **Next:** [What are MCP Servers?](../12-mcp/01-what-are-mcp-servers.md) — connecting EVA to external tools and data sources via the Model Context Protocol.
