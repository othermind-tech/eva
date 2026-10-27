import { useState, useRef, useEffect } from 'react'
import type { ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useChatStore } from '@/store/chatStore'
import { useSessionStore } from '@/store/sessionStore'
import { conversationsApi, exportConversation, type ExportFormat } from '@/api/conversations'
import { projectsApi } from '@/api/projects'
import { ExecutionBadge } from './ExecutionBadge'
import { ModesButton } from './ModesButton'
import { useUiStore } from '@/store/uiStore'
import {
  Sparkles, ChevronDown, Download, Loader2, FileJson, FileText,
  Clock, Bot, Brain, FolderOpen,
  Pencil, Check, X, MoreHorizontal, Trash2, Copy, FolderPlus,
} from 'lucide-react'
import { truncate } from '@/lib/utils'

export default function ChatHeader() {
  const activeConversationId = useChatStore(s => s.activeConversationId)
  const isStreaming           = useChatStore(s => s.isStreaming)
  const viewMode             = useChatStore(s => s.viewMode)
  const activeViewTarget     = useChatStore(s => s.activeViewTarget)
  const config               = useSessionStore(s => s.config)
  const setConfig            = useSessionStore(s => s.setConfig)
  const openProfileModal     = useUiStore(s => s.openProfileModal)

  // ─── Export state ─────────────────────────────────────────────────────────
  const [exportOpen,   setExportOpen]   = useState(false)
  const [exportingFmt, setExportingFmt] = useState<ExportFormat | null>(null)
  const [exportError,  setExportError]  = useState<string | null>(null)
  const exportRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!exportOpen) return
    const handler = (e: MouseEvent) => {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) setExportOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [exportOpen])

  // ─── Model info popover ───────────────────────────────────────────────────
  const [modelOpen, setModelOpen] = useState(false)
  const modelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!modelOpen) return
    const handler = (e: MouseEvent) => {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) setModelOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [modelOpen])

  // ─── More-menu popover ────────────────────────────────────────────────────
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!moreOpen) return
    const handler = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setMoreOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [moreOpen])

  // ─── Inline title edit ────────────────────────────────────────────────────
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft,   setTitleDraft]   = useState('')
  const [savingTitle,  setSavingTitle]  = useState(false)
  const titleInputRef = useRef<HTMLInputElement>(null)
  const queryClient   = useQueryClient()

  // ─── Data queries ─────────────────────────────────────────────────────────
  const { data: conversation } = useQuery({
    queryKey: ['conversation', activeConversationId],
    queryFn:  () => conversationsApi.get(activeConversationId!),
    enabled:  !!activeConversationId && viewMode === 'conversation',
  })

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn:  projectsApi.list,
    staleTime: 60_000,
    enabled: viewMode === 'conversation',
  })

  const projectName = conversation?.project_id
    ? projects.find(p => p.id === conversation.project_id)?.name
    : undefined

  // ─── Model label ──────────────────────────────────────────────────────────
  const modelLabel = config.model
    ? `${config.provider ?? ''} / ${config.model}`.replace(/^\/\s*/, '')
    : config.provider ?? 'No model'

  // Short form for chip display: just the model name
  const modelShort = config.model
    ? config.model.replace(/^(claude-|gpt-|gemini-)/, '').replace(/-\d{4}-\d{2}-\d{2}$/, '')
    : (config.provider ?? 'No model')

  // ─── Title edit handlers ──────────────────────────────────────────────────
  const startEditTitle = () => {
    if (!conversation) return
    setTitleDraft(conversation.title ?? '')
    setEditingTitle(true)
    setMoreOpen(false)
    setTimeout(() => { titleInputRef.current?.select() }, 0)
  }

  const cancelEditTitle = () => {
    setEditingTitle(false)
    setTitleDraft('')
  }

  const saveTitle = async () => {
    if (!activeConversationId || savingTitle) return
    const trimmed = titleDraft.trim()
    if (!trimmed || trimmed === conversation?.title) { cancelEditTitle(); return }
    setSavingTitle(true)
    try {
      await conversationsApi.rename(activeConversationId, trimmed)
      queryClient.invalidateQueries({ queryKey: ['conversation', activeConversationId] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      setEditingTitle(false)
    } finally {
      setSavingTitle(false)
    }
  }

  // ─── Export handler ────────────────────────────────────────────────────────
  const EXPORT_OPTIONS: { format: ExportFormat; label: string; ext: string; icon: ReactNode }[] = [
    { format: 'json',     label: 'JSON',     ext: '.json', icon: <FileJson size={12} /> },
    { format: 'markdown', label: 'Markdown', ext: '.md',   icon: <FileText size={12} /> },
    { format: 'text',     label: 'Text',     ext: '.txt',  icon: <FileText size={12} /> },
  ]

  const handleExport = async (format: ExportFormat) => {
    if (!activeConversationId || exportingFmt) return
    setExportOpen(false)
    setMoreOpen(false)
    setExportError(null)
    setExportingFmt(format)
    try {
      await exportConversation(activeConversationId, format)
    } catch (err: unknown) {
      const status = (err as { status?: number })?.status
      setExportError(
        status === 404 ? 'Conversation not found.' : 'Export failed. Please try again.'
      )
      setTimeout(() => setExportError(null), 4000)
    } finally {
      setExportingFmt(null)
    }
  }

  // ─── Render ───────────────────────────────────────────────────────────────
  // Panel views have their own headers — suppress ChatHeader entirely for them
  const PANEL_VIEWS = ['projects', 'daemons', 'agents', 'memory', 'rag', 'skills', 'settings', 'secrets', 'mcp', 'analytics']
  if (viewMode && PANEL_VIEWS.includes(viewMode)) return null

  return (
    <header
      className="chat-header flex items-center gap-3 shrink-0"
      style={{
        padding: '10px 20px',
        borderBottom: '1px solid var(--border-dim)',
        background: 'rgba(10,13,20,0.7)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        minHeight: 52,
      }}
    >
      {/* ── Left: title area — adapts to viewMode ── */}
      <div className="flex items-center gap-2.5 min-w-0 flex-1">

        {/* Single-item view headers (daemon/agent — plural forms are in PANEL_VIEWS and return null above) */}
        {viewMode === 'daemon' ? (
          <>
            <Clock size={13} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
            <p className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)', maxWidth: 'min(260px, calc(100vw - 240px))' }}>
              {activeViewTarget?.name ?? 'Daemon'}
            </p>
            <PanelBadge label="DAEMON" bg="rgba(0,201,255,0.1)" color="var(--eva-primary)" border="rgba(0,201,255,0.25)" />
          </>
        ) : viewMode === 'agent' ? (
          <>
            <Bot size={13} style={{ color: 'var(--neural)', flexShrink: 0 }} />
            <p className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)', maxWidth: 'min(260px, calc(100vw - 240px))' }}>
              {activeViewTarget?.name ?? 'Agent'}
            </p>
            <PanelBadge label="SUBAGENT" bg="rgba(124,108,243,0.12)" color="var(--neural)" border="rgba(124,108,243,0.25)" />
          </>
        ) : editingTitle ? (
          /* ── Inline title editor ── */
          <div className="flex items-center gap-1.5 min-w-0">
            <input
              ref={titleInputRef}
              className="editable-title"
              value={titleDraft}
              onChange={e => setTitleDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter')  { e.preventDefault(); saveTitle() }
                if (e.key === 'Escape') cancelEditTitle()
              }}
              autoFocus
              disabled={savingTitle}
            />
            <button
              onClick={saveTitle}
              disabled={savingTitle}
              className="w-6 h-6 rounded flex items-center justify-center transition-colors"
              style={{ color: 'var(--success)', background: 'rgba(0,212,160,0.1)' }}
              title="Save"
            >
              {savingTitle ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
            </button>
            <button
              onClick={cancelEditTitle}
              className="w-6 h-6 rounded flex items-center justify-center transition-colors"
              style={{ color: 'var(--text-muted)', background: 'var(--bg-surface2)' }}
              title="Cancel"
            >
              <X size={11} />
            </button>
          </div>
        ) : (
          /* ── Conversation title (click to edit) ── */
          <button
            className="editable-title"
            onClick={activeConversationId ? startEditTitle : undefined}
            style={{ cursor: activeConversationId ? 'text' : 'default' }}
            title={activeConversationId ? 'Click to rename' : undefined}
          >
            {conversation?.title
              ? truncate(conversation.title, 52)
              : activeConversationId
                ? 'Untitled'
                : 'New Chat'}
          </button>
        )}

        {/* Project chip (conversation mode only) */}
        {viewMode === 'conversation' && projectName && !editingTitle && (
          <button className="chip success" style={{ fontSize: 11 }}>
            <FolderOpen size={10} />
            {truncate(projectName, 20)}
          </button>
        )}

        {/* Streaming badge */}
        {isStreaming && <ExecutionBadge />}
      </div>

      {/* ── Divider (conversation view only) ── */}
      {viewMode === 'conversation' && !editingTitle && (
        <div className="div-y" />
      )}

      {/* ── Right: model chip + actions ── */}
      <div className="flex items-center gap-1.5 shrink-0">

        {/* Model chip — click to pin open, hover to peek */}
        <div ref={modelRef} className="group relative">
          <button
            className="chip"
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 11.5,
              cursor: 'pointer',
              background: modelOpen ? 'var(--bg-surface3)' : undefined,
              borderColor: modelOpen ? 'var(--border-bright)' : undefined,
            }}
            title={modelLabel}
            onClick={() => setModelOpen(o => !o)}
          >
            <Sparkles size={11} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
            <span className="max-w-[60px] sm:max-w-[130px] truncate">{modelShort}</span>
            <ChevronDown
              size={10}
              style={{
                color: 'var(--text-muted)',
                flexShrink: 0,
                transform: modelOpen ? 'rotate(180deg)' : 'none',
                transition: 'transform 0.15s',
              }}
            />
          </button>

          {/* Popover — visible on hover (peek) or when clicked (pinned) */}
          <div
            className={`absolute right-0 top-full mt-2 rounded-xl text-xs z-50 transition-opacity ${
              modelOpen
                ? 'opacity-100 pointer-events-auto'
                : 'opacity-0 pointer-events-none group-hover:opacity-100'
            }`}
            style={{
              background: 'var(--bg-surface2)',
              border: '1px solid var(--border-mid)',
              boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
              width: 168,
              padding: '10px 12px',
            }}
          >
            <p className="font-semibold text-xs uppercase tracking-widest mb-2 text-center" style={{ color: 'var(--text-muted)' }}>
              Active Model
            </p>
            <div className="flex flex-col gap-1.5">
              <Row label="Provider" value={config.provider ?? '—'} />
              <Row label="Model"    value={config.model    || '—'} />
            </div>
            <button
              className="mt-3 flex items-center justify-between text-xs w-full"
              style={{
                color: 'var(--text-muted)',
                background: 'none',
                border: 'none',
                borderTop: '1px solid var(--border-dim)',
                cursor: 'pointer',
                padding: '8px 0 0',
              }}
              onClick={() => { setModelOpen(false); openProfileModal('providers') }}
            >
              <span>⚙</span>
              <span>Change Settings</span>
            </button>
          </div>
        </div>

        {/* Modes button — session-level; visible in conversation view (incl. new chat) */}
        {viewMode === 'conversation' && (
          <ModesButton
            agentMode={config.agent_mode         ?? 'auto'}
            toolApproval={config.tool_approval_mode ?? 'auto'}
            skillMode={config.skills_mode          ?? 'auto'}
            onChange={setConfig}
          />
        )}

        {/* Export error toast */}
        {exportError && (
          <div
            className="text-xs px-2.5 py-1 rounded-lg"
            style={{
              background: 'rgba(255,77,109,0.12)',
              border:     '1px solid rgba(255,77,109,0.3)',
              color:      'var(--danger)',
            }}
          >
            {exportError}
          </div>
        )}

        {/* Export button + format picker — conversation view only */}
        {activeConversationId && viewMode === 'conversation' && (
          <div className="relative" ref={exportRef}>
            <button
              className="icon-btn"
              style={{ width: 30, height: 30 }}
              onClick={() => { setExportOpen(o => !o); setExportError(null) }}
              disabled={!!exportingFmt}
              title="Export conversation"
            >
              {exportingFmt
                ? <Loader2 size={14} className="animate-spin" style={{ color: 'var(--eva-primary)' }} />
                : <Download size={14} />}
            </button>

            {exportOpen && (
              <div className="popover" style={{ top: 'calc(100% + 6px)', right: 0, minWidth: 180 }}>
                <p
                  className="px-3 pb-1.5 pt-0.5 text-xs font-semibold uppercase tracking-widest"
                  style={{ color: 'var(--text-muted)', fontSize: 10 }}
                >
                  Export As
                </p>
                {EXPORT_OPTIONS.map(opt => (
                  <button
                    key={opt.format}
                    className="pop-item"
                    onClick={() => handleExport(opt.format)}
                  >
                    {opt.icon}
                    {opt.label}
                    <span className="ml-auto font-mono" style={{ color: 'var(--text-muted)', fontSize: 10 }}>
                      {opt.ext}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* More-actions button — only in conversation view */}
        {activeConversationId && viewMode === 'conversation' && (
          <div className="relative" ref={moreRef}>
            <button
              className="icon-btn"
              style={{ width: 30, height: 30 }}
              onClick={() => setMoreOpen(o => !o)}
              title="More actions"
            >
              <MoreHorizontal size={14} />
            </button>

            {moreOpen && (
              <div className="popover" style={{ top: 'calc(100% + 6px)', right: 0, minWidth: 220 }}>
                <button className="pop-item" onClick={startEditTitle}>
                  <Pencil size={13} /> Rename conversation
                </button>
                <button className="pop-item" onClick={() => setMoreOpen(false)}>
                  <Brain size={13} /> Edit system prompt
                </button>
                <div className="pop-divider" />
                <button className="pop-item" onClick={() => setMoreOpen(false)}>
                  <Copy size={13} /> Duplicate
                </button>
                <button className="pop-item" onClick={() => setMoreOpen(false)}>
                  <FolderPlus size={13} /> Promote to project
                </button>
                <div className="pop-divider" />
                <button className="pop-item danger" onClick={() => setMoreOpen(false)}>
                  <Trash2 size={13} /> Delete conversation
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  )
}

/* ─── Sub-components ─── */

function PanelBadge({ label, bg, color, border }: { label: string; bg: string; color: string; border: string }) {
  return (
    <span
      className="px-1.5 py-0.5 rounded-full shrink-0"
      style={{ fontSize: 9, fontWeight: 700, background: bg, color, border: `1px solid ${border}` }}
    >
      {label}
    </span>
  )
}


function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span style={{ color: 'var(--text-muted)', fontSize: 11.5 }}>{label}</span>
      <span
        className="font-mono text-xs truncate max-w-[80px] sm:max-w-[110px]"
        style={{ color: 'var(--eva-primary)' }}
      >
        {value}
      </span>
    </div>
  )
}
