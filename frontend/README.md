# EVA Frontend — NEXUS UI (Redesign)

> **Location:** `frontend/` in the repository root
> **Last updated:** 2026-09-09
> **Stack:** Vite 7 + React 18 + TypeScript + Radix UI + Zustand + TanStack Query v5 + Axios + Framer Motion
> **Backend:** EVA FastAPI (port 8000) — `backend/`

---

## Quick Start

Requires **Node.js 22+** and **npm 10+** (declared in `package.json` `engines`; npm warns on older versions). Install with `npm ci` — it installs exactly what `package-lock.json` pins.

```bash
cd frontend
npm ci
npm run dev
```

Dev server runs at `http://localhost:5173`. All `/api` requests are proxied to `http://127.0.0.1:8000` (prefix stripped).

---

## Architecture Overview

```
src/
├── api/                    # One module per domain — all use Axios client
│   ├── client.ts           # Axios instance, JWT Bearer interceptor, 401 → refresh → retry
│   ├── auth.ts             # login, logout, refresh, sse-token
│   ├── chat.ts             # POST /chat
│   ├── conversations.ts    # CRUD, export (json/md/txt)
│   ├── tools.ts            # tools, providersApi.list(), live models, costs
│   ├── secrets.ts          # API key upsert (write-only), Ollama base-url
│   ├── memory.ts           # user facts, agent scopes, grant-access
│   ├── rag.ts              # file upload, listing, ingest job polling
│   ├── executions.ts       # status (+DB fallback), running list, metrics
│   ├── agents.ts           # agent CRUD
│   ├── daemons.ts          # daemon CRUD, trigger, lifecycle, webhooks
│   ├── projects.ts         # project CRUD, artifacts, files, sub-resources
│   ├── skills.ts           # skill CRUD
│   ├── mcp.ts              # MCP server CRUD, sync, tools/resources/prompts
│   ├── prompts.ts          # POST /prompts/enhance
│   ├── hooks.ts            # tool hook configs
│   ├── events.ts           # GET /events/stream (daemon + execution SSE)
│   ├── attachments.ts      # file attachments on messages
│   ├── metrics.ts          # usage summary + time-range aggregate
│   ├── session.ts          # session config read/write (app startup hydration)
│   ├── wakeups.ts          # P-19: scheduled/background execution wakeups
│   └── health.ts
│
├── store/                  # Zustand stores
│   ├── authStore.ts        # JWT + refresh token, proactive 5-min-before-expiry refresh timer
│   ├── chatStore.ts        # Active conversation, SSE block state, viewMode routing, approval gate
│   ├── sessionStore.ts     # Provider/model/tools/hooks/flags — persisted to localStorage
│   ├── uiStore.ts          # Transient UI state (panel open states, active tabs)
│   ├── notificationStore.ts# In-memory event log, unread count
│   └── updateStore.ts      # Electron auto-updater status (idle/downloading/ready)
│
├── hooks/
│   ├── useSSE.ts             # Fetch-based SSE reader (see SSE section below)
│   ├── useExecutionReplay.ts # On-demand execution replay stream
│   ├── useEventStream.ts     # Always-on daemon/execution event feed
│   ├── useCloseGuard.ts      # window surface for build/main.js to check before quitting
│   ├── useProjectSync.ts     # Polls project sync execution status
│   ├── useSessionAutoSave.ts # Single source of truth persisting sessionStore.config to backend
│   └── useUpdateEvents.ts    # Wires Electron updater events into updateStore/chatStore
│
├── components/
│   ├── layout/
│   │   ├── AppShell.tsx    # Root layout: NavRail + WorkspacePanel + ChatPage + side panels + UserProfileModal
│   │   │                   # Exports WorkspaceCtx + useOpenWorkspace() hook for descendants
│   │   ├── NavRail.tsx     # 56px left rail — EVA logo (welcome toggle), nav icons, avatar, bell
│   │   ├── WorkspacePanel.tsx  # Root: 360px left panel, Chat/Projects tab switcher + UserPill (~80 lines)
│   │   ├── FilterMenu.tsx      # Shared status/activity/sort filter dropdown — used by ChatTab + ProjectsTab
│   │   ├── ChatTab.tsx         # Conversations list — search, filter, date-grouped recents
│   │   ├── ProjectsTab.tsx     # Project tree — search, filter, expandable per-project child nav
│   │   ├── UserPill.tsx        # Bottom-of-panel user chip — opens UserProfileModal
│   │   └── TitleBar.tsx    # Custom frameless-window titlebar (Electron min/max/close)
│   │
│   ├── docs/
│   │   └── DocsPanel.tsx   # In-app documentation viewer (sidebar + article + on-page TOC)
│   │
│   ├── welcome/
│   │   ├── WelcomePanel.tsx    # Full-screen About EVA page — first login + logo click
│   │   └── WhatsNewModal.tsx   # Post-update changelog modal
│   │
│   ├── chat/
│   │   ├── ChatView.tsx        # Root state machine only (~330 lines) — scroll/pagination, SSE + execution-replay
│   │   │                       # wiring, send/edit/retry, routes viewMode to the feature-folder panels below
│   │   ├── ChatViewModes.ts    # Shared PANEL_VIEW_MODES set + isPanelViewMode() — used by ChatView's 3 view-mode checks
│   │   ├── ProjectConvView.tsx # Project-scoped conversation viewport — reads the same chatStore.messages/
│   │   │                       # activeConversationId slot as the main view, not an independent copy
│   │   ├── ChatHeader.tsx      # Conversation title, model badge, execution status
│   │   ├── MessageList.tsx     # Message thread — NOT virtualized (FE-7.1 audit finding; approach still
│   │   │                       # undecided, see FE-7.3 in the backend audit doc)
│   │   ├── MessageItem.tsx     # Renders all block types; parses [SENSITIVE:] placeholders
│   │   ├── MessageInput.tsx    # Textarea, send, attach, modes toggle
│   │   ├── ToolCallCard.tsx    # Inline tool call block — spinner → tick/X on resolve
│   │   ├── ReActStepCard.tsx   # ReAct loop step display
│   │   ├── ReasoningBlock.tsx  # Collapsible reasoning (chain-of-thought) block
│   │   ├── SubagentActivityCard.tsx # Subagent delegation block
│   │   ├── SensitiveMemoryCard.tsx  # Inline grant-access UI for [SENSITIVE:] placeholders
│   │   ├── ExecutionBadge.tsx  # Execution ID chip
│   │   ├── ExecutionStatusBar.tsx   # Live execution step progress
│   │   ├── ModesButton.tsx     # Quick-access agent/skill/approval mode toggles
│   │   ├── EmptyState.tsx      # New conversation landing with suggestion chips
│   │   ├── CoordinatorPhaseCard.tsx # Multi-agent coordinator phase/result display
│   │   ├── PlanTraceCard.tsx   # Plan-mode step trace (pending/running/done/skipped)
│   │   └── InlineDisclosure.tsx # Shared collapsible summary/detail primitive
│   │
│   ├── agents/              # Subagent browse/list/edit views — viewMode 'agent'/'agents'
│   │   ├── AgentBrowseCard.tsx    # Browse-idle state (no agent selected)
│   │   ├── AgentProfileCard.tsx   # Selected-agent idle state (no messages yet)
│   │   ├── AgentsMainView.tsx     # 2-level router: list vs. edit form
│   │   ├── AgentListCard.tsx      # Single agent row — also reused by projects/ProjectAgentsTab
│   │   ├── AgentsListView.tsx     # Full list with default/custom filter
│   │   └── AgentEditView.tsx      # Create/edit form (~770 lines)
│   │
│   ├── daemons/              # Daemon browse/list/edit views — viewMode 'daemon'/'daemons'
│   │   ├── DaemonBrowseCard.tsx   # Browse-idle state
│   │   ├── DaemonNoRunsCard.tsx   # Selected daemon, no runs yet
│   │   ├── DaemonLifecycleBadge.tsx
│   │   ├── DaemonWebhookSection.tsx
│   │   ├── DaemonRunHistoryTab.tsx
│   │   ├── DaemonListCard.tsx     # Single daemon row — also reused by projects/ProjectDaemonsTab
│   │   ├── DaemonsListView.tsx
│   │   ├── DaemonsMainView.tsx    # 2-level router: list vs. edit form
│   │   ├── DaemonEditView.tsx     # Create/edit form (~880 lines — largest component in the app;
│   │   │                          # also defines the colocated DaemonToggle helper)
│   │   └── daemonFormat.ts        # formatInterval/formatRelativeTime/formatDurationMs/daemonRunStatusConfig
│   │
│   ├── skills/               # Skill browse/edit views — viewMode 'skills'
│   │   ├── SkillsMainView.tsx     # 3-level router: list / folder / file editor
│   │   ├── SkillsListView.tsx
│   │   ├── SkillCreateForm.tsx
│   │   ├── SkillFileTreeNode.tsx  # Recursive file tree (self-referencing)
│   │   ├── SkillFolderView.tsx
│   │   ├── SkillFileEditor.tsx
│   │   └── skillHelpers.tsx       # fileIcon, setFrontmatterTools
│   │
│   ├── projects/             # Project workspace — viewMode 'projects'
│   │   ├── ProjectCreateView.tsx
│   │   ├── ProjectsBrowseCard.tsx # Root router: create form / no-selection card / overview+tabs
│   │   ├── ProjectOverviewBody.tsx
│   │   ├── ProjectArtifactTab.tsx # Tasks/Errors/Policies raw-.eva-file viewer
│   │   ├── ProjectAgentsTab.tsx
│   │   ├── ProjectDaemonsTab.tsx
│   │   ├── ProjectMCPTab.tsx
│   │   ├── ProjectConvList.tsx
│   │   ├── ProjectKnowledgeTab.tsx
│   │   ├── ProjectFilesTab.tsx
│   │   ├── DonutChart.tsx         # DonutTip / DonutChart / ConcentricDonut — cost/token/execution rings
│   │   ├── ExecutionFooter.tsx    # Metrics summary shown below a completed execution
│   │   └── projectFormat.ts       # Formatters + file-tree helpers (buildTree, FileTreeNode, FileIcon, ...)
│   │
│   ├── memory/               # viewMode 'memory'
│   │   ├── MemoryBrowseCard.tsx   # Top-level router
│   │   ├── UserFactsView.tsx
│   │   └── AgentMemoryView.tsx
│   │
│   ├── rag/                  # viewMode 'rag'
│   │   └── RAGBrowseCard.tsx      # Knowledge-base browse/upload/delete
│   │
│   ├── profile/              # UserProfileModal cluster — rendered at AppShell root, not inside WorkspacePanel
│   │   ├── UserProfileModal.tsx      # Named export, imported directly by AppShell.tsx
│   │   ├── ProvidersTabContent.tsx
│   │   ├── AccountTabContent.tsx
│   │   ├── ChangePasswordModal.tsx
│   │   ├── LanguageCombobox.tsx
│   │   ├── PrefFallbackRow.tsx
│   │   ├── PrefFallbackChainEditor.tsx
│   │   └── profileConstants.ts       # TZ_LIST, LANGUAGE_LIST, PROFILE_INPUT, profileFocus/profileBlur
│   │
│   ├── panels/
│   │   ├── SessionPanel.tsx    # Provider, model, tools, agent mode, skill mode, hooks — auto-save
│   │   ├── MCPPanel.tsx        # MCP server list (cloud-scoped), edit form, ServerDetail expand
│   │   ├── AnalyticsPanel.tsx  # Token usage, cost breakdown, provider charts
│   │   └── HooksPanel.tsx      # Tool hook configuration
│   │
│   ├── approval/
│   │   └── ApprovalGate.tsx    # Tool approval + plan approval modal overlay
│   │
│   ├── notifications/
│   │   ├── NotificationDrawer.tsx  # Daemon + execution event feed
│   │   └── ActivityTab.tsx     # Persistent "what's running/paused now" view (wakeups + running executions)
│   │
│   ├── common/
│   │   ├── ProviderRequiredModal.tsx # Shown when sending/enhancing without a configured provider
│   │   ├── StepLimitBanner.tsx # Inline banner + extend-limit action when a run hits max steps
│   │   └── WorkerDownBanner.tsx # Backend worker-unreachable banner
│   │
│   └── ui/
│       ├── SearchableSelect.tsx
│       ├── SystemPromptField.tsx
│       ├── ReasoningControl.tsx # Reasoning-effort level selector (provider-capability-aware)
│       └── ErrorBoundary.tsx
│
├── pages/
│   ├── AuthPage.tsx        # Login + signup, animated EVA card
│   └── ChatPage.tsx        # Authenticated root
│
├── lib/
│   ├── constants.ts        # SSE_ENDPOINT, SSE_TIMEOUT_MS, etc.
│   ├── utils.ts            # cn(), date helpers
│   ├── memoryCategories.ts
│   ├── ragFileTypes.ts
│   └── docsIndex.ts        # DocPage/DocSection types + useDocsIndex() — fetches /docs/manifest.json (no hardcoded nav tree)
│
├── types/
│   └── api.ts              # All TypeScript interfaces matching backend Pydantic models
│
public/
├── eva-icon.svg            # Cat-in-gradient-circle icon (transparent) — NavRail, EmptyState, SVG favicon
├── eva-main.svg            # Cat + EVA wordmark with underline (transparent) — WelcomePanel hero
├── eva-main-no-underline.svg  # Same without the underline, more compact — AuthPage login card
├── favicon-32.png          # 32 px PNG favicon fallback (generated from the icon artwork)
├── apple-touch-icon.png    # 180 px opaque PNG for iOS home-screen icons
└── docs/                   # Static markdown files served by Vite (31 files across 16 sections)
    ├── 01-introduction/
    ├── 02-conversations/
    ├── 03-tools/
    └── … (16 sections total)
```

---

## viewMode Routing (chatStore)

`ChatView.tsx` uses a `viewMode` field in `chatStore` to render different content in the main area without a URL-based router. This keeps navigation state in-memory and avoids page reloads.

| viewMode | What renders |
|----------|-------------|
| `'conversation'` | Active conversation thread |
| `'agents'` | Agent list + create/edit form |
| `'daemons'` | Daemon list + create/edit/trigger |
| `'skills'` | Skills list |
| `'mcp'` | Global MCP server list |
| `'mcp_edit'` | MCP server edit form (mcpBrowseId) |
| `'projects'` | Project browse/detail (`projectBrowseId`) — `'__new__'` = full-page create form, else a real id = project overview/detail |
| `'memory'` | Memory panel |
| `'rag'` | Knowledge base panel |
| `'hooks'` | Tool hooks panel |
| `'analytics'` | Analytics/cost dashboard |
| `'docs'` | In-app documentation viewer (DocsPanel) |
| `'welcome'` | About EVA full-screen page (WelcomePanel) |

`chatStore` companion fields for `'docs'`: `docsPath: string` — the current page ID (e.g. `'01-introduction/01-getting-started'`). Action: `setViewDocs(path?)` — resets conversation state and enters docs mode.

`'welcome'` action: `setViewWelcome()` — resets via `VIEW_RESET` and enters welcome mode. Triggered automatically on first login (checked via `localStorage` key `eva_welcome_seen_<username>`), and toggled by clicking the EVA logo in the NavRail.

Navigation actions (`setViewMCP`, `setViewMCPEdit`, `setViewProject`, etc.) are dispatched from anywhere and always update `viewMode` + a companion ID field in the store.

---

## State Management

| Store | Persisted | Contents |
|-------|-----------|----------|
| `authStore` | `localStorage` | JWT, refresh token, username, expiry, refresh timer |
| `sessionStore` | `localStorage` (`eva-session`) | Provider, model, tools, mode flags — `isDirty` excluded |
| `chatStore` | No | Active conversation, SSE block state, viewMode, approval gate, mcpReturnProjectId |
| `uiStore` | No | Panel open states, active tabs |
| `notificationStore` | No | Last 50 daemon/execution events, unread count |

**Auto-save:** `SessionPanel` debounces 800ms then calls `POST /set-session`. No manual Save button. Transient "Auto-saving… / Saved" chip in panel header.

---

## Auth Flow

1. `POST /auth/login` (form-encoded — `application/x-www-form-urlencoded`) → `{access_token, refresh_token, expires_in: 86400}`
2. Access token stored in `authStore`; proactive refresh scheduled 5 min before expiry via `setTimeout`
3. Axios interceptor catches 401 → queues in-flight requests → `POST /auth/refresh` → retries queue
4. SSE: `POST /auth/sse-token` → 30s single-use UUID → passed as `?sse_token=` on the stream URL

---

## SSE Architecture (fetch-based)

The chat stream uses `fetch()`, **not** native `EventSource`. This was necessary because:
- `EventSource` cannot set an `Authorization` header
- `EventSource` gives no access to HTTP status codes on failure

Flow (`useSSE.ts`):
1. `POST /auth/sse-token` → single-use UUID (30s TTL)
2. `fetch GET /eva_response?conversation_id=…&sse_token=…` with `Authorization: Bearer <token>`
3. Stream body read line-by-line; SSE `event:` / `data:` lines parsed manually
4. Only opens when **both** `conversationId` AND `executionId` are set (prevents re-opening on history load)

### SSE Field Name Normalisation

The backend sends non-standard field names on some events. `useSSE.ts` normalises before dispatch:

| Event type | Backend field | Frontend field |
|------------|--------------|----------------|
| `tool_call` | `call_id` | `tool_call_id` |
| `tool_call` | `arguments` | `tool_input` |
| `tool_result` | `call_id` | `tool_call_id` |

### Tool Call Card lifecycle

1. `tool_call` event → `addToolCall()` → card appears with spinner
2. `tool_result` event → `resolveToolCall()` → spinner → green tick (or red X if `is_error: true`)
3. `done` / stream end → `finalizeMessage()` sweeps any still-pending blocks to `result: null` (safety net for error paths)

---

## Navigation (NavRail)

| Icon | Action | Notes |
|------|--------|-------|
| **EVA logo** | Toggles WelcomePanel — click again (or `×`) to return to conversation | `×` also opens WorkspacePanel |
| `FolderOpen` | Always opens WorkspacePanel + resets to conversation home if in a panel viewMode | Cannot toggle closed — use the `×` inside the panel |
| `Brain` | `viewMode: 'memory'` | |
| `BookOpen` | `viewMode: 'rag'` | Knowledge base |
| `Bot` | `viewMode: 'agents'` | |
| `Clock` | `viewMode: 'daemons'` | |
| `Wand2` | `viewMode: 'skills'` | |
| `Webhook` | `viewMode: 'hooks'` | |
| `Plug2` | `viewMode: 'mcp'` | |
| `Settings` | `viewMode: 'settings'` | SessionPanel |
| `BarChart2` | `viewMode: 'analytics'` | |
| `Library` | `viewMode: 'docs'` toggle | Above Bell; re-click exits docs back to conversation |
| `Bell` | Opens NotificationDrawer | Unread badge |
| **Avatar** | Opens UserProfileModal | Ring colour = server health (green/red/grey); tooltip shows status |

### Avatar Health Ring

The user avatar border doubles as the server health indicator — no separate dot:
- **Green border + soft green halo** — server connected
- **Red border + soft red halo** — server connection error
- **Grey border** — connecting / unknown
- Border and shadow transition at 400ms

### UserProfileModal

`UserProfileModal` is a named export from `WorkspacePanel.tsx` and is rendered at `AppShell` root level (gated by `uiStore.profileModalTab`). This means it opens correctly whether or not WorkspacePanel is visible. Both the NavRail avatar and the WorkspacePanel username pill call `openProfileModal('profile')` from `uiStore`.

---

## Project Panel

### Creating a project

The sidebar **"New project"** quicklink (`WorkspacePanel.tsx`) calls `setViewProject('__new__')` directly — same `'__new__'` sentinel convention as `setViewDaemonDetail`/`setViewAgentDetail` — landing straight on `ProjectCreateView` (`ChatView.tsx`), a full-page `.detail`-style form matching the Daemon/Agent create pattern (back button, `dh-title`/`dh-sub` header, `Create Project` action in the header, `.form-section`/`.form-row` body). Fields: Name, Synopsis (required), Description (optional), and an "Use existing folder as workspace" toggle for an external absolute server path. On success, navigates straight into the new project's overview via `setViewProject(id)`.

`ProjectsBrowseCard`'s empty state (no project selected, `projectBrowseId === null`) also has its own "+ New Project" button that routes to the same `'__new__'` view.

Projects have a **three-state lifecycle** enforced in the UI:

```
active  ──Pause──▶  paused  ──Resume──▶  active
                        └───Archive──▶  archived (terminal)
```

Direct archive of a live project is blocked — must pause first.

Each project expands to sub-tabs: **Overview**, **Conversations**, **Agents**, **Daemons**, **MCP**, **Files**.

The **MCP sub-tab** (`ProjectMCPTab`) shows MCP servers scoped to that project with full CRUD:
- Transport badge, health dot, sync button, enable toggle, edit (returns to project tab on close), delete

---

## MCP Panel

`MCPPanel.tsx` handles global (non-project-scoped) MCP servers. Each server row has:
- Health dot + transport badge + enable toggle + sync button + edit + delete
- **Expand chevron** → `ServerDetail` panel with three tabs:
  - **Tools** (default) — lists tools from `GET /mcp/{id}/tools`, capped to 3 visible rows + scroll
  - **Resources** — resource listing
  - **Prompts** — prompt listing

`ServerDetail` is a named export reused by `ProjectMCPTab` — no duplication.

**Global vs project-scoped:** Global lists filter `!x.project_id` — servers, agents, and daemons assigned to a project are hidden from global views and only appear in their project tab.

---

## API Integration

### Provider & Model Selection

- `GET /providers/list` → `ProviderListItem[]` — curated list with `is_configured`, `is_native`, `is_local`
  - Used by the Account tab provider dropdown (dynamic — no hardcoded list)
  - `is_local: true` providers (Ollama local) are excluded from the API key dropdown and handled via a separate host URL field
- `GET /providers/{provider}/models` → live model list (5-min TTL cache on backend)
- `GET /providers/{provider}/models/{model_id}/capabilities` → per-model feature flags + pricing
  - Cached in TanStack Query under key `['capabilities', provider, model]` — shared across WorkspacePanel, SessionPanel, and MessageInput toolbar
  - Key fields: `supports_reasoning`, `reasoning_levels`, `reasoning_can_disable`
- `POST /providers/models/refresh` → invalidate TTL cache

**424 handling:** `GET /providers/{provider}/models` returns `424` when no API key is configured. SessionPanel shows an inline amber banner.

### Reasoning Level Control (`ReasoningControl.tsx`)

`src/components/ui/ReasoningControl.tsx` is the single shared component used by WorkspacePanel and SessionPanel. The chat toolbar (`MessageInput.tsx`) uses the same logic in popover form. All three surfaces follow identical rules:

- `supports_reasoning: false` → control hidden entirely
- `supports_reasoning: true`, levels ≤ 1 → binary toggle; `canDisable: false` forces it always-ON
- `supports_reasoning: true`, levels ≥ 2 → segmented pill picker; Off segment omitted when `canDisable: false`
- Caps loading (`undefined`) → shown optimistically with full level range

`POST /set-session` may snap an unsupported level to the nearest valid one. All three save paths read the response `reasoning` field and sync it back to the session store.

### Secrets / API Keys

- Provider dropdown populated from `GET /providers/list` — ollama_cloud and all future providers appear automatically
- Configured providers show a green glowing dot in the dropdown
- `POST /secrets/{provider}` → write-only key upsert
- `GET /secrets/ollama/base-url` / `POST /secrets/ollama/base-url` → Ollama host management

### MCP

- `GET /mcp` → list servers (filtered by project_id if scoped)
- `POST /mcp` → create server config
- `PATCH /mcp/{id}` → update
- `DELETE /mcp/{id}` → delete
- `POST /mcp/{id}/sync` → re-sync tools/resources/prompts from live server
- `GET /mcp/{id}/tools` → tool list (from DB cache, no live connection needed)
- `GET /mcp/{id}/resources` → resource list
- `GET /mcp/{id}/prompts` → prompt list

### Memory (Sensitive Gate)

`GET /memory/agent` returns entries; entries with `sensitive: true` are gated from agent tool access.
When the agent tries to recall a sensitive entry, the backend injects a `[SENSITIVE: entry_id=… scope_key=…]` placeholder into the response.
`MessageItem.tsx` parses this placeholder and renders a `SensitiveMemoryCard` in-line:
- Grant access for 1 hour → `POST /memory/agent/{scope}/{id}/grant-access`
- Revoke → `DELETE /memory/agent/{scope}/{id}/grant-access`

### Projects

- `GET/POST /projects` — list and create
- `PATCH /projects/{id}` — update name, status (`active`/`paused`/`archived`), workspace path
- `GET /projects/{id}/artifacts/{type}` — read `.eva` workspace files
- `GET /projects/{id}/files` → file browser tree
- `POST /projects/{id}/files` — upload (triggers async RAG ingestion)
- `GET /projects/{id}/conversations|agents|daemons|mcp` — sub-resource listings

### Event Stream

- `GET /events/stream?sse_token=…` — persistent SSE opened on app mount via `useEventStream`
- Events pushed to `notificationStore`; bell icon shows unread count

---

## Design System (EVA NEXUS)

```css
--bg-base:        #06090F    /* deep space black */
--bg-surface1:    #0D1117    /* nav rail */
--bg-surface2:    #111827    /* panels */
--bg-surface3:    #1C2333    /* cards, tooltips */
--eva-primary:    #00C9FF    /* teal accent */
--neural:         #7C6CF3    /* purple accent */
--amber:          #F59E0B
--danger:         #FF4D6D
--success:        #00D4A0
--text-primary:   #E8EAF0
--text-secondary: #9BA3AF
--text-muted:     #4B5563
```

Fonts: **Inter** (UI) + **JetBrains Mono** (code, IDs, monospace values) — self-hosted via `@fontsource` and imported in `src/fonts.ts` (not CSS `@import`, not a CDN `<link>`).

Layout constants: **60px** NavRail | **280px** WorkspacePanel | **340px** side panels.

---

## Documentation Viewer

EVA ships a built-in documentation viewer accessible via the `Library` icon in the NavRail (`viewMode: 'docs'`).

**Content:** markdown files live in `docs/` (the repository's canonical docs folder, source of truth). `vite.config.ts`'s `viteStaticCopy` plugin copies `../docs/**/*` and `../docs/*.*` into the frontend's served output at `dest: '.'`, so a file at `EVA/docs/01-introduction/01-getting-started.md` is served at `/docs/01-introduction/01-getting-started.md` — both in `vite dev` (via the plugin's dev-server middleware) and in the production build (`dist/docs/...`). `DocsPanel.tsx` fetches these paths directly — no backend endpoint required; works fully offline in Electron.

> **Gotcha:** the two `viteStaticCopy` targets must use `dest: '.'`, not `dest: 'docs'` — the plugin already preserves the `docs/` path segment from the `../docs/**/*` glob itself (it strips only the leading `../`, not the `docs` folder name), so an explicit `dest: 'docs'` doubles it into `dist/docs/docs/...`, which 404s and silently falls through to the SPA `index.html` fallback (a bare `data:` fetch with no content-type check will show raw HTML instead of markdown — this bit `WelcomePanel.tsx`'s changelog fetch too, fixed separately with a content-type guard). Fixed 2026-07-11; verified against both `vite dev` and a real `vite build` output.

**To update docs:** edit files directly under `docs/`. The viewer's nav tree is not hand-maintained in the frontend — `useDocsIndex()` (`src/lib/docsIndex.ts`) fetches `docs/manifest.json`, which `viteStaticCopy` ships with the docs, so a new page appears once it's in the manifest; no frontend edit needed. `DocsPanel.tsx` shows a skeleton/spinner while the manifest loads and a \"Could not load the documentation index\" message if it fails; prev/next navigation is suppressed until it's available.

**Layout:**
```
[DocsSidebar 252px] [Article max-width 780px] [On-page TOC 196px]
```

- **Sidebar** — collapsible section groups, active page teal highlight
- **Article** — breadcrumb, `react-markdown` + `remark-gfm` + `rehype-highlight`, code copy button, prev/next navigation cards
- **TOC** — H2/H3 anchors with `IntersectionObserver` active-heading tracking

**Behaviour:**
- Entering docs: WorkspacePanel auto-closes and cannot be reopened alongside docs
- Chat input (`MessageInput`) is hidden in docs mode
- Re-clicking the Library icon exits back to conversation

---

## Welcome Page (WelcomePanel)

An "About EVA" page accessible via the EVA logo in the NavRail. Implemented as a full-screen `viewMode: 'welcome'` panel — same workspace-exclusion rules as docs mode.

**Content:** Hero (`eva-main.svg`), Beta badge, "What is EVA?" (3 paragraphs), "Why EVA is Different" (2×2 feature card grid), "Know Your EVA" (Getting Started + Full Docs links), amber disclaimer box, footer.

**First-login trigger:** `ChatView` reads `username` from `useAuthStore` and checks `localStorage.getItem('eva_welcome_seen_<username>')`. If absent, calls `setViewWelcome()` and sets the flag. Flag is per-account so different users on the same browser/Electron install each see the welcome page exactly once.

**To re-trigger for testing:** `localStorage.removeItem('eva_welcome_seen_<your-username>')` in the browser console, then reload.

**Closing behaviour:** The `×` close button calls `setViewConversation()` AND opens the workspace panel — implemented via `useOpenWorkspace()` context hook exported from `AppShell`.

**Workspace guard (`AppShell`):** A synchronous `showWorkspace` derived variable prevents workspace from rendering alongside welcome or docs — no frame-gap flash:
```tsx
const showWorkspace = view === 'workspace' && viewMode !== 'docs' && viewMode !== 'welcome'
```
Clicking the workspace icon from welcome/docs exits the full-screen view and opens workspace alongside the conversation.

---

## Key Quirks & Gotchas

| Topic | Detail |
|-------|--------|
| `/tools` response | Plain `[]` array (not `{tools: []}`) |
| `/providers/list` response | Plain `[]` array (not `{providers: []}`) |
| `/rag/files` response | `{files: []}` — must extract `.files` |
| `Conversation.id` | Not `conversation_id` |
| `Message.id` | Not `message_id` |
| Login body | `application/x-www-form-urlencoded` (OAuth2PasswordRequestForm) |
| SSE field names | Backend sends `call_id` / `arguments`; frontend normalises to `tool_call_id` / `tool_input` |
| SSE trigger | `useSSE` only opens when BOTH `conversationId` AND `executionId` are set |
| `tool_result` events | Backend only sends these after agent changes (2026-06-22). `finalizeMessage` sweep handles older sessions |
| Provider dropdown | Uses `GET /providers/list` — never hardcode provider IDs in the frontend |
| `is_local` providers | Ollama local (`is_local: true`) uses a host URL, not an API key — handled separately in Account tab |
| Project-scoped items | Agents, daemons, MCP servers with a `project_id` are filtered from global list views |
| MCP return navigation | `mcpReturnProjectId` in chatStore tracks where to return after closing MCP edit from a project tab |
| Docs content source | `docs/` is the source of truth — `viteStaticCopy` copies it into the served `/docs/...` path at both dev and build time (`dest: '.'`, not `'docs'` — see Documentation Viewer section) |
| Project creation | `setViewProject('__new__')` opens the full-page create form — same `'__new__'` sentinel convention as `setViewDaemonDetail`/`setViewAgentDetail` |
| `UserProfileModal` | Named export from `WorkspacePanel.tsx`; rendered at AppShell root level so it opens regardless of panel state |
| Reasoning `canDisable: false` | Button/toggle shows forced-ON; picker opens but Off option is hidden — backend enforces this too, snapping null → "medium" |
| Reasoning snap sync-back | `POST /set-session` may silently adjust the reasoning level; all save paths read `data.reasoning` from the response and update the store |
| Welcome first-login | `localStorage` key `eva_welcome_seen_<username>` (per-account) — remove it in the console to re-trigger the welcome page |
| `useOpenWorkspace()` | Hook exported from `AppShell.tsx` — any descendant can call it to open the workspace panel without prop drilling |
| EVA logo assets are transparent SVGs, shown through `<img>` | `public/eva-icon.svg` (nav rail, empty state), `public/eva-main.svg` (Welcome), `public/eva-main-no-underline.svg` (Auth). Always `<img src>`, never inlined: the files share gradient ids (`g`, `ul`, `ug`), which collide if two are inlined on one page. Use the transparent variants, not the `-on-black` ones: the app surface is `#06090F`, so a baked-in `#000` background shows as a box. The icon is exactly square; keep it so (the old 180x190 PNG was stretched about 5% by its square `<img>` boxes). In `EmptyState` it is 62 px, not 64: the `.empty-mark` tile has a 1 px border, and the global `img { max-width: 100% }` would clamp a 64 px width to 62 and squeeze it. The SVGs are traced polygons (straight segments only), fine at UI sizes but faceted at poster scale, so keep the designer's source file as the real master. PNGs (favicon, apple-touch) are exported from the icon PNG, since SVG is not accepted there. |
| Two different "tool args" fields | **Live SSE** tool-call blocks use `arguments`, normalised to `toolInput` on the in-memory block. **Historical reload** (`GET /conversations/{id}/messages`) uses a completely separate `Message.tool_args` field, read by `apiMessageToUI()` in `chatStore.ts`. Don't assume one name covers both paths — this exact mix-up caused tool arguments to go blank after a reload (fixed 2026-07-13) |
| `ExecutionMetrics` field names | Backend sends `duration_ms` (not `total_duration_ms`) and `tool_calls: ToolCallMetric[]` (no scalar `tool_calls_count` — derive the count from `.length`). `ExecutionFooter` in `ChatView.tsx` |
| `ExecutionFooter` visibility | Only renders while `chatStore.activeExecutionId` is set — that's ephemeral session state (reset on conversation switch/reload), not restored from a persisted "last execution" on the conversation. Don't expect it after a fresh page load on an old conversation |
| Internal-process UI | Tool calls, reasoning, subagent activity, coordinator phases, plan/step/verify/replan trace, and inline errors all render via the shared `InlineDisclosure.tsx` primitive — a single muted collapsed line + chevron, no bordered/tinted cards, no emoji. Build new passive info surfaces on top of it rather than a bespoke card |
| PEV narration is structured, not text | The backend's PEV loop (`pev_runner.py`) no longer writes "Planning…"/"Verifying step…"/etc. as markdown into the answer text — it's all carried on the `step_progress` SSE event (`SSEStepProgress` in `types/api.ts`), rendered by `PlanTraceCard.tsx` via `chatStore.applyStepProgress()`. If you see raw narration text in an answer again, that's a backend regression, not a frontend rendering gap |
| `tool_name` on reload | The backend's `tool_call` DB row and its paired `tool_result` row both carry `tool_name` (fixed 2026-07-14 — previously only the result did). `ReActStepCard.tsx`'s `nameFor()` still falls back to the matched result's name defensively — keep that fallback, it's the only thing that covers a call with no matching result (e.g. cancelled mid-call) |
| Daemon `event_subscriptions` | Real field, round-trips correctly through create/edit (`DaemonConfigResponse` + `DaemonRegistry.upsert()` on the backend). `POST`/`PUT /daemons` is a full-object upsert, not a patch — the edit form must always send back whatever it read from `GET`, same as every other daemon field |
| Daemon form required fields | "System Prompt" (starts read-only behind an edit icon) is required; "Trigger Message" (plain input, always editable) is optional — easy to conflate. `handleSubmit` auto-opens the System Prompt editor if validation fails on it |
| `POST /cancel` is fire-and-forget | Backend returns 200 the instant it sets a Redis flag — it does NOT mean the run has stopped or that `ExecutionMetrics` are final. Never flip `isStreaming` on this response's `onSuccess`; use `chatStore`'s `stopRequested` flag to show a "Stopping…" state instead, and let the real `done` SSE event (via `setStreaming`) be the sole authority for ending a run. Flipping early caused `ExecutionFooter` to permanently cache blank token/duration data (its `useQuery` has `staleTime: Infinity`) — fixed 2026-07-17 |
| `cancelled` SSE event | Sent by the relay right before `finish`/`done` when a run is stopped (`{"reason": "user"}`). Handled in both `useSSE.ts` and `useExecutionReplay.ts` (separate dispatchers, same event vocabulary) — appends a "_Cancelled by user._" acknowledgment. Deliberately does not touch `isStreaming`; `done` still owns that |
| App-close cancellation guard | `window.__evaCloseGuard` (set up by `useCloseGuard.ts`, mounted in `AppShell.tsx`) exposes `getRunning()`/`cancelAll()` for `build/main.js` to call via `webContents.executeJavaScript()` before a true quit (tray "Quit EVA" / in-app Quit button). Not IPC — `executeJavaScript` runs in the page's own JS context, reaching `window` directly even with `contextIsolation:true`. Backed by `GET /executions/running` (in-memory `ExecutionRegistry`, not the DB — a still-running execution has no `execution_metrics` row yet). Titlebar X is unaffected — it only hides, never kills anything, so it doesn't need the guard |
| No default provider/model | `sessionStore.ts`'s `defaultConfig` has no `provider`/`model` key — "not configured" is a real, honest first-run state, not a value to fake. `GET /session` replaces it with the real value on load. Sending/triggering/enhancing with none configured is blocked client-side (`MessageInput.tsx`'s `handleSubmit`/`handleDaemonTrigger`) or reactively on a `424 provider_not_configured` (`SystemPromptField.tsx`), both routed through the shared `ProviderRequiredModal.tsx` (`uiStore.providerRequiredOpen`) rather than a global redirect — a hard 401-style redirect would blow away an in-progress draft |
| `SystemPromptField`'s `showEnhance` | Only `true` in the Agent editor and Daemon editor (`ChatView.tsx`) — the compose-bar and sidebar conversation-prompt editors are separate bespoke `<textarea>`s that don't use this component at all, so flipping `showEnhance` there does nothing. If Enhance needs to reach those two, they need to be swapped onto `SystemPromptField` first, not just re-flagged |
| `ExecutionStatus` polling — terminal vs. non-terminal | `useProjectSync.ts` (and any future execution-status poller) must check against an explicit `TERMINAL_STATUSES` set (`completed`/`failed`/`cancelled`), never an explicit "keep polling" allowlist. Found live: a daemon run hit the step-limit gate and returned `awaiting_step_extension` — a real status (now in the `ExecutionStatus` union) that an allowlist-based check silently treated as "done," stopping the poll and reverting the UI while the backend execution was still open. Default unknown/future statuses to "still going," not "must be finished" |
| Project manual sync | `POST /projects/{id}/sync` (Section 83) re-triggers a project's auto-created sync daemon. `useProjectSync.ts` is the one shared trigger+poll hook — used per-instance in the project header (`ChatView.tsx`), but the sidebar project list (`WorkspacePanel.tsx`) uses one shared `useMutation` across the whole `.map()`'d list instead (a per-row hook would violate Rules of Hooks). Each button only reflects syncs it itself triggered — no cross-surface shared state, same limitation Pause/Resume/Archive already have |
| `openEventStream`'s event allowlist | `src/api/events.ts` passes a hardcoded array of event names to `addEventListener` per type — it is not a generic `onmessage` catch-all. A new SSE event type on the backend arrives as dead air on the wire until its name is added to that array, even if the type/handler exists everywhere else. Hit both `execution.failed` and the two new P-19 events this way — confirmed by reading the code, not assumed |
| Activity tab (Notifications drawer, P-19) | `GET /wakeups` (paused executions, actionable — Resume/Cancel) and `GET /executions/running` (best-effort "currently running," filtered to exclude rows already shown as paused) share the bell-icon drawer as a second tab, not a new nav-rail icon or a separate panel — kept alongside Notifications since both listen to the same `useEventStream` SSE connection, just patch different query caches (`['wakeups']` / `['executions-running']` vs. `notificationStore`). `RunningExecutionSummary` has no conversation title or active-tool field — Running rows are execution_mode + relative time only; `WakeupSummary` does carry `conversation_title`, so Paused rows are richer. Backend fixed a related root-cause bug same session — see `A-12` below |
| Conversation title-gen on narration-then-tool-call turns | Fixed backend-side (`A-12`, commit `0c34a49`) — a first turn that's mostly "I'll now call X" immediately followed by actually calling X used to produce a garbled title verbatim from that raw text. Not frontend's to fix (there's no good client-side heuristic for "this title is garbage"); found via the Activity tab surfacing a real `conversation_title` this way, reported with exact repro, fixed at the source rather than patched around in the UI |
| Daemon `kind` — `'system'` is never sent, not just usually | `DaemonEditView` (`ChatView.tsx`) omits `kind` from the save payload entirely whenever `existing?.kind === 'system'`, rather than relying on the backend's strip-on-save guard for it. System daemons (currently only a project's auto-created sync daemon) don't show the Standard/Supervisor toggle at all — a read-only badge instead. Existing pre-V6.38 projects' sync daemons are still `kind: "standard"`, never retroactively migrated — only daemons auto-created after the backend feature shipped come back `"system"`; don't assume every `project-sync-*` daemon you find in an old test account has the badge |
| MCP config name `__` validation | Purely a UX improvement, not a correctness fix — confirmed nothing in the frontend parses/splits MCP tool names on any delimiter (old `.` or new `__`), so the backend's delimiter change itself broke nothing. `MCPPanel.tsx`'s `canSave` gate and the Server Name field's inline error both check `form.name.includes('__')` client-side so a user finds out before the round-trip 422, not after |
| PII redaction is two independent surfaces, don't conflate them | `pii_redaction_enabled` (session toggle, `SessionPanel.tsx`) redacts PII out of the user's own chat message before it's stored/sent to the LLM. `redact_pii` (a `HookHandler` value, `HooksPanel.tsx`) is a separate PostToolUse hook that scrubs PII from *tool output* — same shape as the pre-existing `redact_env_vars` handler, auto-corrected to POST-only exactly like it. A user can have either on without the other; live-tested confirms the message toggle actually strips PII from the stored/reloaded message (not just the optimistic bubble, which briefly shows raw text before the round trip). Presidio's detection isn't exhaustive — a test SSN in plain `123-45-6789` form wasn't redacted while email/phone were, in the same message; that's backend detection behavior, not a frontend gap |
| `BcTogRow`'s optional `hint` prop | `SessionPanel.tsx`'s toggle-row primitive supports `desc` (always-visible one-liner) and now `hint` (an `Info` icon + native `title` tooltip for a longer explanation) — added for the PII redaction toggle, reusable for any future toggle whose `desc` alone isn't enough context. Don't reach for a bespoke tooltip component here — extend `hint` |
| Background processes live in `GET /executions/running`, not their own list | Section 90 rows are just `RunningExecutionSummary` entries with `execution_mode: "background_process"` plus `pid`/`log_path`/`command` — no separate endpoint. The Stop button in `ActivityTab.tsx`'s `RunningRow` is gated on `execution.pid != null` (never on `execution_mode`), so no other execution mode ever grows a stop primitive by accident. `process.stopped`/`process.crashed` SSE events are typed and allowlisted in `events.ts` and just invalidate `['executions-running']`; `process.crashed` is rarely seen live (it only fires from the backend's boot-time sweep), so the poll is the reliable path for that case. `execution_id` on these rows/events is the process id, and `conversation_id` may be `""` for a process started standalone — treat empty as "nothing to link to" |
| Tool results arrive wrapped: `{ok, result, metadata}` | `ToolCallBlock.result` is the backend's whole tool-result envelope, not the tool's own payload — the real payload is one level down at `result.result`, and `metadata.tool` is the *calling* tool's name, not the target's. Anything that reaches into a tool's output (e.g. `describeToolTargetName()` in `ToolCallCard.tsx`) must unwrap that first; the generic renderer pretty-prints nested objects as JSON rather than `String(v)`, which used to print `[object Object]` |
| `describe_tool` results are deliberately not shown | `ToolCallCard.tsx` renders only the inspected tool's name for `describe_tool` — its result carries the target tool's full internal description and JSON parameter schema, which is model-facing progressive-disclosure detail, not something a user should see. Don't "restore" the generic dump there |
| Naive-UTC `started_at` mis-parses off-UTC (known, unfixed) | The backend sends timestamps like `2026-09-17T10:24:28.004265` with no `Z`, and `new Date(iso)` reads that as *local* time, so `relativeStarted()` in `ActivityTab.tsx` shows "5h ago" for a just-started process on an IST machine. Affects every `RunningRow`, not just background processes. Fix is either a `Z` server-side or appending it client-side before parsing — left alone so far |
| MCP enable's `sync_error` vs. Sync button's `500` | `PUT /mcp/{id} {enabled: true}` (via `mcpApi.update()`) fails gracefully — 200 with `sync_error` set, surfaced as red text under the row in `MCPPanel.tsx` (`syncErrors` state, cleared on a successful re-sync). `POST /mcp/{id}/sync` does **not** have the same graceful handling — a genuinely broken command returns a real `500`, confirmed live. `syncServer`'s mutation has no `onError`, so a stale `sync_error` message is deliberately left in place rather than cleared on a failed retry — don't add an `onError` that clears it without also handling the 500 there |
| `mcpApi.update()` has its own 120s timeout | Overridden per-call, not global — a cold first `npx` install (e.g. enabling `playwright`) can take 20-60s and would otherwise hit the axios client's global 30s default. `toggleEnabled`'s `onError` refetches `['mcp']`/`['mcp-health']` regardless, so even a request that times out client-side but succeeds server-side self-corrects on the next fetch |
| `.env.production`'s `VITE_API_BASE` must stay relative | It's `/api`, not an absolute `http://127.0.0.1:8000` — `build/main.js`'s CSP (`connect-src 'self'`) blocks the absolute form outright, confirmed live via the renderer's own console, and it blocked *every* backend call from the renderer, not just one endpoint. `main.js`'s `serveFrontend()` already proxies `/api/*` to the backend same-origin; there's no reason for this to ever be absolute again |
| CDP relay: `targetId` must equal the real main-frame `frameId` | `build/cdpRelay.js`'s synthetic browser-level target — real Chrome always sets a page target's `targetId` equal to its own main frame's `frameId`, and Playwright's frame-tracking depends on that equality. A random UUID here passes the entire attach handshake (1 context, 1 page!) but breaks every navigation after that with "Frame has been detached" — confirmed live, the hardest bug in this feature to find. Fetched once via `Page.getFrameTree` at relay startup, never generated |
| CDP relay: real Playwright attaches via `Target.setAutoAttach`, not `Target.attachToTarget` | `connectOverCDP` relies on the browser proactively emitting `Target.attachedToTarget` in response to auto-attach being turned on — it does not call `attachToTarget` itself in practice. A relay that only reacts to the latter completes the handshake (misleadingly looks correct) but produces 0 pages. `attachSession()` in `cdpRelay.js` is called from both, idempotently |
| `ws`'s `WebSocketServer` binds asynchronously | `wss.address()` returns `null` until the `'listening'` event fires — reading it immediately after `new WebSocketServer(...)` is a race, not a guarantee. `startCdpRelay()` is `async` and awaits `'listening'` before reading the port, for exactly this reason |
| Preview pane is a flex sibling, not an overlay | `PreviewPanel.tsx`'s root is `flex: '1 1 0%'`, a normal sibling of `<main>` in `AppShell.tsx` (both `flex-1`) — a fixed/absolute-positioned version was tried first and visually covered the chat instead of sharing space with it. `<main>` needed `min-w-0` added for the split to actually work (a flex item won't shrink below its content's intrinsic width without it) |
| Preview pane content survives close/reopen — that's intentional | `closePreview()` in `main.js` only zeroes the `WebContentsView`'s bounds; it deliberately doesn't reset the loaded page, since the agent's session should persist across the user checking in and out. Don't "fix" this by reloading on close — the actual bug that shipped was the address bar not reflecting that persistence (fixed via `preview-url-changed` + `previewOpen()` returning the current URL), not the persistence itself |
| Preview pane is Electron-only, and can't be tested via this project's usual Browser-pane method | `window.electronAPI` is `undefined` in browser/dev-mode Vite testing (the method used for every other feature this project) — a `WebContentsView` is a native Electron construct with no web-page equivalent. Real testing needs the actual Electron shell (`npm start` in `build/`); `tests/electron/test_preview_relay.js` (using the real `playwright` npm package's `_electron.launch()`) is the automated substitute (run `node tests/electron/test_preview_relay.js` from the repo root; it needs `npm ci` in `build/`, a built Electron app there, the backend on port 8000 and a fresh `frontend/dist`), and it deliberately proves the relay against a wholly separate Playwright connection, not just the one that built it |
| Unreachable-backend errors: reuse `setWorkerDown`/`ErrorBlock`, don't invent new UI | `src/lib/errors.ts`'s `isUnreachableError(e)` is true only for `status: undefined`/`502`/`503`/`504` — a real `400`/`422`/`500` response means the server answered and is never treated as "unreachable." Every mutation `onError` that only branched on specific known status codes (send, resend, tool approve/reject, plan decide) needs an explicit fallback branch for the unhandled case, or a connection failure silently does nothing — this was the exact bug (Section 95). The fallback should call the existing `setWorkerDown(true)` (`chatStore`, rendered as `WorkerDownBanner` in `AppShell.tsx`) and add an error block using the existing `ErrorBlock`/Retry UI, not a new component |
| `messagesLoadError` vs. `EmptyState` | `chatStore.messagesLoadError` distinguishes a real conversation whose messages failed to load (show the error + Retry, title still visible) from a genuinely empty/new chat (show `EmptyState`) — before this, both looked identical, which was the reported bug. Set via `src/hooks/useLoadConversationMessages.ts`, the one shared load path used by both `ChatTab.tsx`'s conversation select and `ChatView.tsx`'s Retry button — don't reimplement the load logic a second time, extend the hook |
| Simulating a dropped connection: patch `XMLHttpRequest`, not `window.fetch` | Axios uses `XMLHttpRequest` in the browser by default — patching `window.fetch` to test a network-failure path does nothing. Patch `XMLHttpRequest.prototype.open`/`.send` and dispatch a `ProgressEvent('error')` on `.send()` instead |
| `react-hooks/purity` and `Date.now()` in render | Calling `Date.now()` directly in a component's own render body (even hoisted to a local `const`) is flagged; the same call inside a separate module-level helper function is not. `ChatTab.tsx`'s `dateGroup()`/`relativeDate()`/`daysSince()` all call `Date.now()` internally and are never flagged — follow that pattern instead of an `eslint-disable` |
| Selectors filter on `chat_ready`, never `is_configured` | A provider can hold a key yet be unable to chat (DeepSeek once failed with "Unknown provider" despite a key). `GET /providers/list` items carry `chat_ready`; the session primary, agent editor, daemon editor and fallback rows all offer only `chat_ready` providers, built through `src/lib/providers.ts` (`chatReadyProviderOptions` / `modelOptions`) — extend that helper rather than re-deriving the filter in a fifth picker. The provider list is backend-owned and has changed (Azure/Bedrock/Cohere/Perplexity removed, 11 added): never hardcode provider ids or a provider count in the UI |
| A saved choice that is no longer offered must stay visible | `SearchableSelect` renders its placeholder whenever `value` matches no option, so a saved primary/agent/fallback provider or model that stopped being offered (key removed, provider dropped, model no longer agent-usable) would look like "nothing selected" while the saved value is still sent. The `lib/providers.ts` helpers keep it in the option list flagged "⚠ unavailable"; `modelOptions()` takes a `loaded` flag so models don't flash as unavailable while their list is still fetching |
| Models: always `agent_usable_only=true`; `supports_tool_calls: null` means unknown | `providersApi.getModels()` sends the filter itself, hiding models the agent loop can't drive (e.g. Groq's whisper/orpheus audio models). `null` is "no capability data", shown as an "unverified" badge — not "can't call tools". Model ids are the **bare** form and are sent back unchanged; the backend adds any routing prefix |
| Removing an API key affects every EVA account on the machine | `DELETE /secrets/{provider}` removes the key from the OS credential store, which is shared by all accounts — and does **not** revoke it at the provider. The Remove key confirm in `AccountTabContent.tsx` says both. When testing, only ever remove a throwaway fake key on an unconfigured provider; the API Keys dropdown defaults to the first provider (Groq), whose Remove button is live |
| Known bug: attachment upload errors never show their message | `MessageInput.tsx`'s upload `catch` reads `err.response.status` / `err.response.data.detail`, but `client.ts`'s interceptor already flattens errors to `{status, detail}` — there is no `response`, so the 422/409 messages never appear (always "Upload failed — try again"). Read `err.status` / `err.detail` instead. Unfixed as of 2026-10-22 |
| Provider refusals have an object `detail` | Saves return `{code: 'EVA_PROVIDER_NOT_USABLE', field, reason, message}` and `POST /chat` returns `{error_code: 'provider_not_usable', reason, provider, model, message}`; `client.ts` passes `detail` through as-is. Use `parseProviderError()` / `extractErrorDetail()` (`lib/errors.ts`), which understand both — a caller that only handles string/array `detail` silently loses the server's explanation. `field` (`provider` / `model` / `fallback_chain[i].provider`) says which control the message belongs on |
| A refused `POST /chat` queued nothing — roll back, don't Retry | `MessageInput.tsx` removes the optimistic bubble and restores the draft on a provider refusal. The Section 95 error-block-plus-Retry pattern is right for *failures after acceptance* (network, 5xx) but wrong here: retrying without fixing the provider just fails again |
| New user events need adding to `openEventStream`'s allowlist | Same trap as before: `provider.fallback_promoted` never arrives until it is in `src/api/events.ts`'s array. It has `execution_id` + `summary` but no `conversation_id`, and can arrive before the assistant message starts — `chatStore.providerPromotion` is keyed by execution id for that reason, not reset per turn |
| Token fields overlap — never add them together | `total_tokens` = prompt + completion. `reasoning_tokens` is a **subset of completion** and `cached_tokens` is a **subset of prompt**. Summing prompt + completion + reasoning (as `DaemonRunHistoryTab.tsx` did) or drawing reasoning as its own slice next to the whole completion (as the project Tokens donut did) double-counts. Show cached as a "N of M prompt" line, not another slice |
| Cost `null` = unknown, not zero | `computed_cost_usd` is `null` for subscription (Ollama Cloud), local and unpriced models. An aggregate with `cost_is_partial: true` is a lower bound, so Analytics shows "$x+" with a hover explanation rather than a bare total. Don't render `null` as "$0.00" |
| Errors from `client.ts` are flat `{status, detail}` — there is no `.response` | The response interceptor rejects with `{code, detail, status}` (see `APIError`), not an AxiosError. Code that reads `err.response.status` / `err.response.data.detail` silently gets `undefined` and falls through to its generic branch — this hid every attachment 422/409 and the Knowledge tab's 415 message for months. Read `err.status` / `err.detail` (or `extractErrorDetail()`). Every instance is now fixed (the last two, `HooksPanel.tsx` and `ChangePasswordModal.tsx`, on 2026-10-22) — `grep -rn "\.response\b" src` should only hit `client.ts` itself |
| Very large project file listings need a longer timeout | `GET /projects/{id}/files` at the server's 50 000-file ceiling takes 33–40 s and returns ~14 MB, longer than `client.ts`'s global 30 s timeout — the request aborted and the Files tab ended up empty, so the "truncated" banner could never show. `projectsApi.listFiles()` overrides to 120 s per call (same as `mcpApi.update()`); keep that override if the call is ever touched |
| Locked-PDF wording: match the substring, show the rest verbatim | `friendlyUploadError()` (`lib/errors.ts`) rewrites only text containing `password-protected` (pypdf's wording, bare on ingest jobs, inside "PDF extraction failed: …" on attachments). Everything else, including corrupt-PDF messages, is shown as the server wrote it — the exact strings beyond that substring are not a contract. The attachment error renders only inside the attach popup |
| Every multipart upload must override `Content-Type` | `client.ts`'s instance default is `application/json`. A `FormData` post that doesn't pass `headers: { 'Content-Type': 'multipart/form-data' }` can't be parsed by the server and answers a bare 422 "Request validation failed". `hooksApi.uploadScript()` / `updateScript()` lacked it, so the Hooks → Script Library upload had never worked — hidden because the error handler read `err.response` (see the row above). Every other upload (`attachments.ts`, `projects.ts`, `rag.ts`, `skills.ts`) sets it; copy that for any new one |
| Never declare a component inside another component | `ChangePasswordModal.tsx` once defined `PwdField` inside its own body: every render created a new component type, so React remounted each input per keystroke and focus dropped after one character (pasting worked, typing didn't). Declare components at module level and pass what they need as props. Only `SecLabel` in `ExecutionFooter.tsx` is still nested — static text, so harmless — but don't copy it for anything with an input |
| Fonts are self-hosted — no CDN, and the CSP depends on it | `src/fonts.ts` imports the static `@fontsource/inter` (300–700) and `@fontsource/jetbrains-mono` (400, 500) CSS, the combined per-weight files (`400.css`), which carry a `unicode-range` per subset, so the browser fetches latin / latin-ext / cyrillic / greek / vietnamese files only when the text needs them (the per-subset `latin-400.css` files have no `unicode-range` and make it fetch every subset up front, so don't import those); the `.woff` fallbacks are stripped at build time by `dropWoffFallback` in `vite.config.ts` because Chromium only ever loads the `.woff2`; the packages are SIL OFL 1.1. The static (not `-variable`) packages keep the family names `'Inter'` and `'JetBrains Mono'`, so no inline `fontFamily` needed editing. The packaged app's CSP (`build/main.js`) allows only `font-src 'self' data:` and `style-src 'self' 'unsafe-inline'`, so adding any Google Fonts / CDN font or stylesheet again will be silently blocked in the installed app even though it works under `npm run dev`, which has no CSP. Import fonts from TypeScript, not a CSS `@import`, which conflicts with Tailwind's ordering. |
| Edition-specific wording lives in `src/lib/brand.ts` | The logo alt text, the edition badge and footer label, the optional product tagline, and the legal notice in `WelcomePanel.tsx` all come from the `BRAND` object in `src/lib/brand.ts`; don't write branded wording inline in a component. |
| `npm run lint` must stay at 0 errors | It exits 0 today (0 errors, 30 warnings), so a CI lint step is green; don't add errors. Two rules are warnings on purpose, set in `eslint.config.js` with the reasoning: `react-hooks/set-state-in-effect` (16 existing effects; a strict compiler-oriented rule, refactor them one at a time with tests rather than in bulk) and `react-refresh/only-export-components` (only affects dev-server fast refresh). A leading underscore (`_x`) marks an intentionally unused argument, variable or rest-destructured field and is ignored by `no-unused-vars`. The `react-hooks/rules-of-hooks` and `react-hooks/static-components` rules stay errors: they catch real bugs (a hook called after an early return crashes when a list item changes kind; a component declared inside another remounts its inputs on every keystroke). |
| `keep_alive_on_app_shutdown` — session-scoped by default | Background-process rows (`RunningExecutionSummary`) carry this flag, default `false` — the process is meant to be killed on app quit unless pinned. `ActivityTab.tsx`'s pin button (`processesApi.setKeepAlive()` → `PATCH /processes/{id}/keep-alive`) only sets the DB flag; nothing on the frontend enforces it, since app-quit cleanup is Electron/`build`-repo scope (Section 97 — a `POST /internal/shutdown` call from `stopBackend()`), not this repo. Don't assume pinning a process here has any real effect until that half ships |
| Testing a background process without a real agent turn | `ShellExecutor.start_background()` (same primitive the `background_process` LLM tool calls) can be invoked directly, in-process, against the live server's own `eva.db` — see `tests/test_background_process_live.py` for the pattern. Useful for exercising `GET /executions/running`/`POST /processes/{id}/stop`/the keep-alive toggle live without needing a configured provider or a real agent conversation |
| PEV step-skip (A-21): `skipped` is neither success nor failure | `PlanTraceCard.tsx` renders a `skipped` step with the neutral `SkipForward` icon and a struck-through, dimmed title — deliberately not green or red. The backend never folds skipped into `steps_completed`, so the summary counts them separately ("Completed plan · 2/4 steps · 2 skipped"); don't add them back into the completed total. The reason text is just the existing `verification_summary`. The final step of a plan is never skipped — confirmed live. Unlike `pii_redaction_enabled`, `pev_lookahead_skip_enabled` defaults to **true** (opt-out), so `SessionPanel.tsx` reads it as `?? true`, not `?? false` |


---

## Build

```bash
npm run dev            # dev server (HMR) at localhost:5173
npm run build          # production build → dist/
npm run type-check     # tsc --noEmit
npm run lint           # ESLint
```

### Dev vs production API routing

In dev, Vite proxies `/api` → `http://127.0.0.1:8000` (configured in `vite.config.ts`). This proxy is gone in the production bundle.

For the **Electron build**, `.env.production` bakes `VITE_API_BASE=http://127.0.0.1:8000` into the bundle at build time, so the frontend calls the backend directly without any proxy. The constant in `src/lib/constants.ts`:

```ts
export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'
// dev:  falls back to '/api' (Vite proxy rewrites to http://127.0.0.1:8000)
// prod: baked as 'http://127.0.0.1:8000' from .env.production
```

`.env.production` is committed — it contains no secrets, only the localhost backend address.

---

## Session Notes

