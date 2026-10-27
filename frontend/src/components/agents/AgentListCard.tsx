import { useState } from 'react'
import { Bot, ChevronRight, Copy, RotateCcw, Trash2, Check, X, AlertTriangle } from 'lucide-react'
import type { SubagentConfigResponse } from '@/types/api'

// ── Per-card row in agents list ────────────────────────────────────────────────
export default function AgentListCard({
  agent, onEdit,
  onClone, onReset, onDelete, projectBadge,
}: {
  agent:        SubagentConfigResponse
  onEdit:       () => void
  onClone:      (newName: string) => void
  onReset:      () => void
  onDelete:     () => void
  projectBadge?: string   // optional project name chip (64-D)
}) {
  const [cloneMode,    setCloneMode]    = useState(false)
  const [cloneName,    setCloneName]    = useState('')
  const [cloneError,   setCloneError]   = useState<string | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const [confirmDel,   setConfirmDel]   = useState(false)

  const submitClone = () => {
    const t = cloneName.trim()
    if (!t) return
    if (!/^[a-z0-9_-]+$/.test(t)) { setCloneError('Lowercase, numbers, hyphens, underscores only'); return }
    setCloneError(null)
    onClone(t)
    setCloneMode(false)
    setCloneName('')
  }

  return (
    <div
      className="kn-card"
      style={{
        cursor: 'pointer',
        borderColor: agent.is_default ? 'rgba(0,201,255,0.15)' : undefined,
      }}
    >
      <div className="kn-head" onClick={onEdit}>
        <div className="row-icon" style={{ background: 'rgba(124,108,243,0.12)', color: 'var(--neural)', width: 32, height: 32, borderRadius: 9, flexShrink: 0 }}>
          <Bot size={14} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text-primary)' }}>
              {agent.name}
            </span>
            {agent.is_default && (
              <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase', padding: '1px 6px', borderRadius: 99, background: 'rgba(0,201,255,0.1)', color: 'var(--eva-primary)', border: '1px solid rgba(0,201,255,0.25)', flexShrink: 0 }}>
                default
              </span>
            )}
            {projectBadge && (
              <span style={{ fontSize: 9, fontWeight: 600, padding: '1px 6px', borderRadius: 99, background: 'rgba(0,212,160,0.1)', color: 'var(--success)', border: '1px solid rgba(0,212,160,0.25)', flexShrink: 0, maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {projectBadge}
              </span>
            )}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
            {agent.description
              ? `${agent.description.slice(0, 60)}${agent.description.length > 60 ? '…' : ''}`
              : 'No description'
            } · {agent.tools?.length ?? 0} tools
          </div>
        </div>
        <span className="chip chip-sm" style={{ background: 'rgba(124,108,243,0.12)', color: 'var(--neural)', border: '1px solid rgba(124,108,243,0.25)', flexShrink: 0 }}>
          {agent.max_steps} steps
        </span>
        {/* Action buttons — stopPropagation so they don't trigger onEdit */}
        <div className="flex items-center" style={{ gap: 1 }} onClick={e => e.stopPropagation()}>
          <button
            className="icon-btn"
            title="Clone agent"
            style={{ width: 26, height: 26, padding: 0, color: cloneMode ? 'var(--eva-primary)' : undefined }}
            onClick={() => { setCloneMode(m => !m); setCloneName(''); setCloneError(null); setConfirmReset(false); setConfirmDel(false) }}
          ><Copy size={13} /></button>
          {agent.is_default ? (
            <button
              className="icon-btn"
              title="Reset to built-in definition"
              style={{ width: 26, height: 26, padding: 0 }}
              onMouseEnter={e => (e.currentTarget.style.color = 'var(--amber)')}
              onMouseLeave={e => (e.currentTarget.style.color = '')}
              onClick={() => { setConfirmReset(true); setConfirmDel(false); setCloneMode(false) }}
            ><RotateCcw size={13} /></button>
          ) : (
            <button
              className="icon-btn"
              title="Delete"
              style={{ width: 26, height: 26, padding: 0 }}
              onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
              onMouseLeave={e => (e.currentTarget.style.color = '')}
              onClick={() => { setConfirmDel(true); setConfirmReset(false); setCloneMode(false) }}
            ><Trash2 size={13} /></button>
          )}
        </div>
        <ChevronRight size={14} style={{ color: 'var(--text-muted)', flexShrink: 0 }} onClick={onEdit} />
      </div>

      {/* Clone inline */}
      {cloneMode && (
        <div style={{ padding: '8px 14px 10px', borderTop: '1px solid var(--border-dim)', background: 'rgba(0,201,255,0.03)' }}>
          <p style={{ fontSize: 11.5, color: 'var(--text-secondary)', marginBottom: 6 }}>Clone as new custom agent:</p>
          <div className="flex items-center gap-2">
            <input
              autoFocus
              className="form-input"
              style={{ flex: 1, fontSize: 12, fontFamily: 'JetBrains Mono, monospace' }}
              value={cloneName}
              onChange={e => { setCloneName(e.target.value.replace(/\s+/g, '_').toLowerCase()); setCloneError(null) }}
              onKeyDown={e => { if (e.key === 'Enter') submitClone(); if (e.key === 'Escape') { setCloneMode(false); setCloneName('') } }}
              placeholder={`${agent.name}_custom`}
            />
            <button className="btn ghost" onClick={submitClone} disabled={!cloneName.trim()}>
              <Check size={11} /> Clone
            </button>
            <button className="icon-btn" onClick={() => { setCloneMode(false); setCloneName('') }}><X size={12} /></button>
          </div>
          {cloneError && <p style={{ fontSize: 11, color: 'var(--danger)', marginTop: 4 }}>{cloneError}</p>}
        </div>
      )}

      {/* Reset inline confirm */}
      {confirmReset && (
        <div className="flex items-center gap-2" style={{ padding: '8px 14px', borderTop: '1px solid var(--border-dim)', background: 'rgba(245,158,11,0.05)' }}>
          <RotateCcw size={11} style={{ color: 'var(--amber)', flexShrink: 0 }} />
          <span style={{ flex: 1, fontSize: 12, color: 'var(--text-secondary)' }}>Reset to built-in definition? Custom edits will be lost.</span>
          <button className="btn ghost" style={{ fontSize: 11, color: 'var(--amber)', borderColor: 'rgba(245,158,11,0.4)' }} onClick={() => { onReset(); setConfirmReset(false) }}>
            <Check size={10} /> Reset
          </button>
          <button className="btn ghost" style={{ fontSize: 11 }} onClick={() => setConfirmReset(false)}>Cancel</button>
        </div>
      )}

      {/* Delete inline confirm */}
      {confirmDel && (
        <div className="flex items-center gap-2" style={{ padding: '8px 14px', borderTop: '1px solid var(--border-dim)', background: 'rgba(255,77,109,0.05)' }}>
          <AlertTriangle size={11} style={{ color: 'var(--danger)', flexShrink: 0 }} />
          <span style={{ flex: 1, fontSize: 12, color: 'var(--text-secondary)' }}>Delete <strong style={{ color: 'var(--text-primary)' }}>{agent.name}</strong>?</span>
          <button className="btn danger" style={{ fontSize: 11 }} onClick={() => { onDelete(); setConfirmDel(false) }}>
            <Check size={10} /> Delete
          </button>
          <button className="btn ghost" style={{ fontSize: 11 }} onClick={() => setConfirmDel(false)}>Cancel</button>
        </div>
      )}
    </div>
  )
}
