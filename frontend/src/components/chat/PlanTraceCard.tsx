import { Loader2, CheckCircle2, XCircle, RefreshCw, SkipForward } from 'lucide-react'
import InlineDisclosure from './InlineDisclosure'
import type { PlanTraceBlock, PlanTraceStepRow } from '@/store/chatStore'

interface Props {
  block: PlanTraceBlock
}

function statusLabel(status: PlanTraceStepRow['status']): string {
  switch (status) {
    case 'running':        return 'running…'
    case 'verifying':      return 'verifying…'
    case 'completed':      return 'done'
    case 'failed':         return 'failed'
    case 'replanning':     return 'replanning…'
    case 'replan_skipped': return 'skipped replan'
    case 'cancelled':      return 'cancelled'
    case 'skipped':        return 'skipped'
  }
}

function StatusIcon({ status }: { status: PlanTraceStepRow['status'] }) {
  const size = 10
  switch (status) {
    case 'running':
    case 'verifying':
      return <Loader2 size={size} className="animate-spin" style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
    case 'completed':
      return <CheckCircle2 size={size} style={{ color: 'var(--success)', flexShrink: 0 }} />
    case 'failed':
      return <XCircle size={size} style={{ color: 'var(--danger)', flexShrink: 0 }} />
    case 'replanning':
      return <RefreshCw size={size} className="animate-spin" style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
    case 'replan_skipped':
    case 'skipped':
      // Neutral, not green/red — neither freshly-verified work nor a failure
      return <SkipForward size={size} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
    case 'cancelled':
      return <XCircle size={size} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
  }
}

export default function PlanTraceCard({ block }: Props) {
  const { goal, totalSteps, planSteps, steps, complete, cancelled } = block

  const current = steps[steps.length - 1]

  let summary: string
  if (cancelled) {
    summary = 'Plan cancelled'
  } else if (complete) {
    const parts = [`Completed plan · ${complete.stepsCompleted}/${totalSteps || steps.length} steps`]
    if (complete.stepsFailed > 0) parts.push(`${complete.stepsFailed} failed`)
    if (complete.stepsSkipped > 0) parts.push(`${complete.stepsSkipped} skipped`)
    if (complete.replanCount > 0) parts.push(`${complete.replanCount} replan${complete.replanCount > 1 ? 's' : ''}`)
    summary = parts.join(' · ')
  } else if (!current) {
    summary = 'Planning…'
  } else {
    summary = `Step ${current.index + 1}/${totalSteps || '?'}: ${current.title} · ${statusLabel(current.status)}`
  }

  const anyFailed = steps.some(s => s.status === 'failed') || (complete?.stepsFailed ?? 0) > 0
  const hasDetail = steps.length > 0 || !!planSteps

  return (
    <InlineDisclosure
      summary={summary}
      disabled={!hasDetail}
      tone={cancelled || anyFailed ? 'danger' : 'default'}
    >
      <div className="flex flex-col gap-2.5">
        {goal && (
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.5 }}>
            {goal}
          </p>
        )}

        {planSteps && planSteps.length > 0 && (
          <div className="flex flex-col gap-0.5">
            {planSteps.map(s => (
              <div key={s.index} className="text-xs" style={{ color: 'var(--text-dim)', lineHeight: 1.6 }}>
                {s.index + 1}. {s.title}
                {s.tool_hint && <span style={{ fontFamily: 'var(--font-mono)' }}> · {s.tool_hint}</span>}
              </div>
            ))}
          </div>
        )}

        {steps.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {steps.map(row => (
              <div key={row.index} className="flex flex-col gap-0.5">
                <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
                  <StatusIcon status={row.status} />
                  <span style={row.status === 'skipped' ? { color: 'var(--text-dim)', textDecoration: 'line-through' } : undefined}>
                    Step {row.index + 1}: {row.title}
                  </span>
                  <span style={{ color: row.status === 'failed' ? 'var(--danger)' : 'var(--text-dim)' }}>
                    · {statusLabel(row.status)}
                  </span>
                </div>
                {(row.verificationSummary || row.verificationIssues || row.replanReason || row.errorDetail) && (
                  <div style={{ marginLeft: 16, fontSize: 11.5, color: 'var(--text-dim)', lineHeight: 1.55 }}>
                    {row.errorDetail && <div>{row.errorDetail}</div>}
                    {row.verificationSummary && <div>{row.verificationSummary}</div>}
                    {row.verificationIssues && <div style={{ color: 'var(--danger)' }}>{row.verificationIssues}</div>}
                    {row.replanReason && <div>{row.replanReason}</div>}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </InlineDisclosure>
  )
}
