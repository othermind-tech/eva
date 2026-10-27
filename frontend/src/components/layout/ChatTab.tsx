import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Search, X, Plus, Loader2, SlidersHorizontal,
  Pencil, MoreHorizontal, Brain, FolderPlus, FileJson, FileText, Trash2, FolderOpen,
} from 'lucide-react'
import { conversationsApi, exportConversation, type ExportFormat } from '@/api/conversations'
import { projectsApi, type Project } from '@/api/projects'
import { useChatStore } from '@/store/chatStore'
import { useLoadConversationMessages } from '@/hooks/useLoadConversationMessages'
import { cn } from '@/lib/utils'
import { RENAME_FOCUS_DELAY_MS } from '@/lib/constants'
import type { Conversation } from '@/types/api'
import FilterMenu, { type FilterState, DEFAULT_FILTER } from './FilterMenu'

type DateGroup = 'today' | 'yesterday' | 'week' | 'older'

const GROUP_LABELS: Record<DateGroup, string> = {
  today:     '',
  yesterday: 'Yesterday',
  week:      'Last 7 days',
  older:     'Older',
}

function daysSince(dateStr: string): number {
  return (Date.now() - new Date(dateStr).getTime()) / 86_400_000
}

function dateGroup(dateStr: string | null | undefined): DateGroup {
  if (!dateStr) return 'older'
  const diffDays = (Date.now() - new Date(dateStr).getTime()) / 86_400_000
  if (diffDays < 1)  return 'today'
  if (diffDays < 2)  return 'yesterday'
  if (diffDays < 7)  return 'week'
  return 'older'
}

function relativeDate(dateStr: string | null | undefined): string {
  if (!dateStr) return ''
  const diffMs   = Date.now() - new Date(dateStr).getTime()
  const diffMins = Math.floor(diffMs / 60_000)
  const diffHrs  = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHrs / 24)
  if (diffMins < 1)  return 'now'
  if (diffMins < 60) return `${diffMins}m`
  if (diffHrs  < 24) return `${diffHrs}h`
  if (diffDays < 30) return `${diffDays}d`
  return `${Math.floor(diffDays / 30)}mo`
}

const PROMPT_MAX  = 2000
const PROMPT_WARN = 1800

// ─── Chat Tab ────────────────────────────────────────────────────────────────
export default function ChatTab() {
  const [search, setSearch]         = useState('')
  const [filterOpen, setFilterOpen] = useState(false)
  const [filters, setFilters]       = useState<FilterState>(DEFAULT_FILTER)
  const filterRef                   = useRef<HTMLDivElement>(null)

  const clearMessages         = useChatStore(s => s.clearMessages)
  const setActiveConversation = useChatStore(s => s.setActiveConversation)
  const setActiveExecution    = useChatStore(s => s.setActiveExecution)

  const { data: conversations = [], isLoading } = useQuery({
    queryKey: ['conversations'],
    queryFn:  conversationsApi.list,
    staleTime: Infinity,
  })

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn:  projectsApi.list,
    staleTime: 60_000,
  })
  const projectMap = Object.fromEntries(projects.map(p => [p.id, p]))

  const startNew = () => {
    clearMessages()
    setActiveConversation(null)
    setActiveExecution(null)
  }

  // Filter + sort + group
  const q = search.trim().toLowerCase()
  // Only show conversations not attached to a project
  const standalone = conversations.filter(c => !c.project_id)

  let filtered = q
    ? standalone.filter(c => (c.title ?? '').toLowerCase().includes(q))
    : [...standalone]

  // Apply lastActivity filter
  if (filters.lastActivity !== 'all') {
    const limits: Record<string, number> = { '1d': 1, '3d': 3, '7d': 7, '30d': 30 }
    const maxDays = limits[filters.lastActivity] ?? Infinity
    filtered = filtered.filter(c => daysSince(c.updated_at ?? c.created_at) <= maxDays)
  }

  // Apply sortBy
  if (filters.sortBy === 'alpha') {
    filtered = [...filtered].sort((a, b) => (a.title ?? '').localeCompare(b.title ?? ''))
  } else if (filters.sortBy === 'created') {
    filtered = [...filtered].sort((a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    )
  }

  const isChatFilterActive = filters.lastActivity !== 'all' || filters.sortBy !== 'recency'

  const grouped = filtered.reduce<Record<DateGroup, Conversation[]>>((acc, c) => {
    const g = dateGroup(c.updated_at)
    ;(acc[g] = acc[g] ?? []).push(c)
    return acc
  }, {} as Record<DateGroup, Conversation[]>)

  const groupOrder: DateGroup[] = ['today', 'yesterday', 'week', 'older']

  return (
    <>
      {/* Search */}
      <div style={{ padding: '4px 14px 4px' }}>
        <div className="search-field">
          <Search size={13} />
          <input
            placeholder="Search chats…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
              <X size={11} />
            </button>
          )}
        </div>
      </div>

      {/* Quick actions */}
      <div style={{ padding: '4px 6px 0' }}>
        <button className="quicklink" onClick={startNew}>
          <span className="ql-icon"><Plus size={15} /></span>
          <span>New chat</span>
          <span className="ql-kbd">⌘N</span>
        </button>
      </div>

      {/* Recents section label + filter */}
      <div className="section-label" style={{ position: 'relative' }} ref={filterRef}>
        <span>{q ? `Results · ${filtered.length}` : 'Recents'}</span>
        {!q && (
          <button
            className={cn('filter-btn', isChatFilterActive && 'is-active')}
            title="Filter chats"
            onClick={() => setFilterOpen(o => !o)}
          >
            <SlidersHorizontal size={12} />
          </button>
        )}
        {filterOpen && (
          <FilterMenu
            filters={filters}
            onChange={f => setFilters(f)}
            onClose={() => setFilterOpen(false)}
            showStatus={false}
          />
        )}
      </div>

      <div style={{ padding: '0 0 12px' }}>
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 size={16} className="animate-spin" style={{ color: 'var(--text-muted)' }} />
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: '24px 16px', color: 'var(--text-muted)', fontSize: 12.5, textAlign: 'center' }}>
            {q
              ? <>No chats match "<span style={{ color: 'var(--text-secondary)' }}>{search}</span>"</>
              : 'No conversations yet'}
          </div>
        ) : (
          groupOrder.map(g => {
            const items = grouped[g]
            if (!items?.length) return null
            return (
              <div key={g}>
                {g !== 'today' && (
                  <div style={{ padding: '10px 20px 4px', fontSize: 10, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600 }}>
                    {GROUP_LABELS[g]}
                  </div>
                )}
                {items.map(conv => (
                  <RecentRow
                    key={conv.id}
                    conv={conv}
                    projectName={conv.project_id ? projectMap[conv.project_id]?.name : undefined}
                    projects={projects.filter(p => p.status === 'active')}
                  />
                ))}
              </div>
            )
          })
        )}
      </div>
    </>
  )
}

// ─── Recent Row ───────────────────────────────────────────────────────────────
function RecentRow({
  conv,
  projectName,
  projects,
}: {
  conv:        Conversation
  projectName: string | undefined
  projects:    Project[]
}) {
  const [menu,        setMenu]        = useState(false)
  const [renaming,    setRenaming]    = useState(false)
  const [draft,       setDraft]       = useState(conv.title ?? '')
  const [editor,      setEditor]      = useState<'prompt' | 'project' | null>(null)
  const [promptVal,   setPromptVal]   = useState(conv.system_prompt ?? '')
  const [exportingFmt, setExportingFmt] = useState<ExportFormat | null>(null)
  const renameRef  = useRef<HTMLInputElement>(null)
  const rowRef     = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()

  const handleExport = async (format: ExportFormat) => {
    setMenu(false)
    if (exportingFmt) return
    setExportingFmt(format)
    try {
      await exportConversation(conv.id, format)
    } catch { /* silently ignore */ }
    finally { setExportingFmt(null) }
  }

  const activeConversationId  = useChatStore(s => s.activeConversationId)
  const setActiveConversation = useChatStore(s => s.setActiveConversation)
  const setActiveExecution    = useChatStore(s => s.setActiveExecution)
  const clearMessages         = useChatStore(s => s.clearMessages)
  const loadConversationMessages = useLoadConversationMessages()

  const isActive = conv.id === activeConversationId

  // Close popover on outside click
  useEffect(() => {
    if (!menu) return
    const close = (e: MouseEvent) => {
      if (rowRef.current && !rowRef.current.contains(e.target as Node)) setMenu(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menu])

  useEffect(() => { setDraft(conv.title ?? '') }, [conv.title])

  const { mutate: renameConv } = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => conversationsApi.rename(id, title),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      setRenaming(false)
    },
  })

  const { mutate: savePrompt } = useMutation({
    mutationFn: ({ id, prompt }: { id: string; prompt: string }) => conversationsApi.setSystemPrompt(id, prompt),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      setEditor(null)
    },
  })

  const { mutate: deleteConv } = useMutation({
    mutationFn: (id: string) => conversationsApi.delete(id),
    onSuccess: (_d, id) => {
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      if (activeConversationId === id) { clearMessages(); setActiveConversation(null); setActiveExecution(null) }
    },
  })

  const { mutate: promoteConv } = useMutation({
    mutationFn: ({ id, projectId }: { id: string; projectId: string }) => conversationsApi.promote(id, projectId),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['conversations'] }); setEditor(null) },
  })

  const handleSelect = () => {
    if (renaming || editor) return
    if (conv.id === activeConversationId) return
    void loadConversationMessages(conv.id)
  }

  const commitRename = () => {
    const t = draft.trim()
    if (t && t !== conv.title) renameConv({ id: conv.id, title: t })
    else setRenaming(false)
  }

  const startRename = () => {
    setMenu(false)
    setRenaming(true)
    setTimeout(() => renameRef.current?.select(), RENAME_FOCUS_DELAY_MS)
  }

  const promptCharCount = promptVal.length
  const promptOverMax   = promptCharCount > PROMPT_MAX
  const promptOverWarn  = promptCharCount > PROMPT_WARN

  return (
    <>
      <div
        ref={rowRef}
        className={cn('recent-row', isActive && 'active', menu && 'menu-open')}
        onClick={handleSelect}
      >
        {/* Circle marker */}
        <span className="rr-marker" />

        {/* Title or inline rename input */}
        {renaming ? (
          <input
            ref={renameRef}
            className="editable-title"
            style={{ flex: 1, fontSize: 13.5, padding: '2px 4px' }}
            value={draft}
            autoFocus
            onChange={e => setDraft(e.target.value)}
            onClick={e => e.stopPropagation()}
            onBlur={commitRename}
            onKeyDown={e => {
              if (e.key === 'Enter')  commitRename()
              if (e.key === 'Escape') { setDraft(conv.title ?? ''); setRenaming(false) }
            }}
          />
        ) : (
          <div className="rr-title">{conv.title || 'Untitled'}</div>
        )}

        {/* Date display */}
        {!renaming && !projectName && (
          <span className="rr-date">{relativeDate(conv.updated_at)}</span>
        )}

        {/* Project badge */}
        {projectName && !renaming && (
          <span className="rr-badge">{projectName}</span>
        )}

        {/* Hover actions: pencil + more-h */}
        {!renaming && (
          <div className="rr-actions" onClick={e => e.stopPropagation()}>
            <button
              className="icon-btn"
              style={{ width: 22, height: 22 }}
              title="Rename"
              onClick={startRename}
            >
              <Pencil size={11} />
            </button>
            <button
              className="icon-btn"
              style={{ width: 22, height: 22 }}
              title="More"
              onClick={() => setMenu(m => !m)}
            >
              {exportingFmt
                ? <Loader2 size={12} className="animate-spin" style={{ color: 'var(--eva-primary)' }} />
                : <MoreHorizontal size={12} />}
            </button>
          </div>
        )}

        {/* Context menu popover */}
        {menu && (
          <div className="popover" style={{ top: 32, right: 8, minWidth: 220 }} onClick={e => e.stopPropagation()}>
            <button className="pop-item" onClick={() => { setEditor('prompt'); setPromptVal(conv.system_prompt ?? ''); setMenu(false) }}>
              <Brain size={13} /> System prompt
            </button>
            <button className="pop-item" onClick={() => { setEditor('project'); setMenu(false) }}>
              <FolderPlus size={13} /> Promote to project…
            </button>
            <div className="pop-divider" />
            <button className="pop-item" onClick={() => handleExport('json')} disabled={!!exportingFmt}>
              <FileJson size={13} /> Export as JSON
            </button>
            <button className="pop-item" onClick={() => handleExport('markdown')} disabled={!!exportingFmt}>
              <FileText size={13} /> Export as Markdown
            </button>
            <button className="pop-item" onClick={() => handleExport('text')} disabled={!!exportingFmt}>
              <FileText size={13} /> Export as Text
            </button>
            <div className="pop-divider" />
            <button
              className="pop-item danger"
              onClick={() => { deleteConv(conv.id); setMenu(false) }}
            >
              <Trash2 size={13} /> Delete
            </button>
          </div>
        )}
      </div>

      {/* Inline system prompt editor */}
      {editor === 'prompt' && (
        <div className="inline-editor" style={{ margin: '4px 14px' }} onClick={e => e.stopPropagation()}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '.08em', fontWeight: 600 }}>
            System prompt for this conversation
          </div>
          <textarea
            value={promptVal}
            onChange={e => setPromptVal(e.target.value.slice(0, PROMPT_MAX))}
            rows={3}
            style={{
              borderColor: promptOverMax ? 'var(--danger)' : promptOverWarn ? 'var(--amber)' : undefined,
            }}
          />
          <p style={{ fontSize: 11, color: promptOverWarn ? 'var(--amber)' : 'var(--text-muted)', textAlign: 'right', marginTop: 2 }}>
            {promptCharCount} / {PROMPT_MAX}
          </p>
          <div className="inline-editor-actions">
            <button className="btn ghost" onClick={() => setEditor(null)}>Cancel</button>
            <button
              className="btn ghost"
              onClick={() => savePrompt({ id: conv.id, prompt: '' })}
              style={{ color: 'var(--danger)', borderColor: 'rgba(255,77,109,0.25)' }}
            >
              Clear
            </button>
            <button
              className="btn primary"
              disabled={promptOverMax}
              onClick={() => savePrompt({ id: conv.id, prompt: promptVal.trim() })}
            >
              Save
            </button>
          </div>
        </div>
      )}

      {/* Inline assign-to-project picker */}
      {editor === 'project' && (
        <div className="inline-editor" style={{ margin: '4px 14px' }} onClick={e => e.stopPropagation()}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '.08em', fontWeight: 600 }}>
            Promote to project
          </div>
          {projects.length === 0 ? (
            <p style={{ fontSize: 12.5, color: 'var(--text-muted)', padding: '8px 0' }}>No active projects found</p>
          ) : projects.map(p => (
            <button
              key={p.id}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 7, cursor: 'pointer', fontSize: 12.5, width: '100%', border: 0, background: 'transparent', textAlign: 'left', color: 'var(--text-secondary)' }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-surface3)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              onClick={() => promoteConv({ id: conv.id, projectId: p.id })}
            >
              <FolderOpen size={12} style={{ color: 'var(--text-secondary)' }} />
              <span style={{ flex: 1 }}>{p.name}</span>
              <span
                className="chip chip-sm"
                style={{
                  background: p.status === 'active' ? 'var(--success-dim)' : 'var(--amber-dim)',
                  color: p.status === 'active' ? 'var(--success)' : 'var(--amber)',
                  borderColor: p.status === 'active' ? 'rgba(0,212,160,0.25)' : 'rgba(245,158,11,0.25)',
                  padding: '2px 7px', borderRadius: 999, fontSize: 10.5, border: '1px solid', display: 'inline-flex',
                }}
              >
                {p.status}
              </span>
            </button>
          ))}
          <div className="inline-editor-actions">
            <button className="btn ghost" onClick={() => setEditor(null)}>Cancel</button>
          </div>
        </div>
      )}
    </>
  )
}
