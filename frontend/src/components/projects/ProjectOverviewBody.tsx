import { Loader2, FolderOpen, Copy, Clock, MessageSquare, Bot, CheckSquare } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { projectsApi } from '@/api/projects'
import type { ProjectMetricsResponse } from '@/types/api'
import { copyPath, relativeDate, fmtCost, fmtDuration, fmtTokensK } from './projectFormat'
import { DonutChart, ConcentricDonut, type DonutSegment, type TipSection } from './DonutChart'

interface ProjectOverviewBodyProps {
  projectId:     string
  synopsis:      string | null
  workspacePath: string | null
  onOpenConv:    (id: string, title: string | null) => void
}

const PROVIDER_COLORS = [
  'var(--eva-primary)',
  'var(--neural)',
  'var(--amber)',
  'var(--success)',
  'var(--danger)',
]

export function ProjectOverviewBody({ projectId, synopsis, workspacePath, onOpenConv }: ProjectOverviewBodyProps) {
  // Single metrics call covers counts, tokens, cost, time, daemons, tasks
  const { data: metrics, isLoading: loadingM } = useQuery<ProjectMetricsResponse>({
    queryKey: ['project-metrics', projectId],
    queryFn:  () => projectsApi.getMetrics(projectId),
    staleTime: 60_000,
  })

  // Conversations still needed for the list (titles + dates)
  const { data: convs = [], isLoading: loadingC } = useQuery({
    queryKey: ['project-conversations', projectId],
    queryFn:  () => projectsApi.listConversations(projectId),
    staleTime: 30_000,
  })

  const m = metrics   // shorthand
  const loading = loadingM

  // Daemon health colour derived from metrics breakdown
  const daemonHealthColor =
    !m || m.daemons.count === 0      ? null
    : m.daemons.failed > 0           ? 'var(--danger)'
    : m.daemons.degraded > 0         ? 'var(--amber)'
    : 'var(--success)'
  const daemonHealthLabel =
    !m || m.daemons.count === 0      ? 'Daemons'
    : m.daemons.failed > 0           ? `${m.daemons.failed} failed`
    : m.daemons.degraded > 0         ? `${m.daemons.degraded} degraded`
    : 'All healthy'
  const daemonIconBg =
    daemonHealthColor === 'var(--success)' ? 'var(--success-dim)'
    : daemonHealthColor === 'var(--danger)'  ? 'var(--danger-dim)'
    : daemonHealthColor === 'var(--amber)'   ? 'var(--amber-dim)'
    : 'rgba(255,255,255,0.05)'

  // Success rate label
  const successRate = m?.executions.success_rate
  const successRateLabel = successRate !== null && successRate !== undefined
    ? `${Math.round(successRate * 100)}% success`
    : m?.executions.total === 0 ? 'no runs yet' : '—'

  // Provider breakdown — used for inner rings of Tokens + Executions donuts
  const providers = m?.provider_breakdown ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* SYNOPSIS + WORKSPACE */}
      {(synopsis || workspacePath) && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {synopsis && (
            <div className="po-hero">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="po-synopsis-label">Synopsis</div>
                <div className="po-synopsis">{synopsis}</div>
              </div>
            </div>
          )}
          {workspacePath && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div className="po-synopsis-label">Workspace</div>
              <button
                className="po-path"
                type="button"
                title="Click to copy"
                aria-label={`Copy workspace path ${workspacePath}`}
                style={{ alignSelf: 'flex-start' }}
                onClick={() => copyPath(workspacePath)}
              >
                <FolderOpen size={11} />
                {workspacePath}
                <Copy size={10} className="po-copy" />
              </button>
            </div>
          )}
        </div>
      )}

      {/* KPI GRID */}
      <div className="po-kpi-grid">

        {/* ── Cost ── donut = per-provider cost distribution */}
        {(() => {
          const costSegs: DonutSegment[] = (m?.provider_breakdown ?? [])
            .slice(0, 5)
            .map((p, i) => ({ value: p.cost_usd ?? 0, color: PROVIDER_COLORS[i % PROVIDER_COLORS.length] }))
          const hasCost = (m?.cost_usd ?? null) !== null
          const costTip: TipSection = {
            heading: 'by provider',
            lines: providers.slice(0, 5).map((p, i) => ({
              color: PROVIDER_COLORS[i % PROVIDER_COLORS.length],
              label: `${p.provider} / ${p.model}`,
              value: p.cost_usd !== null ? `$${p.cost_usd.toFixed(2)}` : '—',
            })),
          }
          return (
            <div className="po-kpi" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="kpi-label">Total cost</div>
                <div className="kpi-value">
                  {loading ? '…' : fmtCost(m?.cost_usd ?? null, m?.cost_is_partial ?? false)}
                </div>
                <div className="kpi-sub">
                  {m?.cost_is_partial ? '* excl. local models' : 'all time'}
                </div>
              </div>
              <DonutChart
                segments={hasCost ? costSegs : [{ value: 1, color: 'var(--bg-surface3)' }]}
                tooltip={hasCost && costTip.lines.length > 0 ? [costTip] : undefined}
              />
            </div>
          )
        })()}

        {/* ── Tokens ── outer = prompt/completion/reasoning · inner = per-model */}
        {(() => {
          const tok = m?.tokens
          // Section 103 - reasoning is a SUBSET of completion, so drawing it as its own slice next
          // to the whole completion would count it twice. Split completion into answer + reasoning.
          const reasoning = Math.min(tok?.reasoning ?? 0, tok?.completion ?? 0)
          const outer: DonutSegment[] = [
            { value: tok?.prompt ?? 0,                          color: 'var(--eva-primary)' },
            { value: (tok?.completion ?? 0) - reasoning,        color: 'var(--neural)'      },
            { value: reasoning,                                 color: 'var(--amber)'       },
          ]
          const inner: DonutSegment[] = providers.slice(0, 5).map((p, i) => ({
            value: p.total_tokens, color: PROVIDER_COLORS[i % PROVIDER_COLORS.length],
          }))
          const outerTip: TipSection = {
            heading: 'type',
            lines: [
              { color: 'var(--eva-primary)', label: 'prompt',     value: fmtTokensK(tok?.prompt     ?? 0) },
              { color: 'var(--neural)',      label: reasoning > 0 ? 'completion (answer)' : 'completion', value: fmtTokensK((tok?.completion ?? 0) - reasoning) },
              ...(reasoning > 0 ? [{ color: 'var(--amber)', label: 'reasoning (part of completion)', value: fmtTokensK(reasoning) }] : []),
            ],
          }
          const innerTip: TipSection = {
            heading: 'by model',
            lines: providers.slice(0, 5).map((p, i) => ({
              color: PROVIDER_COLORS[i % PROVIDER_COLORS.length],
              label: p.model,
              value: fmtTokensK(p.total_tokens),
            })),
          }
          return (
            <div className="po-kpi" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="kpi-label">Tokens</div>
                <div className="kpi-value">
                  {loading ? '…' : fmtTokensK(tok?.total ?? 0)}
                </div>
                <div className="kpi-sub">
                  {tok && tok.total > 0
                    ? `${fmtTokensK(tok.prompt)} in · ${fmtTokensK(tok.completion)} out`
                    : 'all executions'}
                </div>
              </div>
              <ConcentricDonut outer={outer} inner={inner} outerTip={outerTip} innerTip={innerTip} />
            </div>
          )
        })()}

        {/* ── Time spent ── no breakdown, keep icon */}
        <div className="po-kpi">
          <div className="kpi-icon" style={{ background: 'var(--neural-dim)', color: 'var(--neural)' }}>
            <Clock size={14} />
          </div>
          <div className="kpi-label">Time spent</div>
          <div className="kpi-value">
            {loading ? '…' : fmtDuration(m?.duration_total_ms ?? null)}
          </div>
          <div className="kpi-sub">across all runs</div>
        </div>

        {/* ── Executions ── outer = successful/failed/cancelled · inner = per-model */}
        {(() => {
          const ex    = m?.executions
          const total = ex?.total || 1
          const pct   = (n: number) => `${Math.round((n / total) * 100)}%`
          const outer: DonutSegment[] = [
            { value: ex?.successful ?? 0, color: 'var(--success)'         },
            { value: ex?.failed     ?? 0, color: 'var(--danger)'          },
            { value: ex?.cancelled  ?? 0, color: 'rgba(255,255,255,0.18)' },
          ]
          const inner: DonutSegment[] = providers.slice(0, 5).map((p, i) => ({
            value: p.executions, color: PROVIDER_COLORS[i % PROVIDER_COLORS.length],
          }))
          const outerTip: TipSection = {
            heading: 'status',
            lines: [
              { color: 'var(--success)',          label: 'successful', value: pct(ex?.successful ?? 0) },
              { color: 'var(--danger)',            label: 'failed',     value: pct(ex?.failed     ?? 0) },
              { color: 'rgba(255,255,255,0.35)',   label: 'cancelled',  value: pct(ex?.cancelled  ?? 0) },
            ],
          }
          const innerTip: TipSection = {
            heading: 'by model',
            lines: providers.slice(0, 5).map((p, i) => ({
              color: PROVIDER_COLORS[i % PROVIDER_COLORS.length],
              label: p.model,
              value: String(p.executions),
            })),
          }
          return (
            <div className="po-kpi" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="kpi-label">Executions</div>
                <div className="kpi-value">
                  {loading ? '…' : (ex?.total ?? 0)}
                </div>
                <div className="kpi-sub">{loading ? '…' : successRateLabel}</div>
              </div>
              <ConcentricDonut outer={outer} inner={inner} outerTip={outerTip} innerTip={innerTip} />
            </div>
          )
        })()}

      </div>

      {/* MINI-STAT ROW */}
      <div className="po-mini-grid">
        <div className="po-mini" style={{ cursor: 'default' }}>
          <div className="mini-icon" style={{ background: 'var(--eva-primary-dim)', color: 'var(--eva-primary)' }}>
            <MessageSquare size={14} />
          </div>
          <div className="mini-body">
            <div className="mini-value">{loading ? '…' : (m?.conversation_count ?? convs.length)}</div>
            <div className="mini-label">Conversations</div>
          </div>
        </div>

        <div className="po-mini" style={{ cursor: 'default' }}>
          <div className="mini-icon" style={{ background: 'var(--neural-dim)', color: 'var(--neural)' }}>
            <Bot size={14} />
          </div>
          <div className="mini-body">
            <div className="mini-value">{loading ? '…' : (m?.agent_count ?? 0)}</div>
            <div className="mini-label">Subagents</div>
          </div>
        </div>

        <div className="po-mini" style={{ cursor: 'default' }}>
          <div className="mini-icon" style={{ background: daemonIconBg, color: daemonHealthColor ?? 'var(--text-muted)' }}>
            <Clock size={14} />
          </div>
          <div className="mini-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
            <div>
              <div className="mini-value" style={{ color: daemonHealthColor ?? undefined }}>
                {loading ? '…' : (m?.daemons.count ?? 0)}
              </div>
              <div className="mini-label">Daemons</div>
            </div>
            {daemonHealthColor && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                <span className="mini-health-dot" style={{ background: daemonHealthColor }} />
                <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: daemonHealthColor }}>
                  {daemonHealthLabel}
                </span>
              </div>
            )}
          </div>
        </div>

        <div className="po-mini" style={{ cursor: 'default' }}>
          <div className="mini-icon" style={{
            background: (m?.tasks.open ?? 0) > 0 ? 'rgba(245,158,11,0.12)' : 'rgba(255,255,255,0.05)',
            color:      (m?.tasks.open ?? 0) > 0 ? 'var(--amber)' : 'var(--text-muted)',
          }}>
            <CheckSquare size={14} />
          </div>
          <div className="mini-body">
            <div className="mini-value" style={{ color: (m?.tasks.open ?? 0) > 0 ? 'var(--amber)' : undefined }}>
              {loading ? '…' : (m?.tasks.total === 0 ? '—' : (m?.tasks.open ?? '—'))}
            </div>
            {m && m.tasks.total > 0 ? (
              <div className="mini-health" style={{ color: 'var(--text-muted)' }}>
                {m.tasks.done} done
              </div>
            ) : (
              <div className="mini-label">Open tasks</div>
            )}
          </div>
        </div>
      </div>

      {/* RECENT CONVERSATIONS */}
      <div>
        <div className="section-heading">
          Recent conversations
          <span className="count">{loadingC ? '' : convs.length}</span>
        </div>
        {loadingC ? (
          <div className="flex items-center gap-2" style={{ color: 'var(--text-muted)', fontSize: 12 }}>
            <Loader2 size={12} className="animate-spin" /> Loading…
          </div>
        ) : convs.length === 0 ? (
          <div className="po-empty">
            No conversations yet — start one and it will scope to this project.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {convs.slice(0, 8).map(c => (
              <button
                key={c.id}
                type="button"
                className="ws-conv"
                onClick={() => onOpenConv(c.id, c.title ?? null)}
              >
                <MessageSquare size={13} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
                  <div className="wc-title">{c.title || 'Untitled conversation'}</div>
                </div>
                <span className="wc-meta">{relativeDate(c.created_at)}</span>
              </button>
            ))}
            {convs.length > 8 && (
              <button type="button" className="po-see-all">
                See all {convs.length} conversations →
              </button>
            )}
          </div>
        )}
      </div>

    </div>
  )
}
