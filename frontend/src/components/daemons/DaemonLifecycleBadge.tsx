import type { DaemonLifecycleStatus } from '@/types/api'

export default function DaemonLifecycleBadge({ status }: { status: DaemonLifecycleStatus | undefined }) {
  if (!status) return null
  const cfg: Record<DaemonLifecycleStatus, { bg: string; color: string; label: string; border: string }> = {
    idle:               { bg: 'var(--bg-surface3)',          color: 'var(--text-muted)',   label: 'Idle',            border: 'var(--border-dim)' },
    running:            { bg: 'rgba(0,201,255,0.12)',        color: 'var(--eva-primary)',  label: '● Running',       border: 'rgba(0,201,255,0.25)' },
    paused:             { bg: 'rgba(245,158,11,0.1)',        color: 'var(--amber)',        label: '⏸ Paused',       border: 'rgba(245,158,11,0.25)' },
    retrying:           { bg: 'rgba(245,158,11,0.1)',        color: 'var(--amber)',        label: '↻ Retrying',      border: 'rgba(245,158,11,0.25)' },
    permanently_failed: { bg: 'rgba(255,77,109,0.1)',        color: 'var(--danger)',       label: '✕ Failed',        border: 'rgba(255,77,109,0.25)' },
    project_paused:     { bg: 'rgba(245,158,11,0.12)',       color: 'var(--amber)',        label: '⊘ Project Paused', border: 'rgba(245,158,11,0.3)' },
  }
  const c = cfg[status]
  return (
    <span style={{ fontSize: 9, fontWeight: 600, padding: '2px 6px', borderRadius: 999, background: c.bg, color: c.color, border: `1px solid ${c.border}`, flexShrink: 0, whiteSpace: 'nowrap' }}>
      {c.label}
    </span>
  )
}
