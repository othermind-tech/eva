import { useState } from 'react'
import { Loader2, AlertTriangle, History, StopCircle } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { daemonsApi } from '@/api/daemons'
import type { DaemonExecutionSummary, DaemonLifecycleStatus } from '@/types/api'
import { formatTokens } from '@/lib/utils'
import { formatDurationMs, formatRelativeTime, daemonRunStatusConfig } from './daemonFormat'

// ── Daemon run history tab ────────────────────────────────────────────────────
export default function DaemonRunHistoryTab({ daemonName, lifecycleStatus }: { daemonName: string; lifecycleStatus: DaemonLifecycleStatus | undefined }) {
  const qc = useQueryClient()
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const toggleExpand = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const { data: runs = [], isLoading, isError } = useQuery<DaemonExecutionSummary[]>({
    queryKey:        ['daemon-executions', daemonName],
    queryFn:         () => daemonsApi.getExecutions(daemonName, 20),
    refetchInterval: (query) => {
      const list = (query.state.data ?? []) as DaemonExecutionSummary[]
      return list.some(r => r.status === 'running') ? 5_000 : false
    },
  })

  const { mutate: cancelRun, isPending: cancelling } = useMutation({
    mutationFn: () => daemonsApi.cancel(daemonName),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['daemon-executions', daemonName] }); qc.invalidateQueries({ queryKey: ['daemons'] }) },
  })

  if (isLoading) return (
    <div className="flex items-center gap-2 py-8 text-xs" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={12} className="animate-spin" /> Loading history…
    </div>
  )

  if (isError) return (
    <div className="flex items-center gap-2 py-6 text-xs" style={{ color: 'var(--danger)' }}>
      <AlertTriangle size={12} /> Failed to load execution history
    </div>
  )

  if (runs.length === 0) return (
    <div className="flex flex-col items-center gap-2 py-10">
      <History size={22} style={{ color: 'var(--text-muted)' }} />
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No executions recorded yet</p>
    </div>
  )

  return (
    <div className="flex flex-col gap-2">
      {lifecycleStatus === 'running' && (
        <div className="flex items-center justify-between px-3 py-2 rounded-lg text-xs"
          style={{ background: 'rgba(0,201,255,0.07)', border: '1px solid rgba(0,201,255,0.2)' }}
        >
          <div className="flex items-center gap-1.5" style={{ color: 'var(--eva-primary)' }}>
            <Loader2 size={10} className="animate-spin" /> Daemon is running
          </div>
          <button onClick={() => cancelRun()} disabled={cancelling}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold"
            style={{ background: 'var(--danger)', color: '#fff' }}
          >
            {cancelling ? <Loader2 size={9} className="animate-spin" /> : <StopCircle size={9} />} Cancel
          </button>
        </div>
      )}

      {runs.map(run => {
        const sc          = daemonRunStatusConfig(run.status)
        const isFailed    = run.status === 'failed'
        const hasError    = isFailed && !!run.error_message
        const isExpanded  = expanded.has(run.execution_id)
        const errPreview  = run.error_message ?? ''
        const errTrimmed  = errPreview.length > 200 ? errPreview.slice(0, 200) + '…' : errPreview
        const hasMore     = errPreview.length > 200

        // Token + model meta line — only render when data is present
        // Section 103 - reasoning is a subset of completion, so adding it on top double-counts
        const totalTok = (run.prompt_tokens ?? 0) + (run.completion_tokens ?? 0)
        const showMeta = !!(run.model_used || run.provider_used || totalTok > 0)

        return (
          <div
            key={run.execution_id}
            className="rounded-lg overflow-hidden"
            style={{
              background: 'var(--bg-surface1)',
              border: `1px solid ${isFailed ? 'rgba(255,77,109,0.25)' : 'var(--border-dim)'}`,
            }}
          >
            {/* ── Row ── */}
            <div className="px-3 py-2.5 flex flex-col gap-1.5">
              {/* Row 1: status badge + duration */}
              <div className="flex items-center justify-between">
                <span style={{ fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 999, background: sc.bg, color: sc.color, border: `1px solid ${sc.border}`, whiteSpace: 'nowrap' }}>
                  {sc.label}
                </span>
                <span className="font-mono" style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                  {formatDurationMs(run.duration_ms)}
                </span>
              </div>

              {/* Row 2: execution ID + relative time */}
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono truncate" style={{ fontSize: 10, color: 'var(--text-muted)' }}>{run.execution_id}</span>
                <span style={{ fontSize: 10, color: 'var(--text-muted)', flexShrink: 0 }}>{formatRelativeTime(run.created_at)}</span>
              </div>

              {/* Row 3: model / provider / tokens — only when data available */}
              {showMeta && (
                <div className="flex items-center gap-2 flex-wrap" style={{ marginTop: 1 }}>
                  {(run.provider_used || run.model_used) && (
                    <span className="font-mono" style={{ fontSize: 9, color: 'var(--text-dim)', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 4, padding: '1px 5px' }}>
                      {[run.provider_used, run.model_used].filter(Boolean).join(' / ')}
                    </span>
                  )}
                  {totalTok > 0 && (
                    <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>
                      {formatTokens(totalTok)} tok
                    </span>
                  )}
                </div>
              )}

              {/* Row 4: error chip — failed runs only */}
              {isFailed && run.termination_reason && (
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span style={{
                    fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 999,
                    background: 'rgba(255,77,109,0.12)', color: 'var(--danger)',
                    border: '1px solid rgba(255,77,109,0.3)', whiteSpace: 'nowrap',
                  }}>
                    {run.termination_reason}
                  </span>
                  {hasError && (
                    <button
                      onClick={() => toggleExpand(run.execution_id)}
                      className="font-mono"
                      style={{ fontSize: 9, color: 'var(--text-muted)', background: 'none', border: 'none', padding: 0, cursor: 'pointer', textDecoration: 'underline' }}
                    >
                      {isExpanded ? 'hide' : 'details'}
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* ── Expandable error block ── */}
            {hasError && isExpanded && (
              <div
                style={{
                  borderTop: '1px solid rgba(255,77,109,0.15)',
                  background: 'rgba(255,77,109,0.04)',
                  padding: '8px 12px',
                }}
              >
                <pre style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 10,
                  color: 'var(--danger)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  margin: 0,
                  lineHeight: 1.55,
                }}>
                  {isExpanded && !hasMore ? errPreview : errTrimmed}
                </pre>
                {hasMore && (
                  <button
                    onClick={e => { e.stopPropagation(); /* already fully shown when expanded */ }}
                    style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 4, background: 'none', border: 'none', padding: 0, cursor: 'default' }}
                  >
                    {errPreview.length} chars total
                  </button>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
