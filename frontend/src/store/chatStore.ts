import { create } from 'zustand'
import type {
  SSEApprovalRequired, SSEPlanApprovalRequired, SSEToolCall,
  SSEToolResult, SSEUsage, PlanApprovalPlan, SSEStepLimitReached, Message,
  SSEStepProgress, PlanStepMeta
} from '@/types/api'

// A rendered message block (built from SSE events)
export type MessageRole = 'user' | 'assistant'

export interface ToolCallBlock {
  kind: 'tool_call'
  tool_call_id: string
  tool_name: string
  tool_input: Record<string, unknown>
  result?: unknown
  is_error?: boolean
  collapsed: boolean
}

export interface ReasoningBlock {
  kind: 'reasoning'
  content: string
  collapsed: boolean
}

export interface TextBlock {
  kind: 'text'
  content: string
}

export interface ErrorBlock {
  kind: 'error'
  message: string
  errorType: string
  provider?: string
}

// G-003 — single event emitted during a subagent delegation
export interface SubagentEvent {
  event: 'started' | 'text' | 'tool_call' | 'completed' | 'failed'
  content: string
  toolName: string | null
  ts: number
}

// G-003 — inline activity block for a single delegate_to_agent call
export interface SubagentActivityBlock {
  kind: 'subagent_activity'
  agentName: string
  status: 'running' | 'completed' | 'failed'
  events: SubagentEvent[]
  startedAt: number
  endedAt?: number
  collapsed: boolean   // collapses automatically on completion
}

// V6.14 — one row per PEV step, built live from step_progress SSE events keyed
// by step_index. Self-corrects after a replan: a later 'running' event for the
// same index simply overwrites the title/status, no array-splicing needed.
export interface PlanTraceStepRow {
  index:                 number
  title:                 string
  status:                'running' | 'verifying' | 'completed' | 'failed' | 'replanning' | 'replan_skipped' | 'cancelled' | 'skipped'
  verificationSummary?:  string
  verificationIssues?:   string
  replanReason?:         string
  errorDetail?:          string
}

export interface PlanTraceBlock {
  kind:        'plan_trace'
  goal:        string
  totalSteps:  number                // may grow after a replan — always taken from the latest event
  planSteps?:  PlanStepMeta[]        // the *initial* plan snapshot, for context — not kept in sync after a replan
  steps:       PlanTraceStepRow[]    // ordered by index, current live state
  complete?:   { stepsCompleted: number; stepsFailed: number; stepsSkipped: number; replanCount: number }
  cancelled?:  boolean
}

export type ContentBlock = TextBlock | ReasoningBlock | ToolCallBlock | ErrorBlock | SubagentActivityBlock | PlanTraceBlock

export interface ChatMessageUI {
  id: string
  role: MessageRole
  blocks: ContentBlock[]
  usage?: SSEUsage
  createdAt: Date
  // Set only for history messages of type tool_call / tool_result
  msgType?: 'tool_call' | 'tool_result'
  toolName?: string
  toolCallId?: string
  toolInput?: Record<string, unknown>
  isError?: boolean
}

// Approval gate state machine
export type ApprovalState =
  | { status: 'idle' }
  | { status: 'tool'; event: SSEApprovalRequired }
  | { status: 'plan'; event: SSEPlanApprovalRequired; plan: PlanApprovalPlan }

// Step-limit extension gate — non-modal, distinct from ApprovalState (which
// blocks the whole viewport); this is a dismissible banner alongside the chat.
export type StepLimitGateState =
  | { status: 'idle' }
  | { status: 'active'; event: SSEStepLimitReached }

// ── Viewport mode ────────────────────────────────────────────────────────────
// 'conversation' = normal chat; 'daemon' / 'agent' = execution viewer
// remaining values = panel-browse placeholders (no chat input)
export type ViewMode =
  | 'conversation'
  | 'daemon'
  | 'agent'
  | 'agents'      // browse / manage subagents (full main-view panel)
  | 'daemons'     // browse / manage daemons (full main-view panel)
  | 'memory'
  | 'rag'
  | 'skills'
  | 'projects'
  | 'settings'
  | 'analytics'
  | 'mcp'
  | 'hooks'
  | 'docs'
  | 'welcome'

export interface ViewTarget {
  type: 'daemon' | 'agent'
  name: string
  executionId: string | null
  systemPrompt?: string   // agent: pre-fills system prompt when user sends
  model?: string
  provider?: string
}

export interface ChatState {
  // Active conversation
  activeConversationId: string | null
  activeExecutionId: string | null

  // Viewport mode — which content the main chat area shows
  viewMode: ViewMode
  activeViewTarget: ViewTarget | null
  replayLoading: boolean   // true while waiting for execution replay SSE to open

  // Messages in the active conversation
  messages: ChatMessageUI[]

  // Pagination state (B-013)
  hasMoreMessages: boolean
  nextMessageCursor: string | null

  // Section 95 — set when the initial message fetch for the active
  // conversation fails, so ChatView.tsx can tell "genuinely empty/new chat"
  // (EmptyState) apart from "a real conversation whose messages didn't load"
  // (activeConversationId is set, but this is too). Cleared by
  // clearMessages()/loadMessages() — i.e. by any subsequent select or a
  // successful load.
  messagesLoadError: string | null
  setMessagesLoadError: (msg: string | null) => void

  // Streaming state
  isStreaming: boolean
  stopRequested: boolean   // true from the moment Stop is clicked until the backend
                            // actually confirms termination (done/cancelled) — see setStreaming
  streamingMsgId: string | null   // id of the message being built
  workerDown: boolean         // true if SSE timed out
  executionStartedAt: number | null   // Date.now() when streaming starts (for elapsed timer)
  stepProgress: {                      // PEV only — updated by step_progress SSE events
    step_index: number
    total_steps: number
    step_title: string
    step_status: string
  } | null
  statusMessage: string | null         // transient status (e.g. "Summarising conversation…")
  providerFallbackNotice: { provider: string; model: string } | null   // cleared at turn start
  // Section 102 - provider.fallback_promoted arrives on the user-event channel, usually
  // BEFORE the assistant message starts, so it is keyed by execution id (shown only when
  // it matches activeExecutionId) rather than cleared at turn start like the notice above.
  providerPromotion: { executionId: string; summary: string } | null

  // Approval gate
  approval: ApprovalState

  // Step-limit extension gate (V6.13) — non-modal banner, separate from approval
  stepLimitGate: StepLimitGateState

  // Welcome screen / What's New overlay — driven by Electron IPC signals
  forceWelcome: boolean         // app-installed: bypass localStorage gate
  showWhatsNew: boolean         // app-updated: show What's New modal
  whatsNewVersion: string | null
  setForceWelcome: () => void
  clearForceWelcome: () => void
  setShowWhatsNew: (version: string) => void
  dismissWhatsNew: () => void

  // Actions
  setActiveConversation: (conversationId: string | null) => void
  setActiveExecution: (executionId: string | null) => void
  setViewDaemon: (name: string, executionId: string | null) => void
  setViewAgent: (name: string, opts?: { systemPrompt?: string; model?: string; provider?: string }) => void
  setViewWelcome: () => void
  setViewConversation: () => void
  setActiveConvInPlace: (id: string) => void   // sets activeConversationId without changing viewMode
  setViewMemory: () => void
  setViewRAG: () => void

  // Memory panel shared UI state
  memoryTab: 'user' | 'agent'
  memoryFilter: string
  memoryAdding: boolean
  memoryScope: string
  setMemoryTab: (tab: 'user' | 'agent') => void
  setMemoryFilter: (cat: string) => void
  setMemoryAdding: (v: boolean) => void
  setMemoryScope: (scope: string) => void
  setViewSkills: () => void
  setViewSkillDetail: (name: string) => void
  setViewSkillFile: (filePath: string) => void
  skillBrowseId: string | null
  skillFilePath: string | null
  setViewAgents: () => void
  setViewAgentDetail: (name: string) => void   // '__new__' = create form, else agent name = edit form
  agentBrowseId: string | null
  setViewDaemons: () => void
  setViewDaemonDetail: (name: string) => void   // '__new__' = create form, else daemon name = edit form
  daemonBrowseId: string | null
  setViewProjects: () => void
  setViewProject: (id: string) => void   // open a specific project in the chat area; '__new__' = create form
  setViewSettings: () => void
  setViewAnalytics: () => void
  setViewMCP: () => void
  setViewMCPEdit: (id: string, returnProjectId?: string) => void   // navigate to MCP panel and pre-open edit form for this config id
  clearMCPBrowse: () => void             // clear mcpBrowseId after edit form is opened
  mcpBrowseId: string | null
  mcpReturnProjectId: string | null          // if set, closeForm returns to this project's MCP tab
  setViewHooks: () => void
  setViewDocs: (path?: string) => void
  docsPath: string

  // Embedded preview pane (CDP-driven browser automation) — a persistent
  // side panel, not a ViewMode: it coexists with whatever's currently
  // showing (chat or any full-view panel) rather than replacing it, so it's
  // deliberately NOT reset by VIEW_RESET/any setView* action. Global (not
  // AppShell-local useState) because ToolCallCard's screenshot thumbnails
  // need to open it from deep inside the message tree.
  previewOpen: boolean
  setPreviewOpen: (open: boolean) => void

  // Project browse shared UI state
  projectBrowseId: string | null   // '__new__' = create form, else project id = browse/overview
  projectBrowseTab: string           // 'overview' | 'conversations' | 'files' | 'agents' | 'daemons' | 'tasks' | 'knowledge' | 'policies' | 'errors'
  projectConvId: string | null    // conversation open inside project view (keeps viewMode:'projects')
  projectConvTitle: string | null    // title of the open conversation
  projectKnowledgeUpload: boolean             // signal: (+) button clicked — trigger file dialog
  setProjectBrowseTab: (tab: string) => void
  setProjectConvId: (id: string | null, title?: string | null) => void
  setProjectKnowledgeUpload: (v: boolean) => void
  setReplayLoading: (v: boolean) => void
  addUserMessage: (content: string) => string
  startAssistantMessage: () => string
  appendTextChunk: (msgId: string, chunk: string) => void
  appendReasoningChunk: (msgId: string, chunk: string) => void
  addToolCall: (msgId: string, event: SSEToolCall) => void
  resolveToolCall: (msgId: string, event: SSEToolResult) => void
  finalizeMessage: (msgId: string, usage?: SSEUsage) => void
  attachUsage: (msgId: string, usage: SSEUsage) => void
  addStreamError: (message: string, errorType: string, provider?: string) => void
  appendErrorBlock: (msgId: string, message: string, errorType: string, provider?: string) => void
  setStreaming: (v: boolean) => void
  setStopRequested: (v: boolean) => void
  setWorkerDown: (v: boolean) => void
  setApproval: (state: ApprovalState) => void
  clearApproval: () => void
  setStepLimitGate: (state: StepLimitGateState) => void
  clearStepLimitGate: () => void
  setStepProgress: (p: ChatState['stepProgress']) => void
  applyStepProgress: (msgId: string, event: SSEStepProgress) => void
  setStatusMessage: (msg: string | null) => void
  setProviderFallbackNotice: (notice: { provider: string; model: string } | null) => void
  setProviderPromotion: (p: { executionId: string; summary: string } | null) => void
  toggleReasoning: (msgId: string, blockIdx: number) => void
  startSubagentDelegation: (msgId: string, agentName: string, ts: number) => void
  appendSubagentEvent: (msgId: string, agentName: string, event: SubagentEvent) => void
  finalizeSubagentDelegation: (msgId: string, agentName: string, status: 'completed' | 'failed', ts: number) => void
  toggleSubagent: (msgId: string, agentName: string) => void
  truncateMessagesFrom: (msgId: string) => void
  clearMessages: () => void
  loadMessages: (msgs: ChatMessageUI[], hasMore?: boolean, cursor?: string | null) => void
  prependMessages: (older: ChatMessageUI[], hasMore: boolean, cursor: string | null) => void
}

const uuid = () => crypto.randomUUID()

// ── Convert a persisted API Message → ChatMessageUI ───────────────────────────
function tryParseJSON(s: string | null): Record<string, unknown> {
  if (!s) return {}
  try { return JSON.parse(s) as Record<string, unknown> } catch { return {} }
}

export function apiMessageToUI(m: Message): ChatMessageUI {
  if (m.type === 'tool_call') {
    return {
      id: m.id,
      role: 'assistant',
      msgType: 'tool_call',
      toolName: m.tool_name,
      toolCallId: m.tool_call_id,
      // tool_args may arrive as a parsed object or as JSON in content
      toolInput: m.tool_args ?? tryParseJSON(m.content),
      blocks: [],
      createdAt: new Date(m.created_at),
    }
  }
  if (m.type === 'tool_result' || m.role === 'tool') {
    return {
      id: m.id,
      role: 'assistant',
      msgType: 'tool_result',
      toolName: m.tool_name,
      toolCallId: m.tool_call_id,
      isError: m.is_error ?? false,
      blocks: m.content ? [{ kind: 'text', content: m.content }] : [],
      createdAt: new Date(m.created_at),
    }
  }
  return {
    id: m.id,
    role: m.role as MessageRole,
    blocks: m.content ? [{ kind: 'text', content: m.content }] : [],
    createdAt: new Date(m.created_at),
  }
}

const VIEW_RESET = {
  messages: [] as ChatMessageUI[],
  activeConversationId: null as string | null,
  activeExecutionId: null as string | null,
  isStreaming: false,
  stopRequested: false,
  streamingMsgId: null as string | null,
  hasMoreMessages: false,
  nextMessageCursor: null as string | null,
  messagesLoadError: null as string | null,
  approval: { status: 'idle' } as ApprovalState,
  stepLimitGate: { status: 'idle' } as StepLimitGateState,
  executionStartedAt: null as number | null,
  stepProgress: null as null,
  statusMessage: null as string | null,
  providerFallbackNotice: null as { provider: string; model: string } | null,
}

export const useChatStore = create<ChatState>((set) => ({
  activeConversationId: null,
  activeExecutionId: null,
  viewMode: 'conversation',
  activeViewTarget: null,
  replayLoading: false,
  skillBrowseId: null,
  skillFilePath: null,
  agentBrowseId: null,
  daemonBrowseId: null,
  mcpBrowseId: null,
  mcpReturnProjectId: null,
  messages: [],
  hasMoreMessages: false,
  nextMessageCursor: null,
  messagesLoadError: null,
  setMessagesLoadError: (msg) => set({ messagesLoadError: msg }),
  isStreaming: false,
  stopRequested: false,
  streamingMsgId: null,
  workerDown: false,
  approval: { status: 'idle' },
  stepLimitGate: { status: 'idle' },
  forceWelcome: false,
  showWhatsNew: false,
  whatsNewVersion: null,
  executionStartedAt: null,
  stepProgress: null,
  statusMessage: null,
  providerFallbackNotice: null,
  providerPromotion: null,

  // Switching to a conversation also exits daemon/agent viewport mode
  setActiveConversation: (id) => set({
    activeConversationId: id,
    viewMode: 'conversation',
    activeViewTarget: null,
    replayLoading: false,
  }),
  setActiveExecution: (id) => set({ activeExecutionId: id }),

  setViewDaemon: (name, executionId) => set({
    ...VIEW_RESET,
    viewMode: 'daemon',
    activeViewTarget: { type: 'daemon', name, executionId },
    replayLoading: executionId != null,
  }),

  setViewAgent: (name, opts) => set({
    ...VIEW_RESET,
    viewMode: 'agent',
    activeViewTarget: {
      type: 'agent', name, executionId: null,
      systemPrompt: opts?.systemPrompt,
      model: opts?.model,
      provider: opts?.provider,
    },
    replayLoading: false,
  }),

  setViewConversation: () => set({
    viewMode: 'conversation', activeViewTarget: null, replayLoading: false,
  }),
  setActiveConvInPlace: (id) => set({ activeConversationId: id, activeExecutionId: null }),

  memoryTab: 'user',
  memoryFilter: 'all',
  memoryAdding: false,
  memoryScope: 'default',
  setMemoryTab: (tab) => set({ memoryTab: tab }),
  setMemoryFilter: (cat) => set({ memoryFilter: cat }),
  setMemoryAdding: (v) => set({ memoryAdding: v }),
  setMemoryScope: (scope) => set({ memoryScope: scope }),

  setViewMemory: () => set({ ...VIEW_RESET, viewMode: 'memory' }),
  setViewRAG: () => set({ ...VIEW_RESET, viewMode: 'rag' }),
  setViewSkills: () => set({ ...VIEW_RESET, viewMode: 'skills', skillBrowseId: null, skillFilePath: null }),
  setViewSkillDetail: (name) => set({ skillBrowseId: name, skillFilePath: null }),
  setViewSkillFile: (filePath) => set({ skillFilePath: filePath }),
  setViewAgents: () => set({ ...VIEW_RESET, viewMode: 'agents', agentBrowseId: null }),
  setViewAgentDetail: (name) => set({ agentBrowseId: name }),
  setViewDaemons: () => set({ ...VIEW_RESET, viewMode: 'daemons', daemonBrowseId: null }),
  setViewDaemonDetail: (name) => set({ daemonBrowseId: name }),
  setViewProjects: () => set({ ...VIEW_RESET, viewMode: 'projects', projectBrowseId: null, projectConvId: null, projectConvTitle: null }),
  setViewProject: (id) => set({ ...VIEW_RESET, viewMode: 'projects', projectBrowseId: id, projectBrowseTab: 'overview', projectConvId: null, projectConvTitle: null }),
  setViewSettings: () => set({ ...VIEW_RESET, viewMode: 'settings' }),
  setViewAnalytics: () => set({ ...VIEW_RESET, viewMode: 'analytics' }),
  setViewMCP: () => set({ ...VIEW_RESET, viewMode: 'mcp', mcpBrowseId: null, mcpReturnProjectId: null }),
  setViewMCPEdit: (id, returnProjectId) => set({ ...VIEW_RESET, viewMode: 'mcp', mcpBrowseId: id, mcpReturnProjectId: returnProjectId ?? null }),
  clearMCPBrowse: () => set({ mcpBrowseId: null }),
  setViewHooks: () => set({ ...VIEW_RESET, viewMode: 'hooks' }),
  docsPath: '01-introduction/01-getting-started',
  setViewDocs: (path) => set({ ...VIEW_RESET, viewMode: 'docs', docsPath: path ?? '01-introduction/01-getting-started' }),
  setViewWelcome: () => set({ ...VIEW_RESET, viewMode: 'welcome' }),
  previewOpen: false,
  setPreviewOpen: (open) => set({ previewOpen: open }),
  setForceWelcome: () => set({ forceWelcome: true }),
  clearForceWelcome: () => set({ forceWelcome: false }),
  setShowWhatsNew: (version) => set({ showWhatsNew: true, whatsNewVersion: version }),
  dismissWhatsNew: () => set({ showWhatsNew: false, whatsNewVersion: null }),

  projectBrowseId: null,
  projectBrowseTab: 'overview',
  projectConvId: null,
  projectConvTitle: null,
  projectKnowledgeUpload: false,
  setProjectBrowseTab: (tab) => set({ projectBrowseTab: tab, projectConvId: null, projectConvTitle: null }),
  setProjectConvId: (id, title) => set({ projectConvId: id, projectConvTitle: title ?? null }),
  setProjectKnowledgeUpload: (v) => set({ projectKnowledgeUpload: v }),

  setReplayLoading: (v) => set({ replayLoading: v }),

  addUserMessage: (content) => {
    const id = uuid()
    const msg: ChatMessageUI = {
      id, role: 'user',
      blocks: [{ kind: 'text', content }],
      createdAt: new Date(),
    }
    set(s => ({ messages: [...s.messages, msg] }))
    return id
  },

  startAssistantMessage: () => {
    const id = uuid()
    const msg: ChatMessageUI = {
      id, role: 'assistant',
      blocks: [],
      createdAt: new Date(),
    }
    set(s => ({
      messages: [...s.messages, msg],
      streamingMsgId: id,
      isStreaming: true,
      stopRequested: false,
      executionStartedAt: Date.now(),
      stepProgress: null,
      statusMessage: null,
      providerFallbackNotice: null,
    }))
    return id
  },

  appendTextChunk: (msgId, chunk) => {
    set(s => ({
      messages: s.messages.map(m => {
        if (m.id !== msgId) return m
        const blocks = [...m.blocks]
        const last = blocks[blocks.length - 1]
        if (last?.kind === 'text') {
          blocks[blocks.length - 1] = { kind: 'text', content: last.content + chunk }
        } else {
          blocks.push({ kind: 'text', content: chunk })
        }
        return { ...m, blocks }
      })
    }))
  },

  appendReasoningChunk: (msgId, chunk) => {
    set(s => ({
      messages: s.messages.map(m => {
        if (m.id !== msgId) return m
        const blocks = [...m.blocks]
        const last = blocks[blocks.length - 1]
        if (last?.kind === 'reasoning') {
          blocks[blocks.length - 1] = { ...last, content: last.content + chunk }
        } else {
          blocks.push({ kind: 'reasoning', content: chunk, collapsed: false })
        }
        return { ...m, blocks }
      })
    }))
  },

  addToolCall: (msgId, event) => {
    const block: ToolCallBlock = {
      kind: 'tool_call',
      tool_call_id: event.tool_call_id,
      tool_name: event.tool_name,
      tool_input: event.tool_input,
      collapsed: true,   // collapsed by default — compact header view while running
    }
    set(s => ({
      messages: s.messages.map(m =>
        m.id === msgId ? { ...m, blocks: [...m.blocks, block] } : m
      )
    }))
  },

  // Appends a per-step error inline into the message currently in progress —
  // preserves chronological order when the backend recovers (replans) and
  // keeps streaming more blocks into the same message afterward. See
  // addStreamError below for the fallback (no message in progress yet).
  appendErrorBlock: (msgId, message, errorType, provider) => {
    const block: ErrorBlock = { kind: 'error', message, errorType, provider }
    set(s => ({
      messages: s.messages.map(m =>
        m.id === msgId ? { ...m, blocks: [...m.blocks, block] } : m
      )
    }))
  },

  resolveToolCall: (msgId, event) => {
    set(s => ({
      messages: s.messages.map(m => {
        if (m.id !== msgId) return m
        return {
          ...m,
          blocks: m.blocks.map(b =>
            b.kind === 'tool_call' && b.tool_call_id === event.tool_call_id
              ? { ...b, result: event.result, is_error: event.is_error, collapsed: true }
              : b
          )
        }
      })
    }))
  },

  finalizeMessage: (msgId, usage) => {
    set(s => ({
      messages: s.messages.map(m => {
        if (m.id !== msgId) return m
        return {
          ...m,
          usage,
          // Backend doesn't stream tool_result events — resolve any still-pending
          // tool call blocks so their spinner stops and they show as complete.
          blocks: m.blocks.map(b =>
            b.kind === 'tool_call' && b.result === undefined
              ? { ...b, result: null }
              : b
          ),
        }
      }),
      isStreaming: false,
      stopRequested: false,
      streamingMsgId: null,
    }))
  },

  // Attach usage to the message WITHOUT stopping the stream —
  // used when the 'usage' SSE event arrives before 'done'.
  attachUsage: (msgId, usage) => {
    set(s => ({
      messages: s.messages.map(m =>
        m.id === msgId ? { ...m, usage } : m
      ),
    }))
  },
  // stepProgress intentionally NOT cleared here — ExecutionStatusBar reads it for the
  // fade-out phase after streaming ends. VIEW_RESET clears it on next navigation.

  addStreamError: (message, errorType, provider) => {
    const id = uuid()
    const msg: ChatMessageUI = {
      id, role: 'assistant',
      blocks: [{ kind: 'error', message, errorType, provider }],
      createdAt: new Date(),
    }
    set(s => ({
      messages: [...s.messages, msg],
      isStreaming: false,
      stopRequested: false,
      streamingMsgId: null,
    }))
  },

  // Clearing stopRequested here (not just where isStreaming is set false elsewhere)
  // covers every "streaming ended" call site for free — useSSE.ts and
  // useExecutionReplay.ts both end a run by calling this action.
  setStreaming: (v) => set(s => ({ isStreaming: v, stopRequested: v ? s.stopRequested : false })),
  setStopRequested: (v) => set({ stopRequested: v }),
  setWorkerDown: (v) => set({ workerDown: v }),
  setApproval: (state) => set({ approval: state }),
  clearApproval: () => set({ approval: { status: 'idle' } }),
  setStepLimitGate: (state) => set({ stepLimitGate: state }),
  clearStepLimitGate: () => set({ stepLimitGate: { status: 'idle' } }),
  setStepProgress: (p) => set({ stepProgress: p }),

  // V6.14 — upserts the message's PlanTraceBlock from a step_progress SSE
  // event. Creates the block on first arrival (always 'planning'), then
  // updates it in place — same find-or-create shape as addToolCall/resolveToolCall.
  applyStepProgress: (msgId, event) => {
    set(s => ({
      messages: s.messages.map(m => {
        if (m.id !== msgId) return m

        const existing = m.blocks.find((b): b is PlanTraceBlock => b.kind === 'plan_trace')
        const block: PlanTraceBlock = existing ?? {
          kind: 'plan_trace', goal: '', totalSteps: 0, steps: [],
        }

        const next: PlanTraceBlock = { ...block }
        if (event.goal) next.goal = event.goal
        if (event.total_steps) next.totalSteps = event.total_steps
        if (event.plan_steps && !next.planSteps) next.planSteps = event.plan_steps

        if (event.step_status === 'plan_complete') {
          next.complete = {
            stepsCompleted: event.steps_completed ?? 0,
            stepsFailed:    event.steps_failed ?? 0,
            stepsSkipped:   event.steps_skipped ?? 0,
            replanCount:    event.replan_count ?? 0,
          }
        } else if (event.step_status === 'cancelled') {
          next.cancelled = true
        } else if (event.step_status !== 'planning' && event.step_status !== 'awaiting_plan_approval') {
          const rows = [...next.steps]
          const idx = rows.findIndex(r => r.index === event.step_index)
          const row: PlanTraceStepRow = {
            index:  event.step_index,
            title:  event.step_title || rows[idx]?.title || `Step ${event.step_index + 1}`,
            status: event.step_status,
            verificationSummary: event.verification_summary ?? rows[idx]?.verificationSummary,
            verificationIssues:  event.verification_issues  ?? rows[idx]?.verificationIssues,
            replanReason:        event.replan_reason         ?? rows[idx]?.replanReason,
            errorDetail:          event.error_detail           ?? rows[idx]?.errorDetail,
          }
          if (idx >= 0) rows[idx] = row
          else rows.push(row)
          next.steps = rows
        }

        const blocks = existing
          ? m.blocks.map(b => b === existing ? next : b)
          : [...m.blocks, next]
        return { ...m, blocks }
      })
    }))
  },

  setStatusMessage: (msg) => set({ statusMessage: msg }),
  setProviderFallbackNotice: (notice) => set({ providerFallbackNotice: notice }),
  setProviderPromotion: (p) => set({ providerPromotion: p }),

  toggleReasoning: (msgId, blockIdx) => {
    set(s => ({
      messages: s.messages.map(m => {
        if (m.id !== msgId) return m
        return {
          ...m,
          blocks: m.blocks.map((b, i) =>
            i === blockIdx && b.kind === 'reasoning'
              ? { ...b, collapsed: !b.collapsed }
              : b
          )
        }
      })
    }))
  },

  startSubagentDelegation: (msgId, agentName, ts) => {
    const block: SubagentActivityBlock = {
      kind: 'subagent_activity', agentName,
      status: 'running', events: [], startedAt: ts, collapsed: false,
    }
    set(s => ({
      messages: s.messages.map(m =>
        m.id === msgId ? { ...m, blocks: [...m.blocks, block] } : m
      )
    }))
  },

  appendSubagentEvent: (msgId, agentName, event) => {
    set(s => ({
      messages: s.messages.map(m => {
        if (m.id !== msgId) return m
        return {
          ...m,
          blocks: m.blocks.map(b =>
            b.kind === 'subagent_activity' && b.agentName === agentName && b.status === 'running'
              ? { ...b, events: [...b.events, event] }
              : b
          ),
        }
      })
    }))
  },

  finalizeSubagentDelegation: (msgId, agentName, status, ts) => {
    set(s => ({
      messages: s.messages.map(m => {
        if (m.id !== msgId) return m
        return {
          ...m,
          blocks: m.blocks.map(b =>
            b.kind === 'subagent_activity' && b.agentName === agentName && b.status === 'running'
              ? { ...b, status, endedAt: ts, collapsed: true }
              : b
          ),
        }
      })
    }))
  },

  toggleSubagent: (msgId, agentName) => {
    set(s => ({
      messages: s.messages.map(m => {
        if (m.id !== msgId) return m
        return {
          ...m,
          blocks: m.blocks.map(b =>
            b.kind === 'subagent_activity' && b.agentName === agentName
              ? { ...b, collapsed: !b.collapsed }
              : b
          ),
        }
      })
    }))
  },

  truncateMessagesFrom: (msgId) => {
    set(s => {
      const idx = s.messages.findIndex(m => m.id === msgId)
      if (idx === -1) return s
      return { messages: s.messages.slice(0, idx) }
    })
  },

  clearMessages: () => set({
    messages: [], activeExecutionId: null,
    hasMoreMessages: false, nextMessageCursor: null, messagesLoadError: null,
  }),
  loadMessages: (msgs, hasMore = false, cursor = null) => set({
    messages: msgs, hasMoreMessages: hasMore, nextMessageCursor: cursor, messagesLoadError: null,
  }),
  prependMessages: (older, hasMore, cursor) => set(s => ({
    messages: [...older, ...s.messages],
    hasMoreMessages: hasMore,
    nextMessageCursor: cursor,
  })),
}))
