import InlineDisclosure from './InlineDisclosure'
import { CheckCircle2, XCircle } from 'lucide-react'
import type { ToolCallBlock } from '@/store/chatStore'
import type { CoordinateResult, CoordinatePhaseDetail, CoordinatePhaseAgentResult } from '@/types/api'

interface Props {
  block:         ToolCallBlock
  subagentCount: number
}

export default function CoordinatorPhaseCard({ block, subagentCount }: Props) {
  const isDone = block.result !== undefined

  if (!isDone) {
    const summary = (
      <span className="flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }}>
        Coordinating…
        {subagentCount > 0 && <span style={{ color: 'var(--text-dim)' }}>· {subagentCount} {subagentCount === 1 ? 'agent' : 'agents'}</span>}
      </span>
    )
    return <InlineDisclosure summary={summary} disabled />
  }

  if (block.is_error || !block.result || typeof block.result !== 'object') {
    const msg = typeof block.result === 'string' ? block.result : undefined
    return (
      <InlineDisclosure
        tone="danger"
        disabled={!msg}
        summary={<span>Coordinator · failed</span>}
      >
        {msg && (
          <pre style={{ margin: 0, fontSize: 11.5, color: 'var(--danger)', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {msg}
          </pre>
        )}
      </InlineDisclosure>
    )
  }

  const r = block.result as CoordinateResult
  const phases_run  = r.phases_run  ?? 0
  const total_tasks = r.total_tasks ?? 0
  const completed   = r.completed   ?? 0
  const failed      = r.failed      ?? 0
  const phaseList   = r.phase_details ?? []

  const summary = (
    <span style={{ color: 'var(--text-muted)' }}>
      Coordinated{phases_run > 0 ? ` ${phases_run} phase${phases_run !== 1 ? 's' : ''}` : ''}
      {r.strategy && <span style={{ color: 'var(--text-dim)' }}> ({r.strategy})</span>}
      {' · '}
      <span style={{ color: failed > 0 ? 'var(--danger)' : 'var(--text-muted)' }}>{completed}/{total_tasks}</span>
      {failed > 0 && <span style={{ color: 'var(--danger)' }}> · {failed} failed</span>}
    </span>
  )

  return (
    <InlineDisclosure summary={summary} tone={failed > 0 ? 'danger' : 'default'}>
      <div className="flex flex-col gap-2.5">
        {r.goal_summary && (
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.55 }}>
            {r.goal_summary}
          </p>
        )}

        {phaseList.map(phase => (
          <PhaseSection key={phase.phase} phase={phase} />
        ))}

        {r.synthesis && (
          <div>
            <div style={{
              fontSize: 9, fontWeight: 600, letterSpacing: '0.06em',
              textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 3,
            }}>
              Synthesis
            </div>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              {r.synthesis}
            </p>
          </div>
        )}
      </div>
    </InlineDisclosure>
  )
}

function PhaseSection({ phase }: { phase: CoordinatePhaseDetail }) {
  return (
    <div>
      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 4 }}>
        Phase {phase.phase}{phase.execution && <span style={{ fontWeight: 400, textTransform: 'none' }}> · {phase.execution}</span>}
      </div>
      <div className="flex flex-col gap-1">
        {(phase.results ?? []).map(agent => (
          <AgentRow key={agent.agent_name} agent={agent} />
        ))}
      </div>
    </div>
  )
}

function AgentRow({ agent }: { agent: CoordinatePhaseAgentResult }) {
  const isOk = agent.status === 'completed'
  return (
    <div className="flex items-start gap-1.5">
      {isOk
        ? <CheckCircle2 size={10} style={{ color: 'var(--success)', flexShrink: 0, marginTop: 2 }} />
        : <XCircle     size={10} style={{ color: 'var(--danger)',  flexShrink: 0, marginTop: 2 }} />}
      <div className="flex flex-col gap-0.5" style={{ minWidth: 0 }}>
        <span style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>{agent.agent_name}</span>
        {agent.output && (
          <span style={{ fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.5, maxHeight: 54, overflow: 'hidden', display: 'block' }}>
            {agent.output}
          </span>
        )}
        {agent.error && (
          <span style={{ fontSize: 11, color: 'var(--danger)', lineHeight: 1.5 }}>{agent.error}</span>
        )}
      </div>
    </div>
  )
}
