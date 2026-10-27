import type { DaemonRunStatus } from '@/types/api'

// Trigger button state shared between DaemonListCard and DaemonEditView's
// "Run now" action.
export type DaemonTriggerState = 'idle' | 'loading' | 'ok' | 'error'

export function formatInterval(seconds: number | null | undefined): string {
  if (!seconds) return 'Manual only'
  if (seconds < 60)    return `every ${seconds}s`
  if (seconds < 3600)  return `every ${Math.round(seconds / 60)}m`
  if (seconds < 86400) return `every ${Math.round(seconds / 3600)}h`
  return `every ${Math.round(seconds / 86400)}d`
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d    = new Date(iso)
  const now  = new Date()
  const diff = d.getTime() - now.getTime()
  const abs  = Math.abs(diff)
  const mins  = Math.round(abs / 60_000)
  const hrs   = Math.round(abs / 3_600_000)
  const days  = Math.round(abs / 86_400_000)
  if (abs < 60_000)     return diff > 0 ? 'in a moment' : 'just now'
  if (abs < 3_600_000)  return diff > 0 ? `in ${mins}m`  : `${mins}m ago`
  if (abs < 86_400_000) return diff > 0 ? `in ${hrs}h`   : `${hrs}h ago`
  return diff > 0 ? `in ${days}d` : `${days}d ago`
}

export function formatDurationMs(ms: number | null | undefined): string {
  if (!ms) return '—'
  if (ms < 1000)   return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}

export function daemonRunStatusConfig(status: DaemonRunStatus): { label: string; bg: string; color: string; border: string } {
  switch (status) {
    case 'running':   return { label: '● Running',  bg: 'rgba(0,201,255,0.12)',  color: 'var(--eva-primary)', border: 'rgba(0,201,255,0.25)' }
    case 'completed': return { label: '✓ Done',      bg: 'rgba(0,212,160,0.1)',   color: 'var(--success)',     border: 'rgba(0,212,160,0.25)' }
    case 'failed':    return { label: '✕ Failed',    bg: 'rgba(255,77,109,0.1)',  color: 'var(--danger)',      border: 'rgba(255,77,109,0.25)' }
    case 'cancelled': return { label: '⊘ Cancelled', bg: 'rgba(245,158,11,0.1)', color: 'var(--amber)',       border: 'rgba(245,158,11,0.25)' }
    case 'aborted':   return { label: '⊘ Aborted',   bg: 'rgba(245,158,11,0.1)', color: 'var(--amber)',       border: 'rgba(245,158,11,0.25)' }
    default:          return { label: status,         bg: 'var(--bg-surface3)',   color: 'var(--text-muted)',  border: 'var(--border-dim)' }
  }
}
