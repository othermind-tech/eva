import { useState, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Webhook, Shield, Terminal, Globe, Plus, Trash2, Edit3,
  X, Check, Upload, FileCode, Download, Eye, Loader2, CheckCircle2,
  AlertTriangle, Search,
} from 'lucide-react'
import { hooksApi } from '@/api/hooks'
import { useSessionStore } from '@/store/sessionStore'
import { formatBytes } from '@/lib/utils'
import { extractErrorDetail } from '@/lib/errors'
import { ToggleSwitch } from '@/components/panels/SessionPanel'
import type {
  HookDefinition, HookEvent, HookHandler, HookType, HookScript,
} from '@/types/api'

// ─── Constants ────────────────────────────────────────────────────────────────

const HANDLER_OPTIONS: { value: HookHandler; label: string; desc: string; allowedEvents: HookEvent[] }[] = [
  { value: 'audit_log',       label: 'Audit Log',       desc: 'Log every tool call to server output',           allowedEvents: ['pre', 'post'] },
  { value: 'deny_always',     label: 'Deny Always',     desc: 'Block this tool entirely — always returns error', allowedEvents: ['pre'] },
  { value: 'path_guard',      label: 'Path Guard',      desc: 'Restrict file/shell tools to allowed paths',      allowedEvents: ['pre'] },
  { value: 'redact_env_vars', label: 'Redact Env Vars', desc: 'Scrub env variable values from tool output',      allowedEvents: ['post'] },
  { value: 'redact_pii',      label: 'Redact PII',      desc: 'Scrub emails, phone numbers, SSNs, and card numbers from tool output', allowedEvents: ['post'] },
]

const ALL_EVENTS: HookEvent[] = ['pre', 'post', 'session_start', 'prompt_submit', 'stop']

const EVENT_STYLE: Record<HookEvent, { bg: string; color: string; border: string }> = {
  pre:           { bg: 'rgba(245,158,11,0.12)',  color: 'var(--amber)',       border: 'rgba(245,158,11,0.3)'  },
  post:          { bg: 'rgba(0,201,255,0.1)',    color: 'var(--eva-primary)', border: 'rgba(0,201,255,0.25)'  },
  session_start: { bg: 'rgba(124,108,243,0.1)', color: 'var(--neural)',      border: 'rgba(124,108,243,0.3)' },
  prompt_submit: { bg: 'rgba(245,158,11,0.12)',  color: 'var(--amber)',       border: 'rgba(245,158,11,0.3)'  },
  stop:          { bg: 'var(--bg-surface3)',     color: 'var(--text-muted)',  border: 'var(--border-dim)'     },
}

const TYPE_STYLE: Record<HookType, { bg: string; color: string; label: string }> = {
  builtin: { bg: 'var(--bg-surface3)',       color: 'var(--text-muted)',  label: 'built-in' },
  command: { bg: 'rgba(124,108,243,0.1)',    color: 'var(--neural)',      label: 'script'   },
  http:    { bg: 'rgba(0,201,255,0.08)',     color: 'var(--eva-primary)', label: 'http'     },
}

const INVALID_NAME_CHARS = /[/\\:*?"<>|]/
const validateScriptName = (name: string): string | null => {
  if (!name.trim()) return 'Name is required'
  if (name.includes('..')) return 'Name cannot contain ..'
  if (INVALID_NAME_CHARS.test(name)) return 'Name cannot contain / \\ : * ? " < > |'
  if (name.length > 128) return 'Name must be ≤ 128 characters'
  return null
}

// ─── Main panel ───────────────────────────────────────────────────────────────

type Tab = 'tool-hooks' | 'scripts'

export default function HooksPanel() {
  const config    = useSessionStore(s => s.config)
  const setConfig = useSessionStore(s => s.setConfig)
  // Saving to the backend is handled centrally by useSessionAutoSave (mounted
  // once in ChatPage) — this panel only reflects the shared status.
  const autoSaveStatus = useSessionStore(s => s.saveStatus)

  const [tab, setTab] = useState<Tab>('tool-hooks')

  const hookCount = config.tool_hooks?.length ?? 0

  return (
    <div className="detail">
      {/* ── Header ── */}
      <div className="detail-header">
        <div className="dh-icon" style={{ background: 'rgba(124,108,243,0.1)', borderColor: 'rgba(124,108,243,0.25)', color: 'var(--neural)' }}>
          <Webhook size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">Hooks</h2>
          <p className="dh-sub">Tool intercepts · Script handlers</p>
        </div>

        {/* Tab pills inside header */}
        <div
          className="detail-tabs flex gap-0.5 p-0.5 rounded-lg"
          style={{ background: 'var(--bg-surface2)' }}
        >
          {([
            { id: 'tool-hooks' as Tab, label: 'Tool Hooks',     badge: hookCount > 0 ? String(hookCount) : undefined },
            { id: 'scripts'    as Tab, label: 'Script Library' },
          ]).map(({ id, label, badge }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors"
              style={tab === id
                ? { background: 'var(--bg-surface3)', color: 'var(--text-primary)', border: '1px solid var(--border-mid)' }
                : { color: 'var(--text-muted)', border: '1px solid transparent' }
              }
            >
              {label}
              {badge && (
                <span
                  className="px-1.5 py-0.5 rounded-full font-bold"
                  style={{ fontSize: 9, background: 'rgba(124,108,243,0.15)', color: 'var(--neural)' }}
                >
                  {badge}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="detail-actions">
          {autoSaveStatus !== 'idle' && (
            <span
              className="flex items-center gap-1.5"
              style={{ fontSize: 11, color: autoSaveStatus === 'saved' ? 'var(--success)' : 'var(--text-muted)' }}
            >
              {autoSaveStatus === 'saving'
                ? <><Loader2 size={10} className="animate-spin" /> Saving…</>
                : <><CheckCircle2 size={10} /> Saved</>}
            </span>
          )}
        </div>
      </div>

      {/* ── Content ── */}
      <div className="detail-body" style={{ maxWidth: 'none' }}>
        {tab === 'tool-hooks' ? (
          <ToolHooksManager
            hooks={config.tool_hooks ?? []}
            onChange={hooks => setConfig({ tool_hooks: hooks })}
          />
        ) : (
          <HookScriptsManager />
        )}
      </div>
    </div>
  )
}


// ─── ToolHooksManager ────────────────────────────────────────────────────────

function ToolHooksManager({
  hooks, onChange,
}: {
  hooks:    HookDefinition[]
  onChange: (h: HookDefinition[]) => void
}) {
  const [modalOpen, setModalOpen] = useState(false)
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const [deleteIdx, setDeleteIdx] = useState<number | null>(null)
  const [search,    setSearch]    = useState('')

  const openAdd  = () => { setEditIndex(null); setModalOpen(true) }
  const openEdit = (i: number) => { setEditIndex(i); setModalOpen(true) }

  const handleSave = (hook: HookDefinition) => {
    if (editIndex === null) onChange([...hooks, hook])
    else onChange(hooks.map((h, i) => i === editIndex ? hook : h))
    setModalOpen(false)
    setEditIndex(null)
  }

  const handleDelete = (i: number) => {
    onChange(hooks.filter((_, idx) => idx !== i))
    setDeleteIdx(null)
  }

  const filtered = search.trim()
    ? hooks.filter(h =>
        h.tool?.toLowerCase().includes(search.toLowerCase()) ||
        h.event.toLowerCase().includes(search.toLowerCase()) ||
        h.handler?.toLowerCase().includes(search.toLowerCase()) ||
        h.command?.toLowerCase().includes(search.toLowerCase()) ||
        h.url?.toLowerCase().includes(search.toLowerCase())
      )
    : hooks

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="search-field flex-1">
          <Search size={13} />
          <input
            placeholder="Search hooks…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
              <X size={11} />
            </button>
          )}
        </div>
        <button className="btn primary" onClick={openAdd}>
          <Plus size={12} /> New Hook
        </button>
      </div>

      <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)', marginTop: -8 }}>
        Intercept tool calls and lifecycle events. Hooks run in order — full replace on save.
      </p>

      {hooks.length === 0 ? (
        <div
          className="flex flex-col items-center gap-3 py-14 text-center"
        >
          <Shield size={28} style={{ color: 'var(--text-muted)' }} />
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>No hooks configured yet.</p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', opacity: 0.7, maxWidth: 320 }}>
            All tools run without interception. Add a hook to audit, block, or redirect tool calls.
          </p>
          <button className="btn ghost" onClick={openAdd}>
            <Plus size={12} /> Add first hook
          </button>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <AlertTriangle size={20} style={{ color: 'var(--text-muted)' }} />
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>No hooks match "{search}"</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {filtered.map((h, i) => {
            const hookType    = h.type ?? 'builtin'
            const evStyle     = EVENT_STYLE[h.event] ?? EVENT_STYLE.pre
            const typeStyle   = TYPE_STYLE[hookType]
            const handlerMeta = HANDLER_OPTIONS.find(o => o.value === h.handler)
            const showTool    = h.event === 'pre' || h.event === 'post'
            const label = hookType === 'builtin'
              ? (handlerMeta?.label ?? h.handler ?? '—')
              : hookType === 'command' ? (h.command ?? '—') : (h.url ?? '—')

            return (
              <div key={i} className="kn-card" style={{ marginBottom: 0 }}>
                <div className="kn-head" style={{ marginBottom: 0 }}>
                  {/* Event badge */}
                  <span
                    className="chip chip-sm font-mono uppercase"
                    style={{ background: evStyle.bg, color: evStyle.color, border: `1px solid ${evStyle.border}`, fontSize: 9, fontWeight: 700 }}
                  >
                    {h.event.replace(/_/g, ' ')}
                  </span>

                  {/* Tool name (for pre/post events) */}
                  {showTool && (
                    <span
                      className="chip chip-sm font-mono"
                      style={{ background: 'var(--bg-surface3)', color: 'var(--text-secondary)', border: '1px solid var(--border-dim)', fontSize: 10 }}
                    >
                      {h.tool}
                    </span>
                  )}

                  <span style={{ fontSize: 11, color: 'var(--text-muted)', flexShrink: 0 }}>→</span>

                  {/* Type badge */}
                  <span
                    className="chip chip-sm"
                    style={{ background: typeStyle.bg, color: typeStyle.color, border: '1px solid transparent', fontSize: 9, fontWeight: 600 }}
                  >
                    {typeStyle.label}
                  </span>

                  {/* Handler / command / url */}
                  <span className="kn-title font-mono" style={{ fontSize: 12.5 }}>
                    {label}
                  </span>

                  {/* Async tag */}
                  {(hookType === 'command' || hookType === 'http') && h.async_ && (
                    <span className="chip chip-sm" style={{ fontSize: 9, color: 'var(--text-muted)', border: '1px solid var(--border-dim)' }}>
                      async
                    </span>
                  )}

                  {/* Action buttons */}
                  <div className="flex items-center" style={{ gap: 2, marginLeft: 'auto' }} onClick={e => e.stopPropagation()}>
                    {deleteIdx === i ? (
                      <>
                        <span style={{ fontSize: 11, color: 'var(--danger)', marginRight: 4 }}>Remove?</span>
                        <button
                          className="icon-btn danger"
                          style={{ width: 24, height: 24 }}
                          onClick={() => handleDelete(i)}
                          title="Confirm delete"
                        >
                          <Check size={10} />
                        </button>
                        <button
                          className="icon-btn"
                          style={{ width: 24, height: 24 }}
                          onClick={() => setDeleteIdx(null)}
                          title="Cancel"
                        >
                          <X size={10} />
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          className="icon-btn"
                          style={{ width: 26, height: 26 }}
                          onClick={() => openEdit(i)}
                          title="Edit hook"
                          onMouseEnter={e => (e.currentTarget.style.color = 'var(--eva-primary)')}
                          onMouseLeave={e => (e.currentTarget.style.color = '')}
                        >
                          <Edit3 size={12} />
                        </button>
                        <button
                          className="icon-btn"
                          style={{ width: 26, height: 26 }}
                          onClick={() => setDeleteIdx(i)}
                          title="Delete hook"
                          onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
                          onMouseLeave={e => (e.currentTarget.style.color = '')}
                        >
                          <Trash2 size={12} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {modalOpen && (
        <HookModal
          initial={editIndex !== null ? hooks[editIndex] : undefined}
          onSave={handleSave}
          onClose={() => { setModalOpen(false); setEditIndex(null) }}
        />
      )}
    </div>
  )
}

// ─── HookModal ────────────────────────────────────────────────────────────────

function HookModal({ initial, onSave, onClose }: { initial?: HookDefinition; onSave: (h: HookDefinition) => void; onClose: () => void }) {
  const initType: HookType = initial?.type ?? 'builtin'
  const [type,        setType]        = useState<HookType>(initType)
  const [event,       setEvent]       = useState<HookEvent>(initial?.event ?? 'pre')
  const [tool,        setTool]        = useState(initial?.tool ?? '*')
  const [handler,     setHandler]     = useState<HookHandler>(initial?.handler ?? 'audit_log')
  const [pathsText,   setPathsText]   = useState((initial?.config?.allowed_prefixes as string[] | undefined)?.join('\n') ?? '')
  const [denyReason,  setDenyReason]  = useState((initial?.config?.reason as string | undefined) ?? '')
  const [command,     setCommand]     = useState(initial?.command ?? '')
  const [cmdTimeout,  setCmdTimeout]  = useState(initial?.timeout ?? 30)
  const [isAsync,     setIsAsync]     = useState(initial?.async_ ?? false)
  const [url,         setUrl]         = useState(initial?.url ?? '')
  const [headersText, setHeadersText] = useState(Object.entries(initial?.headers ?? {}).map(([k, v]) => `${k}: ${v}`).join('\n'))
  const [httpTimeout, setHttpTimeout] = useState(initial?.timeout ?? 30)

  const { data: scriptsList = [] } = useQuery({
    queryKey: ['hook-scripts'],
    queryFn:  hooksApi.listScripts,
    enabled:  type === 'command',
    staleTime: 30_000,
  })

  const isBlockingEvent = event === 'pre' || event === 'prompt_submit'
  const showToolField   = event === 'pre' || event === 'post'
  const handlerMeta     = HANDLER_OPTIONS.find(o => o.value === handler)!
  const validBuiltinEv  = handlerMeta.allowedEvents
  const effectiveEvent  = type === 'builtin' && !validBuiltinEv.includes(event) ? validBuiltinEv[0] : event

  const parseHeaders = (text: string): Record<string, string> => {
    const obj: Record<string, string> = {}
    text.split('\n').forEach(line => {
      const idx = line.indexOf(':')
      if (idx > 0) { const k = line.slice(0, idx).trim(); const v = line.slice(idx + 1).trim(); if (k) obj[k] = v }
    })
    return obj
  }

  const handleSave = () => {
    const base = { tool: showToolField ? (tool.trim() || '*') : '*', event: effectiveEvent }
    if (type === 'command')
      onSave({ ...base, type: 'command', command: command.trim(), timeout: cmdTimeout, ...(isBlockingEvent ? {} : { async_: isAsync }) })
    else if (type === 'http')
      onSave({ ...base, type: 'http', url: url.trim(), headers: Object.keys(parseHeaders(headersText)).length ? parseHeaders(headersText) : undefined, timeout: httpTimeout, ...(isBlockingEvent ? {} : { async_: isAsync }) })
    else {
      const config = (() => {
        if (handler === 'path_guard') {
          const ps = pathsText.split('\n').map(s => s.trim()).filter(Boolean)
          return ps.length ? { allowed_prefixes: ps } : undefined
        }
        if (handler === 'deny_always' && denyReason.trim()) return { reason: denyReason.trim() }
        return undefined
      })()
      onSave({ ...base, type: 'builtin', handler, config })
    }
  }

  const canSave = type === 'command' ? command.trim().length > 0 : type === 'http' ? url.trim().length > 0 : tool.trim().length > 0
  const typeLabel = (t: HookType) => t === 'builtin' ? 'Built-in' : t === 'command' ? 'Script' : 'HTTP'
  const typeIcon  = (t: HookType) => t === 'builtin' ? <Shield size={11} /> : t === 'command' ? <Terminal size={11} /> : <Globe size={11} />

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(6,9,15,0.75)', backdropFilter: 'blur(4px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="flex flex-col gap-4 p-5 rounded-2xl w-96 max-h-[90vh] overflow-y-auto"
        style={{ background: 'var(--bg-surface1)', border: '1px solid var(--border-mid)', boxShadow: '0 24px 64px rgba(0,0,0,0.5)' }}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield size={14} style={{ color: 'var(--neural)' }} />
            <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              {initial ? 'Edit Hook' : 'Add Hook'}
            </span>
          </div>
          <button className="icon-btn" style={{ width: 26, height: 26 }} onClick={onClose}>
            <X size={13} />
          </button>
        </div>

        {/* Event */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Event</label>
          <select
            value={event}
            onChange={e => setEvent(e.target.value as HookEvent)}
            className="text-xs px-3 py-2 rounded-lg outline-none"
            style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
          >
            {ALL_EVENTS.map(ev => <option key={ev} value={ev}>{ev.replace(/_/g, ' ')}</option>)}
          </select>
          {type === 'builtin' && !validBuiltinEv.includes(event) && (
            <p className="text-xs" style={{ color: 'var(--amber)' }}>
              {handlerMeta.label} requires {validBuiltinEv.map(e => e.toUpperCase()).join(' / ')} event. Will be auto-corrected.
            </p>
          )}
        </div>

        {/* Tool name */}
        {showToolField && (
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Tool name</label>
            <input
              type="text"
              value={tool}
              onChange={e => setTool(e.target.value)}
              placeholder="* for all tools, or exact name (e.g. shell)"
              className="text-xs px-3 py-2 rounded-lg font-mono outline-none"
              style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
            />
          </div>
        )}

        {/* Type */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Type</label>
          <div className="flex gap-2">
            {(['builtin', 'command', 'http'] as HookType[]).map(t => (
              <button
                key={t}
                onClick={() => setType(t)}
                className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold transition-all"
                style={{
                  background: type === t ? TYPE_STYLE[t].bg : 'var(--bg-surface2)',
                  border: type === t ? `1px solid ${TYPE_STYLE[t].color}40` : '1px solid var(--border-dim)',
                  color: type === t ? TYPE_STYLE[t].color : 'var(--text-muted)',
                }}
              >
                {typeIcon(t)} {typeLabel(t)}
              </button>
            ))}
          </div>
        </div>

        {/* Built-in handler options */}
        {type === 'builtin' && (
          <>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Handler</label>
              <div className="flex flex-col gap-1">
                {HANDLER_OPTIONS.map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => setHandler(opt.value)}
                    className="flex flex-col items-start px-3 py-2 rounded-lg text-left transition-all"
                    style={{
                      background: handler === opt.value ? 'rgba(124,108,243,0.1)' : 'var(--bg-surface2)',
                      border: `1px solid ${handler === opt.value ? 'rgba(124,108,243,0.35)' : 'var(--border-dim)'}`,
                    }}
                  >
                    <span className="text-xs font-semibold" style={{ color: handler === opt.value ? 'var(--neural)' : 'var(--text-primary)' }}>
                      {opt.label}
                    </span>
                    <span className="text-xs" style={{ color: 'var(--text-muted)', fontSize: 10 }}>
                      {opt.desc}
                      {opt.allowedEvents.length === 1 && <span style={{ color: 'var(--amber)' }}> · {opt.allowedEvents[0].toUpperCase()} only</span>}
                    </span>
                  </button>
                ))}
              </div>
            </div>
            {handler === 'path_guard' && (
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
                  Allowed path prefixes <span style={{ fontWeight: 400 }}>(one per line)</span>
                </label>
                <textarea
                  value={pathsText}
                  onChange={e => setPathsText(e.target.value)}
                  placeholder={'/home/user/projects\n/tmp/safe'}
                  rows={3}
                  className="text-xs px-3 py-2 rounded-lg font-mono outline-none resize-none"
                  style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
                />
              </div>
            )}
            {handler === 'deny_always' && (
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
                  Denial reason <span style={{ fontWeight: 400 }}>(optional)</span>
                </label>
                <input
                  type="text"
                  value={denyReason}
                  onChange={e => setDenyReason(e.target.value)}
                  placeholder="e.g. Blocked by security policy"
                  className="text-xs px-3 py-2 rounded-lg outline-none"
                  style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
                />
              </div>
            )}
          </>
        )}

        {/* Script (command) options */}
        {type === 'command' && (
          <>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Script</label>
              {scriptsList.length === 0
                ? <p className="text-xs px-3 py-2 rounded-lg" style={{ background: 'var(--bg-surface2)', color: 'var(--text-muted)' }}>
                    No scripts uploaded yet. Add one in the Script Library tab.
                  </p>
                : <select
                    value={command}
                    onChange={e => setCommand(e.target.value)}
                    className="text-xs px-3 py-2 rounded-lg font-mono outline-none"
                    style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
                  >
                    <option value="">— select script —</option>
                    {scriptsList.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
                  </select>
              }
            </div>
            <div className="flex gap-2">
              <div className="flex flex-col gap-1.5 flex-1">
                <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Timeout (s)</label>
                <input
                  type="number" min={1} max={300} value={cmdTimeout}
                  onChange={e => setCmdTimeout(Number(e.target.value))}
                  className="text-xs px-3 py-2 rounded-lg outline-none"
                  style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
                />
              </div>
              {!isBlockingEvent && (
                <div className="flex flex-col gap-1.5 justify-end">
                  <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Async</label>
                  <div className="px-2 py-1.5"><ToggleSwitch checked={isAsync} onChange={setIsAsync} /></div>
                </div>
              )}
            </div>
            {isBlockingEvent && (
              <p className="text-xs" style={{ color: 'var(--text-muted)', fontSize: 10 }}>
                Async disabled — {event} hooks must be blocking.
              </p>
            )}
          </>
        )}

        {/* HTTP options */}
        {type === 'http' && (
          <>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>URL</label>
              <input
                type="url" value={url} onChange={e => setUrl(e.target.value)}
                placeholder="https://my-hook.example.com/hook"
                className="text-xs px-3 py-2 rounded-lg font-mono outline-none"
                style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
                Headers <span style={{ fontWeight: 400 }}>(Key: Value, one per line)</span>
              </label>
              <textarea
                value={headersText} onChange={e => setHeadersText(e.target.value)}
                placeholder={'Authorization: Bearer token\nX-Custom: value'}
                rows={2}
                className="text-xs px-3 py-2 rounded-lg font-mono outline-none resize-none"
                style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
              />
            </div>
            <div className="flex gap-2">
              <div className="flex flex-col gap-1.5 flex-1">
                <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Timeout (s)</label>
                <input
                  type="number" min={1} max={300} value={httpTimeout}
                  onChange={e => setHttpTimeout(Number(e.target.value))}
                  className="text-xs px-3 py-2 rounded-lg outline-none"
                  style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
                />
              </div>
              {!isBlockingEvent && (
                <div className="flex flex-col gap-1.5 justify-end">
                  <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Async</label>
                  <div className="px-2 py-1.5"><ToggleSwitch checked={isAsync} onChange={setIsAsync} /></div>
                </div>
              )}
            </div>
            {isBlockingEvent && (
              <p className="text-xs" style={{ color: 'var(--text-muted)', fontSize: 10 }}>
                Async disabled — {event} hooks must be blocking.
              </p>
            )}
          </>
        )}

        {/* Footer */}
        <div className="flex gap-2 pt-1">
          <button
            onClick={onClose}
            className="btn ghost flex-1"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!canSave}
            className="flex-1 py-2 rounded-lg text-xs font-semibold transition-all"
            style={{
              background: canSave ? 'rgba(124,108,243,0.15)' : 'var(--bg-surface2)',
              border: `1px solid ${canSave ? 'rgba(124,108,243,0.4)' : 'var(--border-dim)'}`,
              color: canSave ? 'var(--neural)' : 'var(--text-muted)',
              cursor: canSave ? 'pointer' : 'not-allowed',
            }}
          >
            {initial ? 'Save Changes' : 'Add Hook'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── HookScriptsManager ───────────────────────────────────────────────────────

function HookScriptsManager() {
  const qc = useQueryClient()
  const [uploadOpen,   setUploadOpen]   = useState(false)
  const [editScript,   setEditScript]   = useState<HookScript | null>(null)
  const [viewScript,   setViewScript]   = useState<HookScript | null>(null)
  const [viewContent,  setViewContent]  = useState<string | null>(null)
  const [viewLoading,  setViewLoading]  = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [deleting,     setDeleting]     = useState(false)
  const [search,       setSearch]       = useState('')

  const { data: scripts = [], isLoading } = useQuery({
    queryKey: ['hook-scripts'],
    queryFn:  hooksApi.listScripts,
    staleTime: 30_000,
  })

  const handleView = async (s: HookScript) => {
    setViewScript(s); setViewContent(null); setViewLoading(true)
    try { setViewContent(await hooksApi.getScriptContent(s.name)) }
    catch { setViewContent('Failed to load script content.') }
    finally { setViewLoading(false) }
  }

  const handleDelete = async (name: string) => {
    setDeleting(true)
    try { await hooksApi.deleteScript(name); qc.invalidateQueries({ queryKey: ['hook-scripts'] }) }
    finally { setDeleting(false); setDeleteTarget(null) }
  }

  const handleDownload = (name: string, content: string) => {
    const blob = new Blob([content], { type: 'text/plain' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a'); a.href = url; a.download = name; a.click()
    URL.revokeObjectURL(url)
  }

  const filtered = search.trim()
    ? scripts.filter(s =>
        s.name.toLowerCase().includes(search.toLowerCase()) ||
        (s.description ?? '').toLowerCase().includes(search.toLowerCase())
      )
    : scripts

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="search-field flex-1">
          <Search size={13} />
          <input
            placeholder="Search scripts…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
              <X size={11} />
            </button>
          )}
        </div>
        <button className="btn primary" onClick={() => { setEditScript(null); setUploadOpen(true) }}>
          <Upload size={12} /> Upload Script
        </button>
      </div>

      <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)', marginTop: -8 }}>
        Upload shell scripts or programs to use as hook handlers. Scripts receive a JSON payload on stdin and respond with an allow/block decision.
      </p>

      {isLoading ? (
        <div className="flex items-center gap-2 text-xs py-4" style={{ color: 'var(--text-muted)' }}>
          <Loader2 size={13} className="animate-spin" /> Loading scripts…
        </div>
      ) : scripts.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-14 text-center">
          <FileCode size={28} style={{ color: 'var(--text-muted)' }} />
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>No hook scripts uploaded yet.</p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', opacity: 0.7, maxWidth: 320 }}>
            Upload executable scripts to use as custom hook handlers for tool interception.
          </p>
          <button className="btn ghost" onClick={() => { setEditScript(null); setUploadOpen(true) }}>
            <Upload size={12} /> Upload first script
          </button>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <AlertTriangle size={20} style={{ color: 'var(--text-muted)' }} />
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>No scripts match "{search}"</p>
        </div>
      ) : (
        <div className="flex flex-col">
          {filtered.map(s => (
            <div key={s.name} className="kn-card">
              <div className="kn-head">
                <div
                  className="row-icon"
                  style={{ background: 'rgba(124,108,243,0.1)', color: 'var(--neural)', width: 32, height: 32, borderRadius: 9, flexShrink: 0 }}
                >
                  <Terminal size={14} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="kn-title font-mono">{s.name}</div>
                  {s.description && (
                    <div className="kn-body" style={{ fontSize: 11, marginTop: 1 }}>
                      {s.description}
                    </div>
                  )}
                </div>
                <span className="chip chip-sm font-mono" style={{ color: 'var(--text-muted)', border: '1px solid var(--border-dim)' }}>
                  {formatBytes(s.size_bytes)}
                </span>
                <div className="flex items-center" style={{ gap: 2 }} onClick={e => e.stopPropagation()}>
                  <button
                    className="icon-btn"
                    style={{ width: 28, height: 28 }}
                    onClick={() => handleView(s)}
                    title="View script"
                    onMouseEnter={e => (e.currentTarget.style.color = 'var(--eva-primary)')}
                    onMouseLeave={e => (e.currentTarget.style.color = '')}
                  >
                    <Eye size={13} />
                  </button>
                  <button
                    className="icon-btn"
                    style={{ width: 28, height: 28 }}
                    onClick={() => setEditScript(s)}
                    title="Edit script"
                    onMouseEnter={e => (e.currentTarget.style.color = 'var(--neural)')}
                    onMouseLeave={e => (e.currentTarget.style.color = '')}
                  >
                    <Edit3 size={13} />
                  </button>
                  {deleteTarget === s.name ? (
                    <>
                      <span style={{ fontSize: 11, color: 'var(--danger)', marginLeft: 4, marginRight: 2 }}>Delete?</span>
                      <button
                        className="icon-btn danger"
                        style={{ width: 26, height: 26 }}
                        onClick={() => handleDelete(s.name)}
                        disabled={deleting}
                      >
                        {deleting ? <Loader2 size={10} className="animate-spin" /> : <Check size={10} />}
                      </button>
                      <button
                        className="icon-btn"
                        style={{ width: 26, height: 26 }}
                        onClick={() => setDeleteTarget(null)}
                      >
                        <X size={10} />
                      </button>
                    </>
                  ) : (
                    <button
                      className="icon-btn"
                      style={{ width: 28, height: 28 }}
                      onClick={() => setDeleteTarget(s.name)}
                      title="Delete script"
                      onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
                      onMouseLeave={e => (e.currentTarget.style.color = '')}
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {(uploadOpen || editScript) && (
        <ScriptUploadModal
          script={editScript}
          onClose={() => { setUploadOpen(false); setEditScript(null) }}
          onSaved={() => { setUploadOpen(false); setEditScript(null); qc.invalidateQueries({ queryKey: ['hook-scripts'] }) }}
        />
      )}

      {/* View script modal */}
      {viewScript && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(6,9,15,0.75)', backdropFilter: 'blur(4px)' }}
          onClick={e => { if (e.target === e.currentTarget) setViewScript(null) }}
        >
          <div
            className="flex flex-col gap-4 p-5 rounded-2xl w-[520px] max-h-[80vh]"
            style={{ background: 'var(--bg-surface1)', border: '1px solid var(--border-mid)', boxShadow: '0 24px 64px rgba(0,0,0,0.5)' }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FileCode size={14} style={{ color: 'var(--neural)' }} />
                <span className="text-sm font-semibold font-mono" style={{ color: 'var(--text-primary)' }}>{viewScript.name}</span>
                <span className="chip chip-sm font-mono" style={{ color: 'var(--text-muted)' }}>{formatBytes(viewScript.size_bytes)}</span>
              </div>
              <div className="flex items-center gap-2">
                {viewContent && (
                  <button
                    className="btn ghost"
                    style={{ padding: '4px 10px' }}
                    onClick={() => handleDownload(viewScript.filename, viewContent)}
                  >
                    <Download size={11} /> Download
                  </button>
                )}
                <button className="icon-btn" style={{ width: 26, height: 26 }} onClick={() => setViewScript(null)}>
                  <X size={13} />
                </button>
              </div>
            </div>
            <div
              className="flex-1 overflow-auto rounded-xl p-3"
              style={{ background: 'var(--bg-base)', border: '1px solid var(--border-dim)', minHeight: 120, maxHeight: 400 }}
            >
              {viewLoading
                ? <div className="flex items-center gap-2 text-xs py-2" style={{ color: 'var(--text-muted)' }}>
                    <Loader2 size={11} className="animate-spin" /> Loading…
                  </div>
                : <pre className="text-xs font-mono whitespace-pre-wrap" style={{ color: 'var(--text-primary)', lineHeight: 1.6 }}>
                    {viewContent ?? ''}
                  </pre>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── ScriptUploadModal ────────────────────────────────────────────────────────

function ScriptUploadModal({
  script, onClose, onSaved,
}: {
  script:   HookScript | null
  onClose:  () => void
  onSaved:  () => void
}) {
  const [name,        setName]        = useState(script?.name ?? '')
  const [description, setDescription] = useState(script?.description ?? '')
  const [file,        setFile]        = useState<File | null>(null)
  const [saving,      setSaving]      = useState(false)
  const [error,       setError]       = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const nameError = script ? null : validateScriptName(name)
  const canSave   = (script ? true : !nameError && name.trim().length > 0) && file !== null

  const handleSave = async () => {
    if (!file) return
    setSaving(true); setError(null)
    try {
      if (script) await hooksApi.updateScript(script.name, file, description || undefined)
      else        await hooksApi.uploadScript(name.trim(), file, description || undefined)
      onSaved()
    } catch (e: unknown) {
      // client.ts flattens errors to { status, detail } - there is no `.response`
      setError(extractErrorDetail(e, 'Upload failed'))
    } finally { setSaving(false) }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(6,9,15,0.7)', backdropFilter: 'blur(4px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="flex flex-col gap-4 p-5 rounded-2xl w-80"
        style={{ background: 'var(--bg-surface1)', border: '1px solid var(--border-mid)', boxShadow: '0 24px 64px rgba(0,0,0,0.5)' }}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Upload size={14} style={{ color: 'var(--neural)' }} />
            <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              {script ? `Edit — ${script.name}` : 'Upload Script'}
            </span>
          </div>
          <button className="icon-btn" style={{ width: 26, height: 26 }} onClick={onClose}>
            <X size={13} />
          </button>
        </div>

        {!script && (
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Script name</label>
            <input
              type="text" value={name} onChange={e => setName(e.target.value)}
              placeholder="my-hook-script"
              className="text-xs px-3 py-2 rounded-lg font-mono outline-none"
              style={{
                background: 'var(--bg-surface2)',
                border: `1px solid ${nameError && name ? 'var(--danger)' : 'var(--border-dim)'}`,
                color: 'var(--text-primary)',
              }}
            />
            {nameError && name && (
              <p className="text-xs" style={{ color: 'var(--danger)', fontSize: 10 }}>{nameError}</p>
            )}
          </div>
        )}

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
            Description <span style={{ fontWeight: 400 }}>(optional)</span>
          </label>
          <input
            type="text" value={description} onChange={e => setDescription(e.target.value)}
            placeholder="What does this script do?"
            className="text-xs px-3 py-2 rounded-lg outline-none"
            style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
            {script ? 'Replacement file' : 'Script file'}
          </label>
          <input
            ref={inputRef}
            type="file"
            onChange={e => setFile(e.target.files?.[0] ?? null)}
            className="hidden"
          />
          <button
            onClick={() => inputRef.current?.click()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs transition-all"
            style={{
              background: file ? 'rgba(124,108,243,0.08)' : 'var(--bg-surface2)',
              border: `1px solid ${file ? 'rgba(124,108,243,0.3)' : 'var(--border-dim)'}`,
              color: file ? 'var(--neural)' : 'var(--text-muted)',
            }}
          >
            <Upload size={11} />
            {file ? file.name : 'Choose file…'}
          </button>
        </div>

        {error && (
          <p className="text-xs px-3 py-2 rounded-lg" style={{ background: 'rgba(255,77,109,0.08)', color: 'var(--danger)', border: '1px solid rgba(255,77,109,0.2)' }}>
            {error}
          </p>
        )}

        <div className="flex gap-2 pt-1">
          <button className="btn ghost flex-1" onClick={onClose}>Cancel</button>
          <button
            onClick={handleSave}
            disabled={!canSave || saving}
            className="flex-1 py-2 rounded-lg text-xs font-semibold transition-all flex items-center justify-center gap-1.5"
            style={{
              background: canSave ? 'rgba(124,108,243,0.15)' : 'var(--bg-surface2)',
              border: `1px solid ${canSave ? 'rgba(124,108,243,0.4)' : 'var(--border-dim)'}`,
              color: canSave ? 'var(--neural)' : 'var(--text-muted)',
              cursor: canSave && !saving ? 'pointer' : 'not-allowed',
            }}
          >
            {saving ? <><Loader2 size={10} className="animate-spin" /> Uploading…</> : (script ? 'Update' : 'Upload')}
          </button>
        </div>
      </div>
    </div>
  )
}
