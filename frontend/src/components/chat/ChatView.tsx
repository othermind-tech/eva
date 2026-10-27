import { useEffect, useRef, useState, useCallback } from 'react'
import { Loader2, ChevronUp, ChevronDown } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useChatStore, apiMessageToUI } from '@/store/chatStore'
import { isPanelViewMode } from './ChatViewModes'
import { useSSE } from '@/hooks/useSSE'
import { useExecutionReplay } from '@/hooks/useExecutionReplay'
import { conversationsApi } from '@/api/conversations'
import { chatApi } from '@/api/chat'
import { useSessionStore } from '@/store/sessionStore'
import { useAuthStore }    from '@/store/authStore'
import { SCROLL_AT_BOTTOM_PX, SCROLL_DELAY_MS } from '@/lib/constants'
import { extractErrorDetail, isUnreachableError } from '@/lib/errors'
import { useLoadConversationMessages } from '@/hooks/useLoadConversationMessages'
import MessageList from './MessageList'
import MessageInput from './MessageInput'
import ChatHeader from './ChatHeader'
import MemoryBrowseCard from '@/components/memory/MemoryBrowseCard'
import RAGBrowseCard from '@/components/rag/RAGBrowseCard'
import SkillsMainView from '@/components/skills/SkillsMainView'
import AgentBrowseCard from '@/components/agents/AgentBrowseCard'
import AgentProfileCard from '@/components/agents/AgentProfileCard'
import AgentsMainView from '@/components/agents/AgentsMainView'
import DaemonBrowseCard from '@/components/daemons/DaemonBrowseCard'
import DaemonNoRunsCard from '@/components/daemons/DaemonNoRunsCard'
import DaemonsMainView from '@/components/daemons/DaemonsMainView'
import { ExecutionFooter } from '@/components/projects/ExecutionFooter'
import ProjectsBrowseCard from '@/components/projects/ProjectsBrowseCard'
import EmptyState from './EmptyState'
import ExecutionStatusBar from './ExecutionStatusBar'
import AnalyticsPanel from '@/components/panels/AnalyticsPanel'
import SessionPanel   from '@/components/panels/SessionPanel'
import MCPPanel       from '@/components/panels/MCPPanel'
import HooksPanel     from '@/components/panels/HooksPanel'
import DocsPanel      from '@/components/docs/DocsPanel'
import WelcomePanel   from '@/components/welcome/WelcomePanel'

export default function ChatView() {
  const activeConversationId  = useChatStore(s => s.activeConversationId)
  const activeExecutionId     = useChatStore(s => s.activeExecutionId)
  const messages              = useChatStore(s => s.messages)
  const hasMoreMessages       = useChatStore(s => s.hasMoreMessages)
  const nextMessageCursor     = useChatStore(s => s.nextMessageCursor)
  const prependMessages       = useChatStore(s => s.prependMessages)
  const isStreaming            = useChatStore(s => s.isStreaming)
  const viewMode              = useChatStore(s => s.viewMode)
  const activeViewTarget      = useChatStore(s => s.activeViewTarget)
  const replayLoading         = useChatStore(s => s.replayLoading)
  const addUserMessage        = useChatStore(s => s.addUserMessage)
  const setActiveConversation = useChatStore(s => s.setActiveConversation)
  const setActiveExecution    = useChatStore(s => s.setActiveExecution)
  const setViewWelcome        = useChatStore(s => s.setViewWelcome)
  const setStreaming           = useChatStore(s => s.setStreaming)
  const truncateMessagesFrom  = useChatStore(s => s.truncateMessagesFrom)
  const addStreamError        = useChatStore(s => s.addStreamError)
  const setWorkerDown         = useChatStore(s => s.setWorkerDown)
  const messagesLoadError     = useChatStore(s => s.messagesLoadError)
  const loadConversationMessages = useLoadConversationMessages()
  const activeProjectId       = useSessionStore(s => s.activeProjectId)
  const username              = useAuthStore(s => s.username)
  const queryClient           = useQueryClient()

  const scrollRef   = useRef<HTMLDivElement>(null)
  const bottomRef   = useRef<HTMLDivElement>(null)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [draft, setDraft]     = useState('')
  const [atBottom, setAtBottom] = useState(true)

  const forceWelcome      = useChatStore(s => s.forceWelcome)
  const clearForceWelcome = useChatStore(s => s.clearForceWelcome)

  // Show welcome on first login per user, or whenever app-installed IPC fires
  useEffect(() => {
    if (!username) return
    const key = `eva_welcome_seen_${username}`
    if (forceWelcome || !localStorage.getItem(key)) {
      setViewWelcome()
      localStorage.setItem(key, '1')
      if (forceWelcome) clearForceWelcome()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [username, forceWelcome])

  // Chat SSE — fires in conversation mode OR when a project conversation is open
  const projectConvId = useChatStore(s => s.projectConvId)
  const sseActive = viewMode === 'conversation' || (viewMode === 'projects' && !!projectConvId)
  useSSE(
    sseActive ? activeConversationId : null,
    sseActive ? activeExecutionId    : null,
  )

  // Execution replay — daemon/agent mode with an execution ID to load
  useExecutionReplay(
    viewMode !== 'conversation' ? (activeViewTarget?.executionId ?? null) : null,
  )

  // Track whether user is near the bottom
  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setAtBottom(distFromBottom < SCROLL_AT_BOTTOM_PX)
  }, [])

  // Auto-scroll only when already at bottom
  useEffect(() => {
    if (atBottom) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, atBottom])

  // When a new streaming response starts always scroll to bottom
  useEffect(() => {
    if (isStreaming) {
      setAtBottom(true)
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), SCROLL_DELAY_MS)
    }
  }, [isStreaming])

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    setAtBottom(true)
  }

  // B-013: load older (paginated) messages
  const handleLoadOlder = async () => {
    if (!activeConversationId || !nextMessageCursor || loadingOlder) return
    setLoadingOlder(true)
    const prevScrollHeight = scrollRef.current?.scrollHeight ?? 0
    try {
      const { messages: older, has_more, next_cursor } = await conversationsApi.getMessages(
        activeConversationId,
        { limit: 50, before: nextMessageCursor }
      )
      prependMessages(older.map(apiMessageToUI), has_more, next_cursor)
      // Maintain scroll position after prepend so content doesn't jump
      requestAnimationFrame(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop =
            scrollRef.current.scrollHeight - prevScrollHeight
        }
      })
    } catch (err) {
      if (import.meta.env.DEV) console.error('[ChatView] failed to load older messages', err)
    } finally { setLoadingOlder(false) }
  }

  // Suggestion chip → populate the input and focus it
  const handleSuggestionSelect = (text: string) => {
    setDraft(text)
  }

  // ── Shared resend helper (used by edit + retry) ──────────────────────────
  const resend = useCallback(async (text: string, conversationId: string | null) => {
    addUserMessage(text)
    try {
      const data = await chatApi.send({
        message:         text,
        conversation_id: conversationId ?? undefined,
        project_id:      activeProjectId ?? undefined,
      })
      setActiveConversation(data.conversation_id)
      setActiveExecution(data.execution_id)
      setStreaming(true)
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
    } catch (err) {
      if (import.meta.env.DEV) console.error('[ChatView] resend failed', err)
      // Section 95 — identical gap to MessageInput.tsx's send mutation: this
      // path (edit-resend, retry-resend) had the same silent swallow.
      if (isUnreachableError(err)) {
        setWorkerDown(true)
        addStreamError("Could not reach the server. Check your connection and try again.", 'network')
      } else {
        addStreamError(extractErrorDetail(err, 'Something went wrong sending this message.'), 'send_failed')
      }
    }
  }, [addUserMessage, setActiveConversation, setActiveExecution, setStreaming, addStreamError, setWorkerDown, activeProjectId, queryClient])

  // Edit a user message — truncate from that message onwards (UI + DB), re-send with new text
  const handleEditSend = useCallback(async (msgId: string, newText: string) => {
    if (isStreaming) return
    truncateMessagesFrom(msgId)
    if (activeConversationId) {
      try { await conversationsApi.truncateFrom(activeConversationId, msgId) } catch { /* non-critical */ }
    }
    resend(newText, activeConversationId)
  }, [isStreaming, truncateMessagesFrom, resend, activeConversationId])

  // Retry after error — find last user message before the error, truncate (UI + DB), re-send
  const handleRetry = useCallback(async (errorMsgId: string) => {
    if (isStreaming) return
    const idx = messages.findIndex(m => m.id === errorMsgId)
    if (idx <= 0) return
    let userIdx = idx - 1
    while (userIdx >= 0 && messages[userIdx].role !== 'user') userIdx--
    if (userIdx < 0) return
    const userMsg = messages[userIdx]
    const userText = userMsg.blocks
      .filter(b => b.kind === 'text')
      .map(b => b.content)
      .join('\n')
    if (!userText) return
    truncateMessagesFrom(userMsg.id)
    if (activeConversationId) {
      try { await conversationsApi.truncateFrom(activeConversationId, userMsg.id) } catch { /* non-critical */ }
    }
    resend(userText, activeConversationId)
  }, [isStreaming, messages, truncateMessagesFrom, resend, activeConversationId])

  return (
    <div className="flex flex-col h-full relative">
      {/* Hide chat chrome when a full-view panel is active */}
      {!isPanelViewMode(viewMode) && (
        <>
          <ChatHeader />
          <ExecutionStatusBar />
        </>
      )}

      {/* Full-view panels — rendered outside scroll container so their headers stay fixed */}
      {viewMode === 'memory'    && <MemoryBrowseCard />}
      {viewMode === 'rag'       && <RAGBrowseCard />}
      {viewMode === 'projects'  && <ProjectsBrowseCard />}
      {viewMode === 'skills'    && <SkillsMainView />}
      {viewMode === 'agents'    && <AgentsMainView />}
      {viewMode === 'daemons'   && <DaemonsMainView />}
      {viewMode === 'analytics' && <AnalyticsPanel />}
      {viewMode === 'settings'  && <SessionPanel />}
      {viewMode === 'mcp'       && <MCPPanel />}
      {viewMode === 'hooks'     && <HooksPanel />}
      {viewMode === 'docs'      && <DocsPanel />}
      {viewMode === 'welcome'   && <WelcomePanel />}

      {/* Message area */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className={`flex-1 overflow-y-auto${isPanelViewMode(viewMode) ? ' hidden' : ''}`}
      >
        {/* Load older button */}
        {hasMoreMessages && messages.length > 0 && (
          <div className="flex justify-center pt-4 pb-2 sticky top-0 z-10">
            <button
              onClick={handleLoadOlder}
              disabled={loadingOlder}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150"
              style={{
                background: 'var(--bg-surface2)',
                border: '1px solid var(--border-mid)',
                color: 'var(--text-muted)',
                backdropFilter: 'blur(8px)',
                boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
              }}
            >
              {loadingOlder
                ? <Loader2 size={12} className="animate-spin" />
                : <ChevronUp size={12} />}
              {loadingOlder ? 'Loading…' : 'Load older messages'}
            </button>
          </div>
        )}

        <div className="px-4 py-6">
          {/* Daemon/Agent viewport: loading state */}
          {replayLoading && (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <Loader2 size={24} className="animate-spin" style={{ color: 'var(--eva-primary)' }} />
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Loading execution…</p>
            </div>
          )}

          {/* Daemon browse: panel opened, no daemon selected yet */}
          {!replayLoading && viewMode === 'daemon' && activeViewTarget === null && (
            <DaemonBrowseCard />
          )}

          {/* Daemon viewport: specific daemon selected but no runs yet */}
          {!replayLoading && viewMode === 'daemon' && activeViewTarget !== null && !activeViewTarget.executionId && messages.length === 0 && (
            <DaemonNoRunsCard name={activeViewTarget.name} />
          )}

          {/* Agent browse: panel opened, no agent selected yet */}
          {!replayLoading && viewMode === 'agent' && activeViewTarget === null && (
            <AgentBrowseCard />
          )}

          {/* Agent viewport: specific agent selected, no messages yet */}
          {!replayLoading && viewMode === 'agent' && activeViewTarget !== null && messages.length === 0 && (
            <AgentProfileCard target={activeViewTarget} />
          )}

          {/* Section 95 — a real, selected conversation whose messages failed
              to load. Distinct from EmptyState below: activeConversationId
              is set here, so this is "we couldn't load it," not "nothing's
              selected yet" — the exact confusion the original bug report was
              about (a real conversation looked like a fresh, empty chat). */}
          {!replayLoading && viewMode === 'conversation' && messages.length === 0
            && activeConversationId && messagesLoadError && (
            <div className="flex flex-col items-center justify-center gap-3 py-20 text-center px-6">
              <p style={{ color: 'var(--danger)', fontSize: 13 }}>{messagesLoadError}</p>
              <button
                onClick={() => void loadConversationMessages(activeConversationId)}
                className="btn ghost"
                style={{ fontSize: 12 }}
              >
                Retry
              </button>
            </div>
          )}

          {/* Normal conversation: empty state */}
          {!replayLoading && viewMode === 'conversation' && messages.length === 0
            && !(activeConversationId && messagesLoadError) && (
            <EmptyState onSelect={handleSuggestionSelect} />
          )}

          {/* Message list — shown in all modes once messages arrive */}
          {messages.length > 0 && !replayLoading && (
            <MessageList
              messages={messages}
              onEditSend={viewMode === 'conversation' ? handleEditSend : undefined}
              onRetry={viewMode === 'conversation' ? handleRetry : undefined}
            />
          )}

          {/* Execution footer — appears once streaming ends, for any mode with an execution */}
          {!isStreaming && !replayLoading && activeExecutionId && messages.length > 0 && (
            <ExecutionFooter executionId={activeExecutionId} />
          )}
          <div ref={bottomRef} className="h-2" />
        </div>
      </div>

      {/* Scroll-to-bottom FAB */}
      <div
        className="absolute right-5 transition-all duration-300 z-10"
        style={{
          bottom: '100px',
          opacity: !atBottom && messages.length > 0 ? 1 : 0,
          transform: !atBottom && messages.length > 0 ? 'translateY(0)' : 'translateY(8px)',
          pointerEvents: !atBottom && messages.length > 0 ? 'auto' : 'none',
        }}
      >
        <button
          onClick={scrollToBottom}
          className="w-8 h-8 rounded-full flex items-center justify-center transition-all duration-150"
          style={{
            background: 'var(--bg-surface2)',
            border: '1px solid var(--border-mid)',
            color: 'var(--text-secondary)',
            boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
          }}
        >
          <ChevronDown size={15} />
        </button>
      </div>

      {/* Hide input in browse-idle mode and all non-interactive panel views */}
      {!(
        (activeViewTarget === null && (viewMode === 'daemon' || viewMode === 'agent')) ||
        isPanelViewMode(viewMode)
      ) && (
        <MessageInput value={draft} onChange={setDraft} />
      )}
    </div>
  )
}

