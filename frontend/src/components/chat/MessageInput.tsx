import { useRef, type KeyboardEvent, type FormEvent, useEffect, useState, useCallback } from 'react'
import { Send, Square, Zap, Brain, Bot, ChevronDown, Play, Loader2, Paperclip, FileText, ImageIcon, MessageSquare, X as XIcon } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { chatApi } from '@/api/chat'
import { sessionApi } from '@/api/session'
import { conversationsApi } from '@/api/conversations'
import { daemonsApi } from '@/api/daemons'
import { attachmentsApi } from '@/api/attachments'
import { executionsApi } from '@/api/executions'
import { providersApi } from '@/api/tools'
import { useChatStore } from '@/store/chatStore'
import { useSessionStore } from '@/store/sessionStore'
import { useUiStore } from '@/store/uiStore'
import { cn } from '@/lib/utils'
import { extractErrorDetail, friendlyUploadError, isUnreachableError, parseProviderError, REASON_NOT_CONFIGURED, type ProviderProblem } from '@/lib/errors'
import { MAX_INPUT_ROWS, TEXTAREA_ROW_HEIGHT_PX } from '@/lib/constants'
import type { ConversationAttachmentResponse, ReasoningLevel } from '@/types/api'
import { REASONING_LEVELS } from '@/types/api'

interface Props {
  value:    string
  onChange: (v: string) => void
}

export default function MessageInput({ value, onChange }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const queryClient = useQueryClient()
  const [focused, setFocused] = useState(false)

  const isStreaming           = useChatStore(s => s.isStreaming)
  const stopRequested         = useChatStore(s => s.stopRequested)
  const setStopRequested      = useChatStore(s => s.setStopRequested)
  const activeConversationId  = useChatStore(s => s.activeConversationId)
  const addUserMessage        = useChatStore(s => s.addUserMessage)
  const truncateMessagesFrom  = useChatStore(s => s.truncateMessagesFrom)
  const addStreamError        = useChatStore(s => s.addStreamError)
  const setWorkerDown         = useChatStore(s => s.setWorkerDown)
  const setActiveConversation = useChatStore(s => s.setActiveConversation)
  const setActiveConvInPlace  = useChatStore(s => s.setActiveConvInPlace)
  const setActiveExecution    = useChatStore(s => s.setActiveExecution)
  const setStreaming           = useChatStore(s => s.setStreaming)
  const viewMode             = useChatStore(s => s.viewMode)
  const activeViewTarget     = useChatStore(s => s.activeViewTarget)
  const setViewDaemon        = useChatStore(s => s.setViewDaemon)
  const setViewConversation  = useChatStore(s => s.setViewConversation)
  const setProjectConvId     = useChatStore(s => s.setProjectConvId)
  const projectBrowseId      = useChatStore(s => s.projectBrowseId)
  const config                = useSessionStore(s => s.config)
  const setConfig             = useSessionStore(s => s.setConfig)
  const markClean             = useSessionStore(s => s.markClean)
  const activeProjectId       = useSessionStore(s => s.activeProjectId)
  const activeProjectName     = useSessionStore(s => s.activeProjectName)
  const setActiveProject      = useSessionStore(s => s.setActiveProject)
  const openProviderRequired  = useUiStore(s => s.openProviderRequired)
  const openProfileModal      = useUiStore(s => s.openProfileModal)

  // Daemon trigger state
  const [triggering,    setTriggering]    = useState(false)
  const [triggerError,  setTriggerError]  = useState<string | null>(null)
  const [projectError,  setProjectError]  = useState<string | null>(null)
  // Section 102 - POST /chat refused a stale provider/model (key removed, model no
  // longer usable). The message was NOT queued, so the bubble is rolled back and the
  // draft restored; this banner says why, with a shortcut to the fix.
  const [providerProblem, setProviderProblem] = useState<ProviderProblem | null>(null)
  const pendingUserMsgRef = useRef<string | null>(null)

  // System prompt quick-set
  const [promptOpen, setPromptOpen]     = useState(false)
  const [promptText, setPromptText]     = useState('')

  // Attachment state
  const attachFileRef                             = useRef<HTMLInputElement>(null)
  const attachImageRef                            = useRef<HTMLInputElement>(null)
  const [attachOpen,     setAttachOpen]           = useState(false)
  const [attachUploading, setAttachUploading]     = useState(false)
  const [attachError,    setAttachError]          = useState<string | null>(null)
  const [pickingRefType, setPickingRefType]       = useState<'conversation' | 'execution' | null>(null)

  // Per-model capabilities — needed for vision gate
  const { data: modelCaps } = useQuery({
    queryKey: ['capabilities', config.provider, config.model],
    queryFn:  () => providersApi.getCapabilities(config.provider!, config.model!),
    enabled:  !!(config.provider && config.model),
    staleTime: 5 * 60_000,
  })
  const supportsVision = modelCaps?.supports_vision ?? false

  // Linked attachments for the active conversation
  const { data: linkedAttachments = [], refetch: refetchAttachments } = useQuery<ConversationAttachmentResponse[]>({
    queryKey: ['conversation-attachments', activeConversationId],
    queryFn:  () => attachmentsApi.getConversationAttachments(activeConversationId!),
    enabled:  !!activeConversationId,
  })

  // Executions for ref picker
  const { data: executions = [] } = useQuery({
    queryKey: ['executions-pick'],
    queryFn:  () => executionsApi.list({ limit: 30 }),
    enabled:  pickingRefType === 'execution',
    staleTime: 30_000,
  })

  // Conversations for ref picker — live query, enabled only when picker is open
  // Falls back to whatever is already in cache so the list appears instantly
  const { data: pickerConversations = [] } = useQuery({
    queryKey:  ['conversations'],
    queryFn:   conversationsApi.list,
    enabled:   pickingRefType === 'conversation',
    staleTime: 30_000,
  })

  // Upload a file/image to library then link to conversation
  const handleAttachFile = async (file: File, type: 'file' | 'image') => {
    if (type === 'image' && !supportsVision) {
      setAttachError('Current model does not support vision/images')
      setTimeout(() => setAttachError(null), 4000)
      return
    }
    setAttachUploading(true)
    setAttachError(null)
    try {
      const att = await attachmentsApi.upload(file, activeProjectId ?? undefined)
      if (activeConversationId) {
        await attachmentsApi.linkToConversation(activeConversationId, { attachment_id: att.attachment_id })
        refetchAttachments()
      }
      setAttachOpen(false)
    } catch (err: unknown) {
      // client.ts flattens every error to { status, detail } - there is no `.response`, so
      // reading e.response.status here meant the 422/409 messages never showed.
      const status = (err as { status?: number } | null)?.status
      if (status === 422) {
        setAttachError(friendlyUploadError(extractErrorDetail(err, 'File type not supported by this model')))
      } else if (status === 409) {
        setAttachError('Already attached')
      } else {
        setAttachError('Upload failed — try again')
      }
      setTimeout(() => setAttachError(null), 4000)
    } finally {
      setAttachUploading(false)
    }
  }

  // Link a conversation reference
  const handleAttachConvRef = async (refConvId: string) => {
    if (!activeConversationId) return
    try {
      await attachmentsApi.linkToConversation(activeConversationId, { conversation_ref_id: refConvId })
      refetchAttachments()
    } catch {
      setAttachError('Failed to link conversation')
      setTimeout(() => setAttachError(null), 3000)
    } finally {
      setPickingRefType(null)
      setAttachOpen(false)
    }
  }

  // Link an execution reference
  const handleAttachExecRef = async (refExecId: string) => {
    if (!activeConversationId) return
    try {
      await attachmentsApi.linkToConversation(activeConversationId, { execution_ref_id: refExecId })
      refetchAttachments()
    } catch {
      setAttachError('Failed to link execution')
      setTimeout(() => setAttachError(null), 3000)
    } finally {
      setPickingRefType(null)
      setAttachOpen(false)
    }
  }

  // Unlink an attachment from the conversation
  const handleUnlink = async (caId: string) => {
    if (!activeConversationId) return
    try {
      await attachmentsApi.unlinkFromConversation(activeConversationId, caId)
      refetchAttachments()
    } catch { /* silent */ }
  }

  // Close attach popup when clicking outside
  const attachPopupRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!attachOpen) return
    const handler = (e: MouseEvent) => {
      if (attachPopupRef.current && !attachPopupRef.current.contains(e.target as Node)) {
        setAttachOpen(false)
        setPickingRefType(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [attachOpen])

  // Helper: look up current conversation's system_prompt from cache
  const getActiveSystemPrompt = useCallback((): string => {
    const convs = queryClient.getQueryData<{ id: string; system_prompt?: string | null }[]>(['conversations'])
    const conv  = convs?.find(c => c.id === activeConversationId)
    return conv?.system_prompt ?? ''
  }, [queryClient, activeConversationId])

  // When conversation changes: sync promptText; open panel for new chats, close for existing
  useEffect(() => {
    if (activeConversationId) {
      setPromptOpen(false)
      setPromptText(getActiveSystemPrompt())
    } else {
      // New conversation — open panel so user can set a prompt before the first message
      setPromptOpen(true)
      setPromptText('')
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConversationId])

  // Reasoning — same rules as ReasoningControl in settings panels
  const capsSupportsReasoning = modelCaps?.supports_reasoning           // undefined=loading | true | false
  const capsCanDisable        = modelCaps?.reasoning_can_disable        // undefined=loading | true | false | null
  const pickerLevels: ReasoningLevel[] = (modelCaps?.reasoning_levels?.length
    ? modelCaps.reasoning_levels as ReasoningLevel[]
    : REASONING_LEVELS)
  const showOff              = capsCanDisable !== false                 // hide Off when model can't disable
  const reasoningLevel       = config.reasoning ?? null
  const effectiveReasoningOn = capsCanDisable === false ? true : reasoningLevel != null

  const [togglingR,   setTogglingR]   = useState(false)
  const [rPickerOpen, setRPickerOpen] = useState(false)
  const rPickerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!rPickerOpen) return
    const handler = (e: MouseEvent) => {
      if (rPickerRef.current && !rPickerRef.current.contains(e.target as Node)) {
        setRPickerOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [rPickerOpen])

  const handleReasoningSelect = async (level: ReasoningLevel | null) => {
    if (togglingR) return
    setRPickerOpen(false)
    const prev = config.reasoning
    setConfig({ reasoning: level })
    setTogglingR(true)
    try {
      const data = await sessionApi.setSession({ ...config, reasoning: level })
      // Sync back if backend snapped the level (e.g. model doesn't support the requested level)
      if (data.reasoning !== level) setConfig({ reasoning: data.reasoning ?? null })
      markClean()
    } catch {
      setConfig({ reasoning: prev })
    } finally {
      setTogglingR(false)
    }
  }

  // When value is set externally (suggestion chip click), focus + resize the textarea
  useEffect(() => {
    if (value && textareaRef.current) {
      textareaRef.current.focus()
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height =
        Math.min(textareaRef.current.scrollHeight, TEXTAREA_ROW_HEIGHT_PX * MAX_INPUT_ROWS) + 'px'
    }
  }, [value])

  const { mutate: send } = useMutation({
    // Accept message as parameter — do NOT close over `value` (it may have been
    // cleared by onChange('') before mutationFn executes)
    mutationFn: (msgText: string) => chatApi.send({
      message:         msgText,
      conversation_id: activeConversationId ?? undefined,
      // In project browse mode, link to the browsed project (not the session-level project)
      project_id:      viewMode === 'projects'
                         ? (projectBrowseId ?? undefined)
                         : (activeProjectId ?? undefined),
    }),
    onSuccess: (data) => {
      if (import.meta.env.DEV) console.debug('[chat] POST /chat response →', data)
      if (viewMode === 'projects') {
        // Stay in project mode — update IDs without switching viewMode to 'conversation'
        setActiveConvInPlace(data.conversation_id)
        setActiveExecution(data.execution_id)
        setProjectConvId(data.conversation_id, null)
      } else {
        setActiveConversation(data.conversation_id)
        setActiveExecution(data.execution_id)
      }
      setStreaming(true)
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      // Save system prompt if set — works for both new convs and existing ones
      if (promptText.trim()) {
        conversationsApi.setSystemPrompt(data.conversation_id, promptText.trim())
          .then(() => {
            setPromptText('')
            setPromptOpen(false)
          })
          .catch(() => { /* non-critical — silent fail */ })
      }
    },
    onError: (err, msgText) => {
      if (import.meta.env.DEV) console.error('[chat] POST /chat failed →', err)
      const problem = parseProviderError(err)
      if (problem) {
        if (pendingUserMsgRef.current) truncateMessagesFrom(pendingUserMsgRef.current)
        pendingUserMsgRef.current = null
        onChange(msgText)
        setProviderProblem(problem)
        return
      }
      const apiErr = err as { status?: number; detail?: string | { error_code?: string } }
      if (apiErr.status === 403) {
        setProjectError('Project is paused or archived — new conversations can\'t be linked. Resume the project or clear it.')
        setActiveProject(null, null)
        return
      }
      // Reactive fallback for the proactive check in handleSubmit below — covers
      // the race where a provider was configured at page-load but removed/
      // invalidated server-side (e.g. another tab) between then and send-time.
      if (apiErr.status === 424 && typeof apiErr.detail === 'object' && apiErr.detail?.error_code === 'provider_not_configured') {
        openProviderRequired()
        return
      }
      // Section 95 — everything else previously did nothing at all: the
      // user's message was already shown (optimistic addUserMessage above),
      // then the turn just silently went nowhere. Reuses the existing
      // ErrorBlock + Retry affordance (ChatView.tsx's handleRetry already
      // walks back to the preceding user message and resends it) rather than
      // a new "failed" flag on the bubble itself — same visible result
      // (an error right under the message that failed, with a working
      // retry), no new UI. Unreachable also raises the shared WorkerDownBanner;
      // a real error response (400/422/500/...) doesn't — the server clearly
      // is running, so "check that the backend service is running" would be wrong.
      if (isUnreachableError(err)) {
        setWorkerDown(true)
        addStreamError("Could not reach the server. Check your connection and try again.", 'network')
      } else {
        addStreamError(extractErrorDetail(err, 'Something went wrong sending this message.'), 'send_failed')
      }
    },
  })

  // /cancel is fire-and-forget on the backend (sets a flag the worker checks on its
  // next cycle) — it does NOT mean the run has actually stopped. Flipping isStreaming
  // here would be a lie: the assistant message, token counts, and duration all keep
  // updating for however long the in-flight tool call actually takes to unwind. Show
  // "Stopping…" instead and let the real terminal SSE event (done/cancelled, handled
  // in useSSE.ts via setStreaming) end the stream.
  const { mutate: cancel } = useMutation({
    mutationFn: () => chatApi.cancel(activeConversationId!),
    onSuccess: () => setStopRequested(true),
    onError:   () => setStopRequested(false),
  })

  // ── Daemon trigger ────────────────────────────────────────────────────────
  const handleDaemonTrigger = async (e?: FormEvent) => {
    e?.preventDefault()
    if (!activeViewTarget || triggering || isStreaming) return
    if (!config.provider) { openProviderRequired(); return }
    const contextMsg = value.trim() || undefined
    setTriggering(true)
    setTriggerError(null)
    try {
      const { execution_id } = await daemonsApi.trigger(activeViewTarget.name, contextMsg)
      onChange('')
      if (textareaRef.current) textareaRef.current.style.height = 'auto'
      // Switch to this new execution — useExecutionReplay will auto-connect
      setViewDaemon(activeViewTarget.name, execution_id)
    } catch {
      setTriggerError('Trigger failed — check daemon is enabled')
      setTimeout(() => setTriggerError(null), 4000)
    } finally {
      setTriggering(false)
    }
  }

  // ── Conversation submit ───────────────────────────────────────────────────
  const handleSubmit = (e?: FormEvent) => {
    e?.preventDefault()
    // In agent mode: switch to conversation first, pre-fill system prompt from agent
    if (viewMode === 'agent') {
      const agentPrompt = activeViewTarget?.systemPrompt
      if (agentPrompt && !promptText) setPromptText(agentPrompt)
      setViewConversation()
      // Fall through — the send below will create a new conversation
    }
    const trimmed = value.trim()
    if (!trimmed || isStreaming) return
    if (!config.provider) { openProviderRequired(); return }
    setProviderProblem(null)
    pendingUserMsgRef.current = addUserMessage(trimmed)
    onChange('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    send(trimmed)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleInput = () => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, TEXTAREA_ROW_HEIGHT_PX * MAX_INPUT_ROWS) + 'px'
    onChange(ta.value)
  }

  const isDaemonMode = viewMode === 'daemon'
  const isAgentMode  = viewMode === 'agent'

  const canSend  = value.trim().length > 0 && !isStreaming
  // Daemon trigger can fire even with empty context (optional)
  const canTrigger = !triggering && !isStreaming
  const tokenEst = value.length > 24 ? Math.ceil(value.length / 4) : null

  // Border/glow state
  const borderColor = isStreaming
    ? 'rgba(0,201,255,0.35)'
    : focused
      ? 'var(--border-mid)'
      : 'var(--border-dim)'

  const boxShadow = isStreaming
    ? '0 0 0 1px rgba(0,201,255,0.12), 0 0 40px rgba(0,201,255,0.08)'
    : focused
      ? '0 0 0 1px rgba(0,201,255,0.06), 0 0 20px rgba(0,201,255,0.04)'
      : 'none'

  return (
    <div className="shrink-0 px-4 pb-4 pt-1.5">
      <form
        onSubmit={isDaemonMode ? handleDaemonTrigger : handleSubmit}
        className="rounded-2xl transition-all duration-200"
        style={{
          background: 'var(--bg-surface1)',
          border: `1px solid ${borderColor}`,
          boxShadow,
        }}
      >
        {/* Streaming indicator bar */}
        {isStreaming && (
          <div
            className="h-0.5 rounded-t-2xl overflow-hidden"
            style={{ background: 'linear-gradient(90deg, transparent, var(--eva-primary), transparent)' }}
          >
            <div className="streaming-progress-bar" />
          </div>
        )}

        {/* Attachments bar — shown when conversation has linked items */}
        {!isDaemonMode && linkedAttachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-4 pt-2.5 pb-0">
            {linkedAttachments.map(ca => {
              const icon =
                ca.attachment_type === 'image'            ? <ImageIcon size={9} />
                : ca.attachment_type === 'conversation_ref' ? <MessageSquare size={9} />
                : ca.attachment_type === 'execution_ref'    ? <Zap size={9} />
                : <FileText size={9} />
              const label =
                ca.attachment_type === 'conversation_ref' ? `conv: ${ca.ref_conversation_id?.slice(0, 8)}`
                : ca.attachment_type === 'execution_ref'  ? `exec: ${ca.ref_execution_id?.slice(0, 8)}`
                : ca.attachment_id?.slice(0, 8) ?? 'file'
              return (
                <span
                  key={ca.conversation_attachment_id}
                  className="flex items-center gap-1 pl-1.5 pr-1 py-0.5 rounded-full text-xs"
                  style={{
                    background: 'rgba(0,201,255,0.08)',
                    border:     '1px solid rgba(0,201,255,0.2)',
                    color:      'var(--eva-primary)',
                    fontSize:   10,
                  }}
                >
                  {icon}
                  <span className="font-mono">{label}</span>
                  <button
                    type="button"
                    onClick={() => handleUnlink(ca.conversation_attachment_id)}
                    className="ml-0.5 rounded-full w-3.5 h-3.5 flex items-center justify-center transition-colors"
                    style={{ color: 'var(--eva-primary)' }}
                    onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
                    onMouseLeave={e => (e.currentTarget.style.color = 'var(--eva-primary)')}
                    title="Remove attachment"
                  >
                    <XIcon size={8} />
                  </button>
                </span>
              )
            })}
          </div>
        )}

        {/* Textarea row — attach button left, textarea centre, send right */}
        <div className="flex items-end gap-2 px-4 pt-3 pb-2">

          {/* Attach button + popup — hidden in daemon mode, sits LEFT of textarea */}
          {!isDaemonMode && (
            <div className="relative shrink-0 mb-0.5" ref={attachPopupRef}>
              <button
                type="button"
                onClick={() => { setAttachOpen(o => !o); setPickingRefType(null) }}
                title="Attach file, image, or reference"
                className="w-8 h-8 rounded-xl flex items-center justify-center transition-all duration-200"
                style={{
                  background: attachOpen || linkedAttachments.length > 0
                    ? 'rgba(0,201,255,0.1)' : 'transparent',
                  border: attachOpen || linkedAttachments.length > 0
                    ? '1px solid rgba(0,201,255,0.25)' : '1px solid transparent',
                  color: attachOpen || linkedAttachments.length > 0
                    ? 'var(--eva-primary)' : 'var(--text-muted)',
                }}
                onMouseEnter={e => { if (!attachOpen) e.currentTarget.style.color = 'var(--eva-primary)' }}
                onMouseLeave={e => { if (!attachOpen && !linkedAttachments.length) e.currentTarget.style.color = 'var(--text-muted)' }}
              >
                <Paperclip size={14} />
              </button>

              {/* Attach popup */}
              {attachOpen && (
                <div
                  className="absolute bottom-full mb-2 left-0 rounded-xl overflow-hidden z-50"
                  style={{
                    background: 'var(--bg-surface2)',
                    border:     '1px solid var(--border-mid)',
                    boxShadow:  '0 8px 32px rgba(0,0,0,0.4)',
                    minWidth:   220,
                  }}
                >
                  {/* Header */}
                  <div
                    className="flex items-center justify-between px-3 py-2"
                    style={{ borderBottom: '1px solid var(--border-dim)' }}
                  >
                    <span className="font-semibold uppercase" style={{ color: 'var(--text-muted)', fontSize: 10, letterSpacing: '0.08em' }}>
                      Attach context
                    </span>
                    <button type="button" onClick={() => { setAttachOpen(false); setPickingRefType(null) }}
                      style={{ color: 'var(--text-muted)' }}
                    >
                      <XIcon size={11} />
                    </button>
                  </div>

                  {/* Error */}
                  {attachError && (
                    <div className="px-3 py-2 text-xs" style={{ color: 'var(--danger)', background: 'rgba(255,77,109,0.07)' }}>
                      {attachError}
                    </div>
                  )}

                  {/* Uploading state */}
                  {attachUploading ? (
                    <div className="flex items-center gap-2 px-3 py-4 text-xs" style={{ color: 'var(--text-muted)' }}>
                      <Loader2 size={12} className="animate-spin" /> Uploading…
                    </div>
                  ) : pickingRefType === null ? (
                    /* Main options */
                    <div className="flex flex-col py-1">
                      <button
                        type="button"
                        onClick={() => attachFileRef.current?.click()}
                        className="flex items-center gap-2.5 px-3 py-2.5 text-xs transition-colors w-full text-left"
                        style={{ color: 'var(--text-primary)' }}
                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-surface3)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                      >
                        <FileText size={13} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
                        <div>
                          <div className="font-medium">Upload File</div>
                          <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>PDF, DOCX, TXT, MD</div>
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (!supportsVision) {
                            setAttachError('Current model does not support images')
                            setTimeout(() => setAttachError(null), 3000)
                          } else {
                            attachImageRef.current?.click()
                          }
                        }}
                        className="flex items-center gap-2.5 px-3 py-2.5 text-xs transition-colors w-full text-left"
                        style={{ color: supportsVision ? 'var(--text-primary)' : 'var(--text-muted)', opacity: supportsVision ? 1 : 0.6 }}
                        onMouseEnter={e => { if (supportsVision) e.currentTarget.style.background = 'var(--bg-surface3)' }}
                        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                      >
                        <ImageIcon size={13} style={{ color: supportsVision ? 'var(--neural)' : 'var(--text-muted)', flexShrink: 0 }} />
                        <div>
                          <div className="font-medium">Upload Image</div>
                          <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>
                            {supportsVision ? 'PNG, JPG, WebP, GIF' : 'Not supported by current model'}
                          </div>
                        </div>
                      </button>
                      {activeConversationId && (<>
                      <div style={{ height: 1, background: 'var(--border-dim)', margin: '4px 12px' }} />
                      <button
                        type="button"
                        onClick={() => setPickingRefType('conversation')}
                        className="flex items-center gap-2.5 px-3 py-2.5 text-xs transition-colors w-full text-left"
                        style={{ color: 'var(--text-primary)' }}
                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-surface3)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                      >
                        <MessageSquare size={13} style={{ color: 'var(--amber)', flexShrink: 0 }} />
                        <div>
                          <div className="font-medium">Reference Conversation</div>
                          <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>Inject past chat as context</div>
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() => setPickingRefType('execution')}
                        className="flex items-center gap-2.5 px-3 py-2.5 text-xs transition-colors w-full text-left"
                        style={{ color: 'var(--text-primary)' }}
                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-surface3)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                      >
                        <Zap size={13} style={{ color: 'var(--success)', flexShrink: 0 }} />
                        <div>
                          <div className="font-medium">Reference Execution</div>
                          <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>Inject run history as context</div>
                        </div>
                      </button>
                      </>)}
                    </div>
                  ) : pickingRefType === 'conversation' ? (
                    /* Conversation picker */
                    <div className="flex flex-col">
                      <button
                        type="button"
                        onClick={() => setPickingRefType(null)}
                        className="flex items-center gap-1.5 px-3 py-2 text-xs"
                        style={{ color: 'var(--text-muted)', borderBottom: '1px solid var(--border-dim)' }}
                      >
                        ← Back
                      </button>
                      <div className="max-h-48 overflow-y-auto py-1">
                        {pickerConversations.filter(c => c.id !== activeConversationId).length === 0 ? (
                          <p className="px-3 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>No conversations found</p>
                        ) : pickerConversations.filter(c => c.id !== activeConversationId).map(c => (
                          <button
                            key={c.id}
                            type="button"
                            onClick={() => handleAttachConvRef(c.id)}
                            className="w-full text-left flex items-center gap-2 px-3 py-2 text-xs transition-colors"
                            style={{ color: 'var(--text-primary)' }}
                            onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-surface3)')}
                            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                          >
                            <MessageSquare size={10} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                            <span className="truncate">{c.title ?? c.id}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    /* Execution picker */
                    <div className="flex flex-col">
                      <button
                        type="button"
                        onClick={() => setPickingRefType(null)}
                        className="flex items-center gap-1.5 px-3 py-2 text-xs"
                        style={{ color: 'var(--text-muted)', borderBottom: '1px solid var(--border-dim)' }}
                      >
                        ← Back
                      </button>
                      <div className="max-h-48 overflow-y-auto py-1">
                        {executions.length === 0 ? (
                          <p className="px-3 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>No executions found</p>
                        ) : executions.map(ex => (
                          <button
                            key={ex.execution_id}
                            type="button"
                            onClick={() => handleAttachExecRef(ex.execution_id)}
                            className="w-full text-left flex items-center gap-2 px-3 py-2 text-xs transition-colors"
                            style={{ color: 'var(--text-primary)' }}
                            onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-surface3)')}
                            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                          >
                            <Zap size={10} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                            <span className="font-mono truncate">{ex.execution_id}</span>
                            <span className="ml-auto shrink-0" style={{ color: 'var(--text-muted)', fontSize: 9 }}>{ex.status}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Hidden file inputs */}
              <input
                ref={attachFileRef}
                type="file"
                className="hidden"
                accept=".pdf,.docx,.txt,.md,.csv,.json,.xml,.yaml,.yml"
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) handleAttachFile(f, 'file')
                  e.target.value = ''
                }}
              />
              <input
                ref={attachImageRef}
                type="file"
                className="hidden"
                accept="image/png,image/jpeg,image/webp,image/gif"
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) handleAttachFile(f, 'image')
                  e.target.value = ''
                }}
              />
            </div>
          )}

          {/* Textarea */}
          <textarea
            ref={textareaRef}
            value={value}
            onInput={handleInput}
            onChange={e => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={
              isStreaming ? 'EVA is responding…'
              : isDaemonMode ? 'Add context for this trigger… (optional, ↵ to trigger)'
              : isAgentMode  ? `Chat using ${activeViewTarget?.name ?? 'this agent'}'s config… (↵ send)`
              : 'Ask EVA anything…  (↵ send · Shift+↵ newline)'
            }
            rows={1}
            disabled={isStreaming}
            className={cn(
              'flex-1 resize-none outline-none text-sm leading-6 bg-transparent py-1',
              'placeholder:text-[var(--text-muted)]',
              isStreaming && 'opacity-50 cursor-default'
            )}
            style={{
              color: 'var(--text-primary)',
              maxHeight: `${TEXTAREA_ROW_HEIGHT_PX * MAX_INPUT_ROWS}px`,
              fontFamily: 'Inter, system-ui, sans-serif',
            }}
          />

          {/* Action button */}
          <div className="shrink-0 mb-0.5">
            {isStreaming ? (
              <button
                type="button"
                onClick={() => !stopRequested && cancel()}
                disabled={stopRequested}
                title={stopRequested ? 'Waiting for the backend to confirm the run has stopped' : 'Stop generating'}
                className="flex items-center gap-1.5 px-3 h-8 rounded-xl text-xs font-semibold transition-all duration-150"
                style={{
                  background: 'var(--danger-dim)',
                  border: '1px solid rgba(255,77,109,0.3)',
                  color: 'var(--danger)',
                  opacity: stopRequested ? 0.7 : 1,
                  cursor: stopRequested ? 'default' : 'pointer',
                }}
              >
                {stopRequested
                  ? <Loader2 size={11} className="animate-spin" />
                  : <Square size={11} fill="currentColor" />}
                {stopRequested ? 'Stopping…' : 'Stop'}
              </button>
            ) : isDaemonMode ? (
              /* Daemon trigger button */
              <button
                type="submit"
                disabled={!canTrigger}
                title={`Trigger ${activeViewTarget?.name ?? 'daemon'}`}
                className="flex items-center gap-1.5 px-3 h-9 rounded-xl text-xs font-semibold transition-all duration-200"
                style={{
                  background: canTrigger
                    ? 'linear-gradient(135deg, rgba(245,158,11,0.9) 0%, rgba(217,119,6,0.9) 100%)'
                    : 'var(--bg-surface2)',
                  border: `1px solid ${canTrigger ? 'rgba(245,158,11,0.5)' : 'var(--border-dim)'}`,
                  color: canTrigger ? '#04080E' : 'var(--text-muted)',
                  boxShadow: canTrigger ? '0 0 18px rgba(245,158,11,0.3)' : 'none',
                  cursor: canTrigger ? 'pointer' : 'not-allowed',
                }}
              >
                {triggering
                  ? <Loader2 size={12} className="animate-spin" />
                  : <Play size={12} fill="currentColor" />}
                {triggering ? 'Triggering…' : 'Trigger'}
              </button>
            ) : (
              /* Normal send button */
              <button
                type="submit"
                disabled={!canSend}
                title="Send message"
                className="w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-200"
                style={{
                  background: canSend
                    ? 'linear-gradient(135deg, #00C9FF 0%, #0088BB 100%)'
                    : 'var(--bg-surface2)',
                  border: `1px solid ${canSend ? 'rgba(0,201,255,0.45)' : 'var(--border-dim)'}`,
                  color: canSend ? '#04080E' : 'var(--text-muted)',
                  boxShadow: canSend ? '0 0 18px rgba(0,201,255,0.35)' : 'none',
                  cursor: canSend ? 'pointer' : 'not-allowed',
                  transform: canSend ? 'scale(1)' : 'scale(0.92)',
                }}
              >
                <Send size={15} strokeWidth={2.5} />
              </button>
            )}
          </div>
        </div>

        {/* Trigger error toast */}
        {isDaemonMode && triggerError && (
          <div
            className="mx-4 mb-2 flex items-center gap-2 px-3 py-2 rounded-xl text-xs"
            style={{ background: 'rgba(255,77,109,0.08)', border: '1px solid rgba(255,77,109,0.25)', color: 'var(--danger)' }}
          >
            {triggerError}
          </div>
        )}

        {/* Section 102 - stale provider/model rejected by POST /chat */}
        {providerProblem && (
          <div
            className="mx-4 mb-2 flex items-center justify-between gap-3 px-3 py-2 rounded-xl text-xs"
            style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', color: 'var(--amber)' }}
          >
            <span>{providerProblem.message} Your message was not sent.</span>
            <span className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => openProfileModal(providerProblem.reason === REASON_NOT_CONFIGURED ? 'account' : 'providers')}
                style={{ color: 'var(--eva-primary)', fontWeight: 600, textDecoration: 'underline' }}
              >
                {providerProblem.reason === REASON_NOT_CONFIGURED ? 'Open API Keys' : 'Choose provider'}
              </button>
              <button onClick={() => setProviderProblem(null)} style={{ color: 'var(--amber)' }}>✕</button>
            </span>
          </div>
        )}

        {/* Project 403 error */}
        {projectError && (
          <div
            className="mx-4 mb-2 flex items-center justify-between gap-2 px-3 py-2 rounded-xl text-xs"
            style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', color: 'var(--amber)' }}
          >
            <span>{projectError}</span>
            <button onClick={() => setProjectError(null)} style={{ color: 'var(--amber)', flexShrink: 0 }}>✕</button>
          </div>
        )}

        {/* System prompt quick-set panel — hidden in daemon mode */}
        {!isDaemonMode && promptOpen && (
          <div
            className="mx-4 mb-2 rounded-xl overflow-hidden"
            style={{ border: '1px solid rgba(124,108,243,0.3)', background: 'rgba(124,108,243,0.05)' }}
          >
            <div
              className="flex items-center justify-between px-3 py-1.5"
              style={{ borderBottom: '1px solid rgba(124,108,243,0.15)' }}
            >
              <div className="flex items-center gap-1.5">
                <Bot size={11} style={{ color: 'var(--neural)' }} />
                <span className="text-xs font-medium" style={{ color: 'var(--neural)' }}>
                  System Prompt
                </span>
              </div>
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {promptText.length}/2000
              </span>
            </div>
            <textarea
              value={promptText}
              onChange={e => setPromptText(e.target.value.slice(0, 2000))}
              placeholder="Set instructions or a persona for this conversation… (optional)"
              rows={3}
              className="w-full resize-none outline-none text-xs leading-relaxed px-3 py-2.5 bg-transparent"
              style={{ color: 'var(--text-primary)', fontFamily: 'JetBrains Mono, monospace' }}
            />
          </div>
        )}

        {/* Footer: model indicator + token estimate */}
        <div
          className="flex items-center justify-between px-4 pb-2.5"
          style={{ borderTop: '1px solid var(--border-dim)' }}
        >
          <div className="flex items-center gap-2 min-w-0">
            {activeProjectName && (
              <span
                className="text-xs px-1.5 py-0.5 rounded-full font-mono truncate max-w-[65px] sm:max-w-[100px]"
                style={{
                  fontSize: 9, fontWeight: 600,
                  background: 'rgba(0,201,255,0.08)',
                  color: 'var(--eva-primary)',
                  border: '1px solid rgba(0,201,255,0.2)',
                }}
                title={`Project: ${activeProjectName}`}
              >
                {activeProjectName}
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            {tokenEst && (
              <span className="text-xs" style={{ color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
                ~{tokenEst.toLocaleString()} tokens
              </span>
            )}

            {/* Reasoning picker — hidden when model explicitly doesn't support reasoning (mirrors ReasoningControl) */}
            {!isDaemonMode && capsSupportsReasoning !== false && (
              <div ref={rPickerRef} style={{ position: 'relative' }}>
                <button
                  type="button"
                  onClick={() => { if (!togglingR && !isStreaming) setRPickerOpen(p => !p) }}
                  disabled={togglingR || isStreaming}
                  title={effectiveReasoningOn ? 'Reasoning active — click to change level' : 'Reasoning off — click to enable'}
                  className="flex items-center gap-1.5 px-2 py-0.5 rounded-lg transition-all duration-200"
                  style={{
                    background: effectiveReasoningOn ? 'rgba(124,108,243,0.12)' : 'transparent',
                    border:     effectiveReasoningOn ? '1px solid rgba(124,108,243,0.25)' : '1px solid transparent',
                    color:      effectiveReasoningOn ? 'var(--neural)' : 'var(--text-muted)',
                    opacity:    (togglingR || isStreaming) ? 0.5 : 1,
                    cursor:     (togglingR || isStreaming) ? 'default' : 'pointer',
                  }}
                >
                  <Brain size={11} className={togglingR ? 'animate-pulse' : ''} />
                  <span className="text-xs input-btn-label">
                    {reasoningLevel ? reasoningLevel.charAt(0).toUpperCase() + reasoningLevel.slice(1) : 'Reasoning'}
                  </span>
                </button>

                {rPickerOpen && (
                  <div style={{
                    position: 'absolute', bottom: 'calc(100% + 6px)', left: '50%',
                    transform: 'translateX(-50%)',
                    background: 'var(--bg-surface2)',
                    border: '1px solid var(--border-dim)',
                    borderRadius: 10, padding: 5, display: 'flex', flexDirection: 'column', gap: 2,
                    boxShadow: '0 4px 20px rgba(0,0,0,0.4)',
                    zIndex: 200, minWidth: 100,
                  }}>
                    {/* Off only shown when model allows disabling — same rule as ReasoningControl.showOff */}
                    {showOff && (
                      <button
                        type="button"
                        onClick={() => handleReasoningSelect(null)}
                        style={{
                          padding: '4px 10px', border: 'none', borderRadius: 6,
                          fontSize: 11.5, fontWeight: reasoningLevel === null ? 600 : 400, textAlign: 'left',
                          background: reasoningLevel === null ? 'var(--neural)' : 'transparent',
                          color: reasoningLevel === null ? '#fff' : 'var(--text-secondary)',
                          cursor: 'pointer',
                        }}
                      >Off</button>
                    )}
                    {pickerLevels.map(lvl => {
                      const active = reasoningLevel === lvl
                      return (
                        <button
                          key={lvl}
                          type="button"
                          onClick={() => handleReasoningSelect(lvl)}
                          style={{
                            padding: '4px 10px', border: 'none', borderRadius: 6,
                            fontSize: 11.5, fontWeight: active ? 600 : 400, textAlign: 'left',
                            background: active ? 'var(--neural)' : 'transparent',
                            color: active ? '#fff' : 'var(--text-secondary)',
                            cursor: 'pointer',
                          }}
                        >
                          {lvl.charAt(0).toUpperCase() + lvl.slice(1)}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )}

            {/* System prompt quick-toggle — hidden in daemon mode */}
            {!isDaemonMode && (
              <button
                type="button"
                onClick={() => {
                  const next = !promptOpen
                  if (next && !promptText) {
                    const stored = getActiveSystemPrompt()
                    if (stored) setPromptText(stored)
                  }
                  setPromptOpen(next)
                }}
                title={promptOpen ? 'Hide system prompt' : 'Set system prompt for this conversation'}
                className="flex items-center gap-1.5 px-2 py-0.5 rounded-lg transition-all duration-200"
                style={{
                  background: (promptOpen || promptText) ? 'rgba(124,108,243,0.12)' : 'transparent',
                  border:     (promptOpen || promptText) ? '1px solid rgba(124,108,243,0.25)' : '1px solid transparent',
                  color:      (promptOpen || promptText) ? 'var(--neural)' : 'var(--text-muted)',
                }}
              >
                <Bot size={11} />
                <span className="text-xs input-btn-label">System Prompt</span>
                <ChevronDown
                  size={9}
                  style={{
                    transform: promptOpen ? 'rotate(180deg)' : 'rotate(0)',
                    transition: 'transform 0.15s',
                  }}
                />
              </button>
            )}

            <span className="text-xs" style={{ color: 'var(--border-mid)' }}>
              {isDaemonMode ? '↵ trigger' : '↵ send'}
            </span>
          </div>
        </div>
      </form>

      <p className="text-center mt-1.5" style={{ color: 'var(--text-muted)', fontSize: 11 }}>
        EVA may make mistakes · Always verify critical information
      </p>
    </div>
  )
}
