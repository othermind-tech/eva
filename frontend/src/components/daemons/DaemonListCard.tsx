import { useState } from 'react'
import { Loader2, ChevronRight, Check, Trash2, Monitor, Copy, Power, Play, AlertTriangle, X, Clock } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { daemonsApi } from '@/api/daemons'
import { projectsApi } from '@/api/projects'
import type { DaemonConfig } from '@/types/api'
import { formatInterval, type DaemonTriggerState } from './daemonFormat'
import DaemonLifecycleBadge from './DaemonLifecycleBadge'

// ── Daemon list card ──────────────────────────────────────────────────────────
export default function DaemonListCard({
  daemon, onEdit, onDelete, onMonitor,
}: {
  daemon:    DaemonConfig
  onEdit:    () => void
  onDelete:  () => void
  onMonitor: () => void
}) {
  const qc = useQueryClient()
  const [triggerState, setTriggerState] = useState<DaemonTriggerState>('idle')
  const [confirmDel,   setConfirmDel]   = useState(false)
  const [toggleError,  setToggleError]  = useState<string | null>(null)

  const { mutate: doToggle, isPending: toggling } = useMutation({
    mutationFn: (enabled: boolean) =>
      daemonsApi.update(daemon.name, {
        name:          daemon.name,
        system_prompt: daemon.system_prompt,
        enabled,
      }),
    onSuccess: () => {
      setToggleError(null)
      qc.invalidateQueries({ queryKey: ['daemons'] })
    },
    onError: (err: unknown) => {
      const e = err as { status?: number }
      const msg = e.status === 403
        ? 'Project is paused — resume the project to enable or disable this daemon.'
        : 'Failed to update daemon status. Please try again.'
      setToggleError(msg)
      setTimeout(() => setToggleError(null), 6000)
    },
  })

  // ── Clone state ──────────────────────────────────────────────
  const [cloneOpen,      setCloneOpen]      = useState(false)
  const [cloneName,      setCloneName]      = useState('')
  const [cloneProjectId, setCloneProjectId] = useState('')
  const [cloneError,     setCloneError]     = useState<string | null>(null)

  const { data: projects = [] } = useQuery<import('@/api/projects').Project[]>({
    queryKey:  ['projects'],
    queryFn:   projectsApi.list,
    staleTime: 60_000,
    enabled:   cloneOpen,
  })

  const { mutate: cloneDaemon, isPending: cloning } = useMutation({
    mutationFn: () => daemonsApi.cloneDaemon(daemon.name, cloneName.trim(), cloneProjectId || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['daemons'] })
      setCloneOpen(false); setCloneName(''); setCloneProjectId(''); setCloneError(null)
    },
    onError: (err: unknown) => {
      const e = err as { status?: number }
      setCloneError(e.status === 409 ? `A daemon named "${cloneName.trim()}" already exists.` : 'Clone failed. Please try again.')
    },
  })

  const handleCloneSubmit = (ev: React.MouseEvent) => {
    ev.stopPropagation()
    const t = cloneName.trim()
    if (!t) { setCloneError('Name is required'); return }
    if (!/^[a-z0-9_-]+$/.test(t)) { setCloneError('Lowercase, numbers, hyphens, underscores only'); return }
    setCloneError(null)
    cloneDaemon()
  }

  const handleTrigger = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (triggerState === 'loading') return
    setTriggerState('loading')
    try {
      await daemonsApi.trigger(daemon.name)
      setTriggerState('ok')
      setTimeout(() => setTriggerState('idle'), 3000)
    } catch (err: unknown) {
      const e = err as { status?: number }
      setTriggerState('error')
      setTimeout(() => setTriggerState('idle'), 3000)
      if (e.status === 403) {
        setToggleError('Cannot trigger — project is paused. Resume the project first.')
        setTimeout(() => setToggleError(null), 6000)
      }
    }
  }

  const ls = daemon.lifecycle_status

  return (
    <div className="kn-card" style={{ opacity: daemon.enabled ? 1 : 0.7 }}>
      <div className="kn-head" onClick={onEdit}>
        <div className="row-icon" style={{ background: 'rgba(0,201,255,0.1)', color: 'var(--eva-primary)', width: 32, height: 32, borderRadius: 9, flexShrink: 0 }}>
          <Clock size={14} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text-primary)' }}>
            {daemon.name}
          </span>
          <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
            {daemon.description
              ? `${daemon.description.slice(0, 60)}${daemon.description.length > 60 ? '…' : ''}`
              : 'No description'
            } · {formatInterval(daemon.run_interval_seconds)}
          </div>
        </div>
        <DaemonLifecycleBadge status={daemon.lifecycle_status} />
        {/* Actions — stopPropagation so they don't trigger onEdit */}
        <div className="flex items-center" style={{ gap: 1 }} onClick={e => e.stopPropagation()}>
          {/* Power toggle — disabled when project_paused */}
          {ls === 'project_paused' ? (
            <span
              title="Project is paused — resume the project to re-enable this daemon"
              style={{
                width: 26, height: 26, padding: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'var(--amber)', opacity: 0.55, cursor: 'not-allowed', flexShrink: 0,
              }}
            >
              <Power size={13} />
            </span>
          ) : (
            <button
              className="icon-btn"
              title={daemon.enabled ? 'Disable' : 'Enable'}
              disabled={toggling}
              style={{ width: 26, height: 26, padding: 0, color: daemon.enabled ? 'var(--success)' : 'var(--text-muted)' }}
              onMouseEnter={e => { if (!toggling) e.currentTarget.style.color = daemon.enabled ? 'var(--danger)' : 'var(--success)' }}
              onMouseLeave={e => { if (!toggling) e.currentTarget.style.color = daemon.enabled ? 'var(--success)' : 'var(--text-muted)' }}
              onClick={() => doToggle(!daemon.enabled)}
            >{toggling ? <Loader2 size={13} className="animate-spin" /> : <Power size={13} />}</button>
          )}
          {/* Clone / Duplicate */}
          <button
            className="icon-btn"
            title="Duplicate daemon"
            style={{ width: 26, height: 26, padding: 0, color: cloneOpen ? 'var(--neural)' : 'var(--text-muted)' }}
            onMouseEnter={e => { if (!cloneOpen) e.currentTarget.style.color = 'var(--neural)' }}
            onMouseLeave={e => { if (!cloneOpen) e.currentTarget.style.color = 'var(--text-muted)' }}
            onClick={e => { e.stopPropagation(); setCloneOpen(o => !o); setCloneName(`${daemon.name}-copy`); setCloneProjectId(daemon.project_id ?? ''); setCloneError(null) }}
          ><Copy size={13} /></button>
          {/* Monitor — open execution viewer */}
          <button
            className="icon-btn"
            title="Open in chat"
            style={{ width: 26, height: 26, padding: 0 }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--eva-primary)')}
            onMouseLeave={e => (e.currentTarget.style.color = '')}
            onClick={() => onMonitor()}
          ><Monitor size={13} /></button>
          {/* Trigger */}
          {ls === 'project_paused' ? (
            <span
              title="Cannot trigger — project is paused. Resume the project first."
              style={{ width: 26, height: 26, padding: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--amber)', opacity: 0.55, cursor: 'not-allowed', flexShrink: 0 }}
            >
              <Play size={13} />
            </span>
          ) : (
            <button
              className="icon-btn"
              title="Trigger now"
              disabled={triggerState === 'loading'}
              style={{ width: 26, height: 26, padding: 0, color: triggerState === 'ok' ? 'var(--success)' : triggerState === 'error' ? 'var(--danger)' : 'var(--text-muted)' }}
              onMouseEnter={e => { if (triggerState === 'idle') e.currentTarget.style.color = 'var(--eva-primary)' }}
              onMouseLeave={e => { if (triggerState === 'idle') e.currentTarget.style.color = 'var(--text-muted)' }}
              onClick={handleTrigger}
            >
              {triggerState === 'loading' ? <Loader2 size={13} className="animate-spin" />
               : triggerState === 'ok'    ? <Check size={13} />
               : triggerState === 'error' ? <AlertTriangle size={13} />
               : <Play size={13} />}
            </button>
          )}
          {/* Delete */}
          <button
            className="icon-btn"
            title="Delete"
            style={{ width: 26, height: 26, padding: 0 }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
            onMouseLeave={e => (e.currentTarget.style.color = '')}
            onClick={() => setConfirmDel(true)}
          ><Trash2 size={13} /></button>
        </div>
        <ChevronRight size={14} style={{ color: 'var(--text-muted)', flexShrink: 0 }} onClick={onEdit} />
      </div>

      {/* Trigger feedback */}
      {triggerState === 'ok' && (
        <div className="flex items-center gap-2 px-3 py-1.5 text-xs"
          style={{ background: 'rgba(0,212,160,0.07)', borderTop: '1px solid rgba(0,212,160,0.15)', color: 'var(--success)' }}>
          <Check size={10} /> Triggered — running in background
        </div>
      )}
      {triggerState === 'error' && (
        <div className="flex items-center gap-2 px-3 py-1.5 text-xs"
          style={{ background: 'rgba(255,77,109,0.07)', borderTop: '1px solid rgba(255,77,109,0.15)', color: 'var(--danger)' }}>
          <AlertTriangle size={10} /> Trigger failed — check daemon is enabled
        </div>
      )}

      {/* Toggle error banner */}
      {toggleError && (
        <div className="flex items-center gap-2 px-3 py-2 text-xs"
          style={{ background: 'rgba(245,158,11,0.08)', borderTop: '1px solid rgba(245,158,11,0.2)', color: 'var(--amber)' }}>
          <AlertTriangle size={11} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{toggleError}</span>
          <button
            onClick={() => setToggleError(null)}
            style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex' }}
          ><X size={11} /></button>
        </div>
      )}

      {/* Clone popover */}
      {cloneOpen && (
        <div
          style={{ padding: '10px 14px', borderTop: '1px solid var(--border-dim)', background: 'rgba(124,108,243,0.03)', display: 'flex', flexDirection: 'column', gap: 8 }}
          onClick={e => e.stopPropagation()}
        >
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--neural)' }}>Duplicate daemon</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <input
              autoFocus
              value={cloneName}
              onChange={e => { setCloneName(e.target.value.replace(/\s+/g, '-').toLowerCase()); setCloneError(null) }}
              onKeyDown={e => { if (e.key === 'Enter') handleCloneSubmit(e as unknown as React.MouseEvent); if (e.key === 'Escape') setCloneOpen(false) }}
              placeholder={`${daemon.name}-copy`}
              style={{
                width: '100%', fontSize: 12, padding: '6px 10px', borderRadius: 8, outline: 'none', fontFamily: 'JetBrains Mono, monospace',
                background: 'var(--bg-surface2)', border: `1px solid ${cloneError ? 'rgba(255,77,109,0.4)' : 'var(--border-dim)'}`, color: 'var(--text-primary)',
              }}
            />
            <select
              value={cloneProjectId}
              onChange={e => setCloneProjectId(e.target.value)}
              style={{
                width: '100%', fontSize: 12, padding: '6px 10px', borderRadius: 8, outline: 'none',
                background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: cloneProjectId ? 'var(--text-primary)' : 'var(--text-muted)',
              }}
            >
              <option value="">— same project —</option>
              {(projects as import('@/api/projects').Project[]).filter(p => p.status !== 'archived').map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            {cloneError && <p style={{ fontSize: 11, color: 'var(--danger)', margin: 0 }}>{cloneError}</p>}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className="btn"
              disabled={cloning || !cloneName.trim()}
              onClick={handleCloneSubmit}
              style={{ fontSize: 11, background: 'rgba(124,108,243,0.12)', border: '1px solid rgba(124,108,243,0.3)', color: 'var(--neural)', display: 'flex', alignItems: 'center', gap: 4, padding: '5px 10px', borderRadius: 7 }}
            >
              {cloning ? <Loader2 size={10} className="animate-spin" /> : <Copy size={10} />}
              {cloning ? 'Duplicating…' : 'Duplicate'}
            </button>
            <button
              className="btn ghost"
              onClick={e => { e.stopPropagation(); setCloneOpen(false) }}
              style={{ fontSize: 11 }}
            >Cancel</button>
          </div>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: 0 }}>
            The duplicate will be <strong style={{ color: 'var(--text-secondary)' }}>disabled</strong> — enable it when ready.
          </p>
        </div>
      )}

      {/* Delete inline confirm */}
      {confirmDel && (
        <div className="flex items-center gap-2" style={{ padding: '8px 14px', borderTop: '1px solid var(--border-dim)', background: 'rgba(255,77,109,0.05)' }}>
          <AlertTriangle size={11} style={{ color: 'var(--danger)', flexShrink: 0 }} />
          <span style={{ flex: 1, fontSize: 12, color: 'var(--text-secondary)' }}>Delete <strong style={{ color: 'var(--text-primary)' }}>{daemon.name}</strong>?</span>
          <button className="btn danger" style={{ fontSize: 11 }} onClick={() => { onDelete(); setConfirmDel(false) }}>
            <Check size={10} /> Delete
          </button>
          <button className="btn ghost" style={{ fontSize: 11 }} onClick={() => setConfirmDel(false)}>Cancel</button>
        </div>
      )}
    </div>
  )
}
