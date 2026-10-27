// Section 84/85 (P-19) — persistent "what's running/paused right now" view,
// distinct from the transient Notifications feed it sits alongside in the
// same drawer. Kept fresh via useEventStream's SSE-driven invalidation of
// ['wakeups'] / ['executions-running'], with a light poll as insurance.
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Clock, PauseCircle, Square, Pin, PinOff } from 'lucide-react'
import { wakeupsApi } from '@/api/wakeups'
import { executionsApi } from '@/api/executions'
import { processesApi } from '@/api/processes'
import type { WakeupSummary, RunningExecutionSummary, APIError } from '@/types/api'

const POLL_MS = 20_000

function formatWakeAt(iso: string | null): string {
  if (!iso) return 'no deadline set'
  const d = new Date(iso)
  const diffMs = d.getTime() - Date.now()
  if (diffMs <= 0) return 'due now'
  const mins = Math.round(diffMs / 60_000)
  if (mins < 60) return `in ${mins}m`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `in ${hrs}h`
  return d.toLocaleString()
}

function relativeStarted(iso: string | null): string {
  if (!iso) return ''
  const diffSec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  const mins = Math.floor(diffSec / 60)
  if (mins < 60) return `${mins}m ago`
  return `${Math.floor(mins / 60)}h ago`
}

// Section 90 — GET /executions/running only ever sends the raw execution_mode
// enum, never a command/label for background_process rows (that field isn't
// in the API response, even though BackgroundProcess.command exists in the
// DB — see the Section 90 Frontend Implementation note in
// frontend_changes_needed.md for the backend ask). PID is the only
// per-process detail actually available today, so surface that instead of
// pretending we have a real name.
function modeLabel(mode: string): string {
  switch (mode) {
    case 'conversational':        return 'Chat execution'
    case 'plan_execute':          return 'Plan & Execute'
    case 'daemon':                return 'Daemon run'
    case 'subagent_background':   return 'Background subagent'
    case 'background_process':    return 'Background process'
    default:                      return mode
  }
}

function WakeupRow({ wakeup }: { wakeup: WakeupSummary }) {
  const qc = useQueryClient()

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['wakeups'] })
    void qc.invalidateQueries({ queryKey: ['executions-running'] })
  }

  const { mutate: resume, isPending: resuming } = useMutation({
    mutationFn: () => wakeupsApi.resume(wakeup.id),
    onSuccess: invalidate,
    onError:   invalidate,   // 409 = already resolved elsewhere — refetch shows the real state
  })
  const { mutate: cancel, isPending: cancelling } = useMutation({
    mutationFn: () => wakeupsApi.cancel(wakeup.id),
    onSuccess: invalidate,
    onError:   invalidate,
  })

  const busy = resuming || cancelling

  const watchLine = wakeup.watch_execution_id
    ? wakeup.watched_status
      ? `waiting on ${wakeup.watch_daemon_name ?? wakeup.watch_execution_id} · ${wakeup.watched_status}`
      : `waiting on ${wakeup.watch_daemon_name ?? wakeup.watch_execution_id} · check manually`
    : `wakes ${formatWakeAt(wakeup.wake_at)}`

  return (
    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-dim)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <PauseCircle size={14} style={{ color: 'var(--amber)', flexShrink: 0, marginTop: 2 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ margin: 0, fontWeight: 600, fontSize: 12.5, color: 'var(--text-primary)' }}>
            {wakeup.conversation_title ?? 'Untitled conversation'}
          </p>
          <p style={{ margin: '3px 0 0', fontSize: 11.5, color: 'var(--text-secondary)', lineHeight: 1.4 }}>
            {wakeup.reason}
          </p>
          <p style={{ margin: '4px 0 0', fontSize: 10.5, color: 'var(--text-muted)' }}>
            {watchLine}
          </p>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 6, marginTop: 8, marginLeft: 22 }}>
        <button
          onClick={() => resume()}
          disabled={busy}
          style={{
            fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 6,
            background: 'rgba(0,201,255,0.12)', border: '1px solid rgba(0,201,255,0.3)',
            color: 'var(--eva-primary)', cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.6 : 1,
          }}
        >
          {resuming ? <Loader2 size={10} className="animate-spin" style={{ display: 'inline', marginRight: 4 }} /> : null}
          Resume now
        </button>
        <button
          onClick={() => cancel()}
          disabled={busy}
          style={{
            fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 6,
            background: 'transparent', border: '1px solid var(--border-mid)',
            color: 'var(--text-muted)', cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.6 : 1,
          }}
        >
          {cancelling ? <Loader2 size={10} className="animate-spin" style={{ display: 'inline', marginRight: 4 }} /> : null}
          Cancel
        </button>
      </div>
    </div>
  )
}

function RunningRow({ execution }: { execution: RunningExecutionSummary }) {
  const qc = useQueryClient()
  const [note, setNote] = useState<string | null>(null)

  // Section 90 (V6.49 Phase 3) — pid is only ever set on background_process
  // rows; every other execution_mode has no direct stop primitive here.
  const isBackgroundProcess = execution.pid != null

  const { mutate: stop, isPending: stopping } = useMutation({
    mutationFn: () => processesApi.stop(execution.execution_id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['executions-running'] })
    },
    onError: (err: APIError) => {
      // 409 = already stopped elsewhere (cleanup sweep, a second click) — not
      // a real failure. Surface the backend's human-readable detail and
      // refetch so the row's real state (gone) shows up either way.
      if (err.status === 409 && typeof err.detail === 'string') {
        setNote(err.detail)
        setTimeout(() => setNote(null), 3000)
      }
      void qc.invalidateQueries({ queryKey: ['executions-running'] })
    },
  })

  // Section 96 (V6.58 Phase 2) — pins/unpins a background process to survive
  // an EVA quit. Pure DB flag; same ownership/error model as Stop.
  const { mutate: setKeepAlive, isPending: pinning } = useMutation({
    mutationFn: (keepAlive: boolean) => processesApi.setKeepAlive(execution.execution_id, keepAlive),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['executions-running'] })
    },
    onError: (err: APIError) => {
      if (typeof err.detail === 'string') {
        setNote(err.detail)
        setTimeout(() => setNote(null), 3000)
      }
      void qc.invalidateQueries({ queryKey: ['executions-running'] })
    },
  })

  return (
    <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border-dim)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--success)', boxShadow: '0 0 6px rgba(0,212,160,0.5)', flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-primary)' }}>
            {modeLabel(execution.execution_mode)}
          </p>
          {execution.pid != null && (
            <p
              title={execution.command ?? undefined}
              style={{
                margin: '2px 0 0', fontSize: 10.5, color: 'var(--text-muted)', fontFamily: '"JetBrains Mono", monospace',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}
            >
              {execution.command ?? `PID ${execution.pid}`}
            </p>
          )}
        </div>
        <span style={{ fontSize: 10.5, color: 'var(--text-muted)', flexShrink: 0 }}>
          {relativeStarted(execution.started_at ?? null)}
        </span>
        {isBackgroundProcess && (
          <button
            onClick={() => setKeepAlive(!execution.keep_alive_on_app_shutdown)}
            disabled={pinning}
            title={execution.keep_alive_on_app_shutdown ? 'Will keep running after EVA closes — click to unpin' : 'Keep running after EVA closes'}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              width: 22, height: 22, borderRadius: 6, flexShrink: 0,
              background: execution.keep_alive_on_app_shutdown ? 'rgba(0,201,255,0.1)' : 'transparent',
              border: execution.keep_alive_on_app_shutdown ? '1px solid rgba(0,201,255,0.28)' : '1px solid var(--border-dim)',
              color: execution.keep_alive_on_app_shutdown ? 'var(--eva-primary)' : 'var(--text-muted)',
              cursor: pinning ? 'default' : 'pointer', opacity: pinning ? 0.6 : 1,
            }}
          >
            {pinning
              ? <Loader2 size={10} className="animate-spin" />
              : execution.keep_alive_on_app_shutdown ? <Pin size={11} /> : <PinOff size={11} />}
          </button>
        )}
        {isBackgroundProcess && (
          <button
            onClick={() => stop()}
            disabled={stopping}
            style={{
              display: 'flex', alignItems: 'center', gap: 4,
              fontSize: 11, fontWeight: 600, padding: '4px 8px', borderRadius: 6,
              background: 'rgba(255,77,109,0.1)', border: '1px solid rgba(255,77,109,0.28)',
              color: 'var(--danger)', cursor: stopping ? 'default' : 'pointer', opacity: stopping ? 0.6 : 1, flexShrink: 0,
            }}
          >
            {stopping ? <Loader2 size={10} className="animate-spin" /> : <Square size={10} />}
            Stop
          </button>
        )}
      </div>
      {note && (
        <p style={{ margin: '6px 0 0 14px', fontSize: 10.5, color: 'var(--text-muted)' }}>
          {note}
        </p>
      )}
    </div>
  )
}

export default function ActivityTab() {
  const { data: wakeups = [] } = useQuery({
    queryKey: ['wakeups'],
    queryFn:  wakeupsApi.list,
    refetchInterval: POLL_MS,
  })
  const { data: running = [] } = useQuery({
    queryKey: ['executions-running'],
    queryFn:  executionsApi.listRunning,
    refetchInterval: POLL_MS,
  })

  // GET /executions/running already includes awaiting_wakeup rows — the
  // wakeups list above renders those with richer detail, so exclude them
  // here rather than showing the same execution twice.
  const runningOnly = running.filter(e => e.status !== 'awaiting_wakeup')

  const empty = wakeups.length === 0 && runningOnly.length === 0

  return (
    <div style={{ flex: 1, overflowY: 'auto' }}>
      {empty ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12, color: 'var(--text-muted)' }}>
          <Clock size={36} style={{ opacity: 0.4 }} />
          <span style={{ fontSize: 13 }}>Nothing running or paused right now</span>
        </div>
      ) : (
        <>
          {wakeups.length > 0 && (
            <>
              <p style={{ margin: 0, padding: '10px 16px 4px', fontSize: 10.5, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-faint)' }}>
                Paused · {wakeups.length}
              </p>
              {wakeups.map(w => <WakeupRow key={w.id} wakeup={w} />)}
            </>
          )}
          {runningOnly.length > 0 && (
            <>
              <p style={{ margin: 0, padding: '10px 16px 4px', fontSize: 10.5, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-faint)' }}>
                Running · {runningOnly.length}
              </p>
              {runningOnly.map(e => <RunningRow key={e.execution_id} execution={e} />)}
            </>
          )}
        </>
      )}
    </div>
  )
}
