# What are MCP Servers?

MCP stands for Model Context Protocol — an open standard for connecting AI agents to external tools, data sources, and services. An MCP server is a process that speaks this protocol. EVA connects to it, discovers what it can do, and makes those capabilities available to the agent alongside EVA's built-in tools.

In practice this means you can point EVA at an MCP server for your database, your file system, your GitHub account, or any custom service — and EVA gains the ability to query, read, and act on those systems without you having to build custom tool code inside EVA itself.

---

## What MCP servers expose

Every MCP server can advertise three types of capability. EVA discovers all three automatically when you connect a server.

**Tools** are callable functions — the same concept as EVA's built-in tools. A filesystem MCP might expose `read_file`, `write_file`, and `list_directory`. A database MCP might expose `run_query` and `describe_table`. EVA can call these the same way it calls its own tools.

**Resources** are read-only data sources addressed by URI. A resource might be a file, a database record, a configuration document, or an API response. Each resource has a name, a URI, and a MIME type. EVA can fetch a resource's content on demand. Resources appear in the expanded server view in the MCP panel under the Resources tab.

**Prompts** are reusable prompt templates with named parameters. A server can define a structured prompt — for example, a code review template that takes a `language` and `code` parameter — which EVA can render and inject into a conversation. Prompts appear under the Prompts tab in the expanded server view.

Not every server exposes all three types. Tools are the most common. Resources and prompts are optional capabilities — if a server doesn't support them, those tabs simply show empty.

---

## How EVA handles MCP connections

EVA keeps a **persistent, pooled connection** to each MCP server rather than opening a fresh one for every discovery pass or every tool call. This keeps repeated tool calls fast — no per-call connection setup — while still recovering automatically if a connection goes stale or a server restarts.

Two distinct things happen, on different schedules: keeping the tool catalogue current, and actually calling a tool.

---

### Tool discovery — cheap, on every execution

At the start of each agent execution — meaning each time you send a message — EVA reads the tool list for each of your enabled servers straight from its local database. No live connection is opened just to build this list, so the agent gets an up-to-date tool catalogue on every message without paying a connection cost each time.

That cached list is kept current by:

- **Enabling a server, or saving changes to an already-enabled server** — EVA connects immediately, re-syncs its tools, and updates the cache.
- **Clicking Sync** on a server's card — forces an immediate reconnect and tool re-discovery for that one server, without touching any others.

The agent itself sees MCP tools presented alongside EVA's built-in tools, namespaced as `server-name__tool-name` (a double underscore) — for example, a tool named `place_order` on an MCP server named `kite` appears to the agent as `kite__place_order`. The agent has no awareness of the transport or connection details.

---

### Tool execution — persistent, pooled connections

When the agent calls an MCP tool, EVA reuses an existing pooled connection to that server if one is already open — checked with a quick liveness ping first, since a connection can go stale while sitting idle. If there's no live connection, or the ping fails, EVA opens a fresh one and adds it to the pool. If a call itself fails because the connection broke, EVA discards that connection and retries once against a new one before returning an error. If the server is reachable and simply *refuses* the call — it rejects the arguments, or reports that the tool failed — EVA returns that answer to the agent as it is, without reconnecting or retrying, because trying the same call again would get the same refusal.

A pooled connection that sits idle for 15 minutes is closed automatically. Editing or deleting a server's configuration also closes its pooled connection immediately — the next call reconnects using the updated settings.

**If a server is confirmed unreachable** — meaning the reconnect-and-retry above also failed — its tools are removed from the agent's active toolset for the rest of that execution, and the agent is told explicitly not to keep retrying that server. This stops the agent from repeatedly hammering a dead connection mid-task. The server's cached tool list itself is left untouched, so a later message in the same conversation will try connecting to it again rather than treating one bad connection as permanent.

---

### What this means in practice

**EVA starts instantly regardless of how many MCP servers you have configured.** No connections are attempted at startup — the server boots, loads its config from the database, and is ready. Connections open lazily, the first time a tool from that server is actually called.

**A slow or unreachable MCP server can't stall EVA startup or unrelated conversations.** Tool discovery reads from the local cache, not a live connection, so an unreachable server never blocks the agent from starting a turn — it simply has no usable tools once EVA tries and fails to (re)connect to it.

**Connections cannot hang indefinitely.** Every connection has configured timeouts — for the transport handshake, the MCP initialisation, and individual tool calls. A timed-out connection is discarded and the error is returned to the agent as a retryable failure, with the automatic reconnect-and-retry described above.

---

EVA connects to MCP servers over three transports. The right choice depends on where the server runs and which MCP protocol version it implements.

**STDIO** launches a local process and communicates over its standard input/output streams. This is the most common transport for development and local tools — most MCP servers in the MCP ecosystem are designed for STDIO. EVA starts the process when needed and communicates through it directly.

> Use STDIO for servers you run locally: `npx @modelcontextprotocol/server-filesystem /home/user/projects`, custom Python or Node.js servers, anything you launch on the same machine as EVA.

**SSE** (Server-Sent Events) connects to a remote server using the legacy MCP SSE protocol (MCP spec ≤ November 2024). EVA connects to the `/sse` endpoint, receives an event pointing to a separate POST endpoint, and communicates through that pair. Many popular hosted MCP servers — including the official GitHub MCP server — use this transport.

> Use SSE for remote servers that predate the 2025 streamable HTTP spec. Typical URL format: `http://localhost:3001/sse` or a hosted endpoint.

**HTTP** connects using the newer Streamable HTTP protocol (MCP spec March 2025). EVA POSTs requests and receives SSE responses on the same connection. This is the modern transport for network-accessible MCP servers.

> Use HTTP for servers that implement the 2025 spec. Typical URL format: `http://localhost:3001/mcp`.

---

## Global vs project-scoped

An MCP server can be either **global** or **project-scoped**.

**Global servers** (the default) are available across all conversations regardless of which project is active. They appear in the MCP Servers panel. Add globally the tools you use in most of your work — a filesystem server, a general-purpose web search server, a code execution environment.

**Project-scoped servers** are tied to a specific project. They are available only within that project's conversations and are automatically paused when the project is paused or archived. They do not appear in the global MCP Servers panel — they are managed from the project browser under the **MCP** tab. Use project scope for servers that are specific to one codebase, client, or environment — a database MCP for a particular app, an internal API server for a specific team's services.

If the project is paused, project-scoped servers become unavailable until the project is resumed. If the project is archived, they remain suspended permanently.

---

## Adding a server

Open the **MCP Servers** panel from the sidebar. Click **Add Server**.

Fill in the form:

**Server Name** — a label for this connection. Shown in the server list and in health reports, and used as the prefix for every tool the server provides. Any name you recognise, with one restriction: it cannot contain a double underscore (`__`), which EVA reserves as the separator between the server name and the tool name. The form flags this as you type and won't let you save.

**Transport** — select STDIO, SSE, or HTTP based on where and how the server runs (see above).

For **STDIO**:
- **Command** — the executable to launch. Examples: `npx`, `python`, `/usr/local/bin/my-mcp-server`.
- **Arguments** — comma-separated arguments passed to the command. Example: `@modelcontextprotocol/server-filesystem, /home/user/projects` runs `npx @modelcontextprotocol/server-filesystem /home/user/projects`.

For **SSE** and **HTTP**:
- **URL** — the endpoint to connect to. SSE: typically ends in `/sse`. HTTP: typically ends in `/mcp`.

**Project** (optional) — leave blank for a global server. Select a project to scope this server to it.

**Enable on save** — toggle off if you want to save the config but not connect yet. Disabled servers are not contacted and their tools are not available to agents.

Click **Add Server**. EVA connects immediately, runs a tool sync, and the server appears in the list with a health status dot.

---

## Credentials and secrets

MCP servers often need an API token or password, passed as an environment variable (STDIO) or a request header (SSE and HTTP). EVA never keeps those values in its database or shows them back to you.

When you save a server, EVA looks at the names of its environment variables and headers. Any whose name suggests a secret — containing words like `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `AUTH`, `COOKIE`, `CREDENTIAL`, or `SESSION` — has its value moved into your operating system's secure credential store (Windows Credential Manager on Windows), the same place your provider API keys live. In their place the server's settings show a masked placeholder (`••••••••`). The real value is looked up only at the moment EVA connects.

- **Editing a server** — leave the masked placeholder as it is to keep the stored value. Type a new value to replace it. Remove the entry to delete it from the credential store.
- **Deleting a server** — its stored credentials are deleted with it.
- **Servers you added earlier** — any secret values that were saved in plain text before this protection existed are moved to the credential store automatically the next time EVA starts.

There is nothing extra to configure — paste or enter the server's settings as usual and the protection applies on its own.

---

## Default MCP: browser automation

EVA ships one ready-made MCP server: **Playwright**, which gives the agent a real web browser. With it, EVA can open a page it just built, click through it, read the browser console for errors, look at failed network requests and take screenshots — then fix what it finds and check again. It is what lets EVA build, run, look at and correct a web app on its own.

**It starts switched off.** Every new account gets a Playwright entry in the MCP Servers panel, disabled. Turning it on adds around 25 browser tools to every conversation, which costs some context, so it is opt-in — toggle it on when you want EVA to work with a browser. Existing accounts that don't have the entry yet can click **Restore default MCPs** in the panel header. It only adds what is missing and never overwrites anything you have changed.

**What to expect:**
- **Requirements** — Node.js must be installed (EVA runs the server through `npx`). On Windows it uses Microsoft Edge, which is on every install; elsewhere it uses Chrome. If Node is missing, *Restore default MCPs* tells you.
- **First use is slower** — the first time you enable it, the server may take up to a minute to download and start. If the toggle appears to hang, wait — the panel corrects itself once EVA finishes.
- **Private by default** — the browser runs in an isolated, in-memory profile. It never touches your real browser's cookies, history or logins.
- **You can watch it** — with the desktop app, the browser appears inside EVA's **Preview pane** so you can follow along without switching windows. See [Activity and the Preview pane](../02-conversations/07-activity-and-preview.md). Without the pane it opens as a separate window.
- **Screenshots** — saved into your EVA workspace, not the app directory. On a model that supports vision they can be shown to the model as images; otherwise EVA works from the page's text structure, console and network output instead.

---

## Health and status

EVA checks the health of all configured servers automatically every 30 seconds. Each server card shows a status dot:

| Status | Meaning |
|--------|---------|
| Green — healthy | Connected. Shows round-trip latency and tool count. |
| Red — error | Connection failed. Hover the dot for the error message. |
| Disabled | Server is toggled off. No connection attempted. |
| Amber — project paused | The server's project is paused. Server is unavailable until the project is resumed. |

Click the **Refresh** button in the panel header to trigger an immediate health check and reload the server list. This does not trigger a tool sync — it updates health status only.

---

## Browsing tools, resources, and prompts

Click the **chevron (›)** on the right side of any server card to expand it. The expanded view shows the server's connection details (command, arguments, or URL, and the date the server was added) followed by three tabs:

**Tools** (default) — lists every tool the server exposes. Each entry shows the tool name in monospace and its description. The list is scrollable if there are many tools. If no tools have been synced yet, the tab shows "No tools exposed by this server. Try syncing first."

**Resources** — lists every resource the server exposes. Each entry shows the resource name, its URI, an optional description, and the MIME type. Resources are fetched live from the server when you open the tab.

**Prompts** — lists every prompt template the server exposes. Each entry shows the prompt name and description. Prompts are fetched live when you open the tab.

If a server doesn't support a given type, the corresponding tab shows an empty state message.

---

## Enabling and disabling

Each server has an enable/disable toggle on its card (the toggle icon on the right side of the row). Toggling a server off disconnects it immediately — its tools, resources, and prompts are no longer available to agents. The server config is preserved; re-enabling reconnects and re-syncs tools automatically.

Disabled servers are shown at reduced opacity in the list and reported as "Disabled" in health checks.

If a server can't start or its tools can't be fetched when you enable it, the reason appears in red under the card — for example *"The system cannot find the file specified — use Sync to retry."* The toggle stays on so you can fix the cause (a missing program, a wrong path) and click **Sync** to try again. Clicking **Sync** on a server that is still failing shows the error the same way rather than leaving the card unchanged.

---

## Tool sync

EVA syncs a server's tool list — refreshing the names, descriptions, and input schemas cached in its local database — in three situations. Every message reads from this cache rather than triggering a sync itself; see [How EVA handles MCP connections](#how-eva-handles-mcp-connections) above for why.

**Automatically on enable or save.** When you enable a server or save changes to an enabled server, EVA immediately connects, syncs its tools into the cache, and disconnects.

**Manually per server.** Each server card has a **Sync** button (the refresh icon in the card's action row). Clicking it forces an immediate reconnect and tool re-discovery for that specific server — without affecting any other servers. The button spins while the sync is in progress and is disabled if the server is turned off or its project is paused.

**Resources and prompts on demand.** When you open the Resources or Prompts tabs in the expanded server view, EVA fetches the current list live from the server. This sync covers resources and prompts only, not tools.

---

## Troubleshooting

**Server shows red immediately after adding** — the connection failed. Expand the server and check the error message on the health dot. Common causes: the process command is wrong (STDIO), the URL is unreachable (SSE/HTTP), the server isn't running, or a firewall is blocking the port.

**STDIO server connects but shows 0 tools** — the process started but the MCP handshake timed out. Some servers (particularly proxy-style STDIO servers) need a first real message before they wake up. This is handled gracefully — the server is still usable; tools will appear once the agent makes its first call.

**SSE server unreachable even though the URL looks correct** — confirm the server is running the legacy SSE protocol (MCP spec ≤ November 2024), not the newer Streamable HTTP. If it's the newer spec, switch the transport to HTTP.

**Project-scoped server not responding** — check the project status in the project browser. If the project is paused or archived, all its MCP servers are suspended. Resume the project to restore them — connections re-establish automatically.

**Playwright won't enable, or shows an error under the card** — Playwright runs through Node.js, so `npx` must be installed and on your path. After installing Node, click **Sync** on the Playwright card to retry. If the entry is missing entirely, click **Restore default MCPs**.

**Tools not updating after server restart** — click **Sync** on the server's card, or disable and re-enable it, to force an immediate reconnect and tool re-discovery. Sending a message alone won't pick up the change — execution reads the existing cached tool list rather than reconnecting to the server itself.

---

> **Next:** [Configuring Providers](../13-providers/01-configuring-providers.md) — connecting EVA to AI providers and managing API keys.
