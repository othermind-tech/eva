import InlineDisclosure from './InlineDisclosure'
import type { SubagentActivityBlock } from '@/store/chatStore'

interface Props {
  block:    SubagentActivityBlock
  onToggle: () => void
}

function formatDuration(startMs: number, endMs: number): string {
  const ms = endMs - startMs
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  return `${m}m ${(s % 60).toFixed(0)}s`
}

export default function SubagentActivityCard({ block, onToggle }: Props) {
  const { agentName, status, events, startedAt, endedAt, collapsed } = block

  const isRunning = status === 'running'
  const isFailed  = status === 'failed'

  const duration = !isRunning && endedAt ? formatDuration(startedAt, endedAt) : null
  const contentEvents = events.filter(e => e.event === 'text' || e.event === 'tool_call')

  const summary = (
    <span style={{ color: isFailed ? 'var(--danger)' : 'var(--text-muted)' }}>
      Delegated to <span style={{ fontFamily: 'var(--font-mono)' }}>{agentName}</span>
      <span style={{ color: 'var(--text-dim)' }}>
        {' · '}{isRunning ? 'running…' : isFailed ? `failed · ${duration ?? '?'}` : `done · ${duration}`}
      </span>
    </span>
  )

  return (
    <InlineDisclosure
      summary={summary}
      tone={isFailed ? 'danger' : 'default'}
      open={!collapsed}
      onOpenChange={onToggle}
      disabled={contentEvents.length === 0 && !isRunning}
    >
      {contentEvents.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {contentEvents.map((e, i) => (
            <div key={i} className="flex items-start gap-1.5 text-xs">
              {e.event === 'tool_call' ? (
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                  {e.toolName ?? 'tool'}
                </span>
              ) : (
                <>
                  <span style={{ color: 'var(--text-dim)' }}>↳</span>
                  <p className="line-clamp-3" style={{ color: 'var(--text-dim)', lineHeight: 1.5 }}>
                    {e.content}
                  </p>
                </>
              )}
            </div>
          ))}
        </div>
      ) : isRunning ? (
        <p className="text-xs" style={{ color: 'var(--text-dim)' }}>Starting…</p>
      ) : null}
    </InlineDisclosure>
  )
}
