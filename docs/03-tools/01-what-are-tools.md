# What are Tools?

Tools are how EVA acts on the world. When you ask EVA to search the web, read a file, run a command, or call an API, it is not generating a description of those actions — it is actually executing them. Tools are the bridge between EVA's reasoning and real outcomes.

In a ReAct loop, EVA works like this: it reasons about what to do next, calls a tool, reads the result, then reasons again — repeating until the task is complete. Every tool call and its result are visible in the conversation stream. EVA never does hidden work.

---

## Authority levels

Every tool has an authority level that describes the consequence of running it. This level determines when EVA pauses for your approval before proceeding.

**Read-only** — inspection and retrieval only. Reading a file, searching the web, checking git status, recalling a memory. These never modify anything and run without prompting for approval in Auto mode.

**Mutating** — creates or changes state. Writing a file, running a shell command, making an HTTP POST, storing a memory. These pause for approval in Ask mode.

**Destructive** — irreversible operations. Deleting a file, deleting a memory entry, running `git push`. These are highlighted distinctly in the tool browser and follow the same approval rules as mutating tools, with extra visual weight given their permanence.

MCP tools sourced from external servers carry no authority classification — they show as **unclassified**, and their actual impact depends on what the MCP server exposes.

---

## Tool catalog

EVA ships a complete set of tools across eleven categories. All are available by default.

### Filesystem

Tools for reading and writing files within your workspace. Relative paths resolve into your active workspace; absolute paths require explicit approval if they point outside it.

| Tool | Authority | What it does |
|------|-----------|--------------|
| `read_file` | Read-only | Read the full contents of a file |
| `read_file_range` | Read-only | Read a specific line range from a file |
| `list_files` | Read-only | List files in a directory, with optional extension filter |
| `stat_file` | Read-only | Return metadata for a file or directory (size, type, timestamps) |
| `write_file` | Mutating | Write or overwrite a file |
| `edit_file` | Mutating | Replace the first occurrence of an exact string match in a file — a targeted change rather than rewriting the whole file |
| `delete_file` | Destructive | Delete a file or directory (`recursive=true` required for non-empty directories) |

### Search

Tools for finding things across your workspace without reading every file individually.

| Tool | Authority | What it does |
|------|-----------|--------------|
| `search_files` | Read-only | Recursive content search using a regex pattern |
| `find_files` | Read-only | Recursive filename search using a glob pattern |

Both are capped at a maximum result count — see [Configuration Reference](../15-configuration/01-configuration-reference.md#tool-execution) — and truncate with a "narrow your pattern" message rather than returning an unbounded result set.

Searches are also limited in how much *work* they do, not just how much they return: a time budget (10 seconds by default), a cap on files looked at, a skip on files over 2 MB, and only the start of extremely long lines. Common noise folders such as `.git` and `node_modules` are skipped when searching below a folder. If a limit stops a search, EVA gets back what it found along with a plain message that the result may be incomplete, and can retry with a narrower path. Searches run on their own worker, so a slow search over a large codebase never makes the rest of EVA wait.

### Shell

| Tool | Authority | What it does |
|------|-----------|--------------|
| `shell` | Mutating | Run a shell command with persistent session state |

The shell tool maintains a stateful session per conversation: `cd` into a directory once and subsequent calls stay there. Environment variables set with `export` persist across calls. All commands run through the Shell Safety system before executing — see [Approvals](../02-conversations/04-approvals.md) for a full description of what is blocked, warned, and allowed.

The shell tool waits for a command to finish, and stops it after a maximum of five minutes. Anything that is meant to keep running — a dev server, a file watcher — belongs in the Background work tools below instead.

Commands the shell tool, background processes and git run don't inherit your AI provider API keys: those are removed from their environment, so a command can't read or print them. See [Privacy & Security](../16-appendix/02-privacy-and-security.md#how-api-keys-are-stored).

### Background work

Tools for work that outlives a single step: processes that keep running, and pausing a task to resume later.

| Tool | Authority | What it does |
|------|-----------|--------------|
| `background_process` | Mutating | Start, list, tail, or stop a long-running process such as a dev server |
| `schedule_wakeup` | Mutating | Pause the current task and resume it automatically after a delay, or when a background task it is watching finishes |

**`background_process`** starts a command that keeps running on its own and returns immediately — `npm run dev`, `flask run`, `python -m http.server`, a `--watch` mode. EVA can then read its recent output (`tail`), see what is running (`list`), and shut it down (`stop`, which also stops anything the process started). Asking to start the same command in the same folder twice returns the copy already running rather than starting a second one. The process is *not* stopped when a task ends, pauses or is cancelled — EVA stops it when it is finished with it, unless you want to keep using it, such as a dev server you want to open. You can also see and stop running processes yourself from the Activity tab; see [Activity and the Preview pane](../02-conversations/07-activity-and-preview.md). Any process still running after several hours is stopped automatically. Starting a process goes through the same Shell Safety checks as the shell tool.

**`schedule_wakeup`** lets EVA pause instead of waiting. It gives a reason and either a delay ("check again in 20 minutes") or a background task to watch ("continue when the build daemon finishes"), or both — whichever comes first resumes it. While paused the task shows as *paused* in the Activity tab, and the pause survives EVA restarting. When the time comes, EVA continues the same conversation from where it left off. You can resume a paused task early or cancel it from the Activity tab. Cancelling a paused task does not stop the background task it was watching.

### Web search

Web search runs against DuckDuckGo and requires no API key.

| Tool | Authority | What it does |
|------|-----------|--------------|
| `web_search` | Read-only | Search the web. The `search_type` parameter selects the index: `text` (default), `news`, `images`, `videos`, or `books`. |

All search types share the same top-level parameters (`query`, `search_type`, `max_results`, `timelimit`). Type-specific filters (region, safesearch, image size, video resolution, etc.) can be passed through an optional `advanced_filters` dict.

### Memory

| Tool | Authority | What it does |
|------|-----------|--------------|
| `memory_recall` | Read-only | Search long-term memory for user facts or agent knowledge matching a query |
| `memory_store` | Mutating | Write a fact or knowledge entry to long-term memory |
| `memory_forget` | Destructive | Delete a specific memory entry by its ID |

When `memory_recall` is enabled, it runs in **Active Recall Mode** — EVA retrieves memory on demand during the conversation, rather than pre-loading all stored facts into the system prompt at the start of every execution.

### RAG (Document search)

| Tool | Authority | What it does |
|------|-----------|--------------|
| `rag_retrieve` | Read-only | Semantic search over documents indexed in your local knowledge base |
| `rag_ingest` | Mutating | Index new content — a file or raw text — into your local knowledge base |

`rag_retrieve` is called automatically when EVA reasons that a question is best answered by searching your indexed documents, rather than relying on what is already in context. See [Knowledge Base](../07-rag/01-knowledge-base.md) for how ingestion works.

### Project

Tools for reading and writing a project's `.eva` artifact files directly — the same files EVA already updates automatically after every project-linked turn. See [What are Projects?](../05-projects/01-what-are-projects.md) for what these files are.

| Tool | Authority | What it does |
|------|-----------|--------------|
| `project_artifact_read` | Read-only | Read one of a project's `.eva` files by name (e.g. `knowledge`, `tasks`) |
| `project_artifact_write` | Mutating | Append to, edit, or replace a section of a project's `.eva` file |

### Git

| Tool | Authority | What it does |
|------|-----------|--------------|
| `git_status` | Read-only | Show working-tree status |
| `git_diff` | Read-only | Show staged or unstaged changes |
| `git_log` | Read-only | Show commit history |
| `git_add` | Mutating | Stage files |
| `git_commit` | Mutating | Record staged changes as a new commit |
| `git_branch` | Mutating | List, create, or delete branches |
| `git_checkout` | Mutating | Switch branch or restore files |
| `git_push` | Destructive | Push commits to a remote repository |

Git tools are sandboxed to your workspace directory.

### HTTP

| Tool | Authority | What it does |
|------|-----------|--------------|
| `http_get` | Read-only | Make an HTTP GET request to an external URL |
| `http_post` | Mutating | Make an HTTP POST request with a JSON body |

Both tools block requests to localhost and private IP ranges to prevent unintended access to local services. Response bodies are capped at 10 MB; POST payloads at 1 MB. Default timeout is 30 seconds.

### Agent & daemon configuration

EVA can create, inspect, edit, and delete your subagents and daemons directly through conversation — for example, *"set up a daemon that checks my inbox every hour."* See [What are Subagents?](../08-subagents/01-what-are-subagents.md) and [What are Daemons?](../09-daemons/01-what-are-daemons.md) for the full walkthrough, including how EVA gathers requirements and confirms before saving anything.

| Tool | Authority | What it does |
|------|-----------|--------------|
| `create_subagent_config` | Mutating | Create a new subagent |
| `get_subagent_config` | Read-only | Read a subagent's current configuration |
| `edit_subagent_config` | Mutating | Update a subagent's configuration |
| `delete_subagent_config` | Destructive | Delete a subagent |
| `create_daemon_config` | Mutating | Create a new daemon |
| `get_daemon_config` | Read-only | Read a daemon's current configuration |
| `edit_daemon_config` | Mutating | Update a daemon's configuration |
| `delete_daemon_config` | Destructive | Delete a daemon |

---

## Agent delegation

`delegate_to_agent` is available in both chat mode and Plan-Execute-Verify mode. It hands a task off to a named or dynamic subagent and returns the result.

| Tool | Authority | What it does |
|------|-----------|--------------|
| `delegate_to_agent` | Read-only | Delegate a task to a named or dynamic subagent and return its result. With `background=true`, the subagent runs on its own and the call returns immediately; EVA can carry on, or pause with `schedule_wakeup` until the subagent finishes. |

The tools below are only available in Plan-Execute-Verify mode. They require an execution context, a shared blackboard, or a running orchestrator to operate meaningfully and are not accessible in a standard chat exchange.

**Multi-agent orchestration** — run and coordinate multiple subagents:

| Tool | Authority | What it does |
|------|-----------|--------------|
| `parallel_delegate` | Read-only | Run multiple subagents concurrently and collect all results |
| `coordinate` | Read-only | Orchestrate a structured multi-agent workflow across phases |

**Blackboard** — shared scratchpad for inter-agent coordination within a single execution:

| Tool | Authority | What it does |
|------|-----------|--------------|
| `blackboard_write` | Mutating | Write a value to a named slot in the execution blackboard |
| `blackboard_read` | Read-only | Read one or all slots from the execution blackboard |

**Inter-agent messaging** — structured messages between agents in the same execution:

| Tool | Authority | What it does |
|------|-----------|--------------|
| `send_agent_message` | Mutating | Send a message to another agent's inbox |
| `read_agent_messages` | Read-only | Read messages from the calling agent's own inbox |

**Daemon signalling** — trigger background daemon agents (PEV mode only):

| Tool | Authority | What it does |
|------|-----------|--------------|
| `trigger_daemon` | Mutating | Trigger a daemon agent. `wait=false` (default) returns immediately with the execution ID; `wait=true` blocks until the daemon completes or times out. |

> **PEV pre-seeded tools:** When a Plan-Execute-Verify execution starts, four tools are promoted immediately without requiring a `describe_tool()` call first: `coordinate`, `delegate_to_agent`, `parallel_delegate`, and `trigger_daemon`. All other tools (web search, shell, git, etc.) remain behind progressive disclosure, exactly as in chat mode.

---

## Tool sources

In addition to the built-in tool set above, EVA supports two additional sources:

**Custom tools** — tools you build and register yourself. Custom tools appear in the tool browser alongside default tools and can be individually enabled or disabled per session.

**MCP tools** — tools exposed by external MCP (Model Context Protocol) servers you have connected. Once a server is synced, its tools appear in the tool browser under the MCP group, named as `server_name__tool_name` (a double underscore). MCP tools carry no authority classification.

---

> **Next:** [Enabling and Disabling Tools](02-enabling-disabling-tools.md) — controlling which tools are available and at what authority level.
