import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  BarChart2, RefreshCw, TrendingUp, Zap, DollarSign, Activity, Loader2, XCircle,
} from 'lucide-react'
import { metricsApi, type UsageRangeParams } from '@/api/metrics'
import { providersApi } from '@/api/tools'
import type { UsageSummary, ProviderCostRow, ProviderListItem } from '@/types/api'

// ─── Date range helpers ─────────────────────────────────────────────────────
type RangeKey = '7d' | '30d' | '90d' | 'all' | 'custom'

const RANGE_OPTIONS: RangeKey[] = ['7d', '30d', '90d', 'all', 'custom']

const RANGE_WINDOW: Record<RangeKey, string> = {
  '7d':     '7d window',
  '30d':    '30d window',
  '90d':    '90d window',
  'all':    'All time',
  'custom': 'Custom range',
}


function toDateStr(offset = 0): string {
  return new Date(Date.now() - offset * 86_400_000).toISOString().split('T')[0]
}

/** Params for GET /metrics/usage — ISO datetime strings */
function buildMetricsParams(range: RangeKey, start: string, end: string): UsageRangeParams {
  if (range === 'all') return {}
  if (range === 'custom') {
    const p: UsageRangeParams = {}
    if (start) p.start = new Date(start).toISOString()
    if (end)   p.end   = new Date(end + 'T23:59:59.999Z').toISOString()
    return p
  }
  return { start: new Date(Date.now() - parseInt(range, 10) * 86_400_000).toISOString() }
}

/** Params for GET /providers/costs — date strings (YYYY-MM-DD) */
function buildCostParams(range: RangeKey, start: string, end: string, provider: string) {
  const p: { provider?: string; from_date?: string; to_date?: string } = {}
  if (provider) p.provider = provider
  if (range === 'all') return p
  if (range === 'custom') {
    if (start) p.from_date = start
    if (end)   p.to_date   = end
    return p
  }
  p.from_date = new Date(Date.now() - parseInt(range, 10) * 86_400_000)
    .toISOString().split('T')[0]
  return p
}

// ─── Formatters ─────────────────────────────────────────────────────────────
function fmtTokens(n: number | null | undefined): string {
  const v = n ?? 0
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000)     return `${(v / 1_000).toFixed(1)}K`
  return String(v)
}

function fmtCost(n: number | null | undefined): string {
  const v = n ?? 0
  if (v === 0)   return '–'
  if (v < 0.001) return '< $0.001'
  return `$${v.toFixed(4)}`
}

// ─── Stat card ───────────────────────────────────────────────────────────────
function StatCard({ icon, label, value, color, title }: {
  icon: React.ReactNode; label: string; value: string; color: string; title?: string
}) {
  return (
    <div
      title={title}
      className="flex flex-col gap-2 rounded-2xl p-5"
      style={{
        background: 'var(--bg-surface1)',
        border: `1px solid color-mix(in srgb, ${color} 20%, var(--border-dim))`,
      }}
    >
      <div className="flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }}>
        <span style={{ color }}>{icon}</span>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
          {label}
        </span>
      </div>
      <div style={{ fontSize: 28, fontWeight: 700, color, lineHeight: 1, letterSpacing: '-0.02em' }}>
        {value}
      </div>
    </div>
  )
}

// ─── Provider / model cost row (richer than old provider row — includes model) ──
function CostRow({ row }: { row: ProviderCostRow }) {
  const pct = row.total_tokens > 0
    ? Math.round((row.total_completion_tokens / row.total_tokens) * 100)
    : 0

  return (
    <div
      className="rounded-xl p-4 flex flex-col gap-2.5"
      style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}
    >
      <div className="flex items-start justify-between gap-3">
        {/* Left: model + provider · runs */}
        <div className="min-w-0">
          <p className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
            {row.model}
          </p>
          <p className="text-xs capitalize mt-0.5" style={{ color: 'var(--text-muted)' }}>
            {row.provider} · {row.total_executions} run{row.total_executions !== 1 ? 's' : ''}
          </p>
        </div>
        {/* Right: cost + total tokens */}
        <div className="text-right shrink-0">
          <p
            className="text-sm font-bold font-mono"
            style={{ color: row.total_cost_usd != null ? 'var(--success)' : 'var(--text-muted)' }}
          >
            {fmtCost(row.total_cost_usd)}
          </p>
          <p className="text-xs font-mono mt-0.5" style={{ color: 'var(--text-muted)' }}>
            {fmtTokens(row.total_tokens)} tok
          </p>
        </div>
      </div>

      {/* Token split bar — prompt (teal) vs completion (purple) */}
      <div>
        <div
          className="h-1.5 rounded-full overflow-hidden"
          style={{ background: 'var(--bg-surface3)' }}
        >
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{
              width: `${100 - pct}%`,
              background: 'linear-gradient(90deg, var(--eva-primary), var(--neural))',
            }}
          />
        </div>
        <div className="flex justify-between mt-1" style={{ color: 'var(--text-muted)', fontSize: 10 }}>
          <span>in: {fmtTokens(row.total_prompt_tokens)}</span>
          <span>out: {fmtTokens(row.total_completion_tokens)}</span>
        </div>
      </div>
    </div>
  )
}

// ─── Empty state ─────────────────────────────────────────────────────────────
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-4">
      <div
        className="w-16 h-16 rounded-2xl flex items-center justify-center"
        style={{ background: 'rgba(0,201,255,0.08)', border: '1px solid rgba(0,201,255,0.15)' }}
      >
        <BarChart2 size={28} style={{ color: 'var(--eva-primary)', opacity: 0.5 }} />
      </div>
      <p style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-secondary)' }}>No usage data yet</p>
      <p className="text-center leading-relaxed" style={{ fontSize: 13, color: 'var(--text-muted)', maxWidth: 260 }}>
        Send a message to start tracking token usage and costs.
      </p>
    </div>
  )
}

// ─── Main panel ───────────────────────────────────────────────────────────────
export default function AnalyticsPanel() {
  const [range,        setRange]        = useState<RangeKey>('all')
  const [provider,     setProvider]     = useState('')
  const [customStart,  setCustomStart]  = useState(toDateStr(30))
  const [customEnd,    setCustomEnd]    = useState(toDateStr(0))
  const [appliedStart, setAppliedStart] = useState('')
  const [appliedEnd,   setAppliedEnd]   = useState('')

  const customReady = range !== 'custom' || (!!appliedStart && !!appliedEnd)

  const metricsParams = useMemo(
    () => buildMetricsParams(range, appliedStart, appliedEnd),
    [range, appliedStart, appliedEnd],
  )
  const costParams = useMemo(
    () => buildCostParams(range, appliedStart, appliedEnd, provider),
    [range, appliedStart, appliedEnd, provider],
  )

  // ── Query 0a: provider list for filter dropdown ──
  const { data: providerList } = useQuery<ProviderListItem[]>({
    queryKey: ['providers', 'list'],
    queryFn:  () => providersApi.list(),
    staleTime: 5 * 60_000,
  })

  // ── Query 1: summary stats + token split (GET /metrics/usage or /metrics/usage/summary) ──
  const {
    data: metrics,
    isLoading: metricsLoading,
    isFetching: metricsFetching,
    isError: metricsError,
    refetch: refetchMetrics,
  } = useQuery<UsageSummary>({
    queryKey: ['metrics', 'usage', range, appliedStart, appliedEnd],
    queryFn:  () => range === 'all' ? metricsApi.summary() : metricsApi.usage(metricsParams),
    staleTime: 60_000,
    retry: 1,
    enabled: customReady,
  })

  // ── Query 2: provider/model breakdown (GET /providers/costs) ──
  const {
    data: costs,
    isLoading: costsLoading,
    isFetching: costsFetching,
    isError: costsError,
    refetch: refetchCosts,
  } = useQuery({
    queryKey: ['provider-costs', range, appliedStart, appliedEnd, provider],
    queryFn:  () => providersApi.getCosts(costParams),
    staleTime: 60_000,
    retry: 1,
    enabled: customReady,
  })

  const handleRefresh = () => { refetchMetrics(); refetchCosts() }
  const isFetching    = metricsFetching || costsFetching

  const handleApplyCustom = () => {
    setAppliedStart(customStart)
    setAppliedEnd(customEnd)
  }

  const totalTokens = metrics
    ? (metrics.prompt_tokens ?? 0) + (metrics.completion_tokens ?? 0)
    : 0
  const avgPerExec  = metrics && metrics.execution_count > 0
    ? Math.round(totalTokens / metrics.execution_count)
    : 0

  const isEmpty = !metricsLoading && !metricsError && metrics && metrics.execution_count === 0

  // ── Subtitle line ──
  const subtitle = range === 'custom' && appliedStart && appliedEnd
    ? `${appliedStart} → ${appliedEnd}`
    : `${RANGE_WINDOW[range]} · ending today`

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: 'var(--bg-base)' }}>

      {/* ── Header ── */}
      <div
        className="flex items-center justify-between shrink-0 px-6 py-4 gap-4"
        style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-surface1)' }}
      >
        {/* Left: icon + title */}
        <div className="flex items-center gap-3 shrink-0">
          <div
            className="flex items-center justify-center rounded-xl shrink-0"
            style={{ width: 42, height: 42, background: 'rgba(0,201,255,0.1)', border: '1px solid rgba(0,201,255,0.2)' }}
          >
            <BarChart2 size={18} style={{ color: 'var(--eva-primary)' }} />
          </div>
          <div>
            <h2 style={{ fontSize: 19, fontWeight: 700, color: 'var(--text-primary)', margin: 0, lineHeight: 1.2 }}>
              Analytics
            </h2>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0, marginTop: 2 }}>
              {subtitle}
            </p>
          </div>
        </div>

        {/* Right: controls */}
        <div className="flex items-center gap-2 flex-wrap justify-end">

          {/* Range pills */}
          <div
            className="flex items-center gap-0.5 rounded-xl p-1"
            style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}
          >
            {RANGE_OPTIONS.map(r => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className="rounded-lg transition-all duration-150"
                style={{
                  padding: '2px 10px',
                  fontSize: 12,
                  fontWeight: 600,
                  lineHeight: '18px',
                  background: range === r ? 'var(--eva-primary)' : 'transparent',
                  color:      range === r ? 'var(--bg-base)' : 'var(--text-muted)',
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                {r === 'all' ? 'All' : r === 'custom' ? 'Custom' : r}
              </button>
            ))}
          </div>

          {/* Provider filter — populated from GET /providers/list */}
          <select
            value={provider}
            onChange={e => setProvider(e.target.value)}
            className="rounded-xl outline-none cursor-pointer"
            style={{
              padding: '6px 10px',
              fontSize: 12,
              fontWeight: 600,
              lineHeight: '18px',
              background: 'var(--bg-surface2)',
              border: `1px solid ${provider ? 'var(--border-bright)' : 'var(--border-dim)'}`,
              color: provider ? 'var(--text-primary)' : 'var(--text-muted)',
            }}
          >
            <option value="">All providers</option>
            {(providerList ?? []).map(p => (
              <option key={p.provider} value={p.provider}>{p.display_name}</option>
            ))}
          </select>


          {/* Refresh */}
          <button
            onClick={handleRefresh}
            disabled={isFetching}
            className="icon-btn"
            title="Refresh"
            style={{ width: 34, height: 34, color: isFetching ? 'var(--eva-primary)' : undefined }}
          >
            <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} />
          </button>

        </div>
      </div>

      {/* ── Custom date row ── */}
      {range === 'custom' && (
        <div
          className="flex items-center gap-3 shrink-0 px-6 py-3"
          style={{ borderBottom: '1px solid var(--border-dim)', background: 'var(--bg-surface1)' }}
        >
          <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>From</span>
          <input
            type="date"
            value={customStart}
            max={customEnd || toDateStr(0)}
            onChange={e => setCustomStart(e.target.value)}
            className="rounded-lg px-3 py-1.5 text-xs outline-none"
            style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-mid)', color: 'var(--text-primary)', colorScheme: 'dark' }}
          />
          <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>To</span>
          <input
            type="date"
            value={customEnd}
            min={customStart}
            max={toDateStr(0)}
            onChange={e => setCustomEnd(e.target.value)}
            className="rounded-lg px-3 py-1.5 text-xs outline-none"
            style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-mid)', color: 'var(--text-primary)', colorScheme: 'dark' }}
          />
          <button
            onClick={handleApplyCustom}
            disabled={!customStart || !customEnd}
            className="rounded-lg px-4 py-1.5 text-xs font-semibold"
            style={{
              background: !customStart || !customEnd ? 'var(--bg-surface3)' : 'var(--eva-primary)',
              color:      !customStart || !customEnd ? 'var(--text-muted)' : 'var(--bg-base)',
              cursor:     !customStart || !customEnd ? 'not-allowed' : 'pointer',
              border: 'none',
            }}
          >
            Apply
          </button>
          {appliedStart && appliedEnd && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Showing {appliedStart} → {appliedEnd}
            </span>
          )}
        </div>
      )}

      {/* ── Body ── */}
      <div className="flex-1 overflow-y-auto" style={{ padding: '24px 28px' }}>

        {/* Full-page spinner on initial load */}
        {metricsLoading ? (
          <div className="flex items-center justify-center py-24">
            <RefreshCw size={22} className="animate-spin" style={{ color: 'var(--text-muted)' }} />
          </div>

        ) : metricsError ? (
          <div
            className="rounded-xl px-4 py-4 text-sm"
            style={{ background: 'rgba(255,77,109,0.08)', border: '1px solid rgba(255,77,109,0.2)', color: 'var(--danger)', maxWidth: 480 }}
          >
            Failed to load metrics. Ensure the backend is running.
          </div>

        ) : isEmpty ? (
          <EmptyState />

        ) : metrics ? (
          <div className="flex flex-col gap-8">

            {/* ── 1. Stat cards ── */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
              <StatCard icon={<Zap size={13} />}       label="Total Tokens" value={fmtTokens(totalTokens)}            color="var(--eva-primary)" />
              <StatCard
                icon={<DollarSign size={13} />} label="Total Cost" color="var(--amber)"
                // Section 103 - null is "unknown", not zero; partial means some executions had no
                // price (subscription / local / unpriced model), so the real total is higher.
                value={metrics.cost_is_partial && metrics.total_cost_usd != null ? `${fmtCost(metrics.total_cost_usd)}+` : fmtCost(metrics.total_cost_usd)}
                title={metrics.cost_is_partial ? 'At least this much: some executions in this range have no known price (subscription, local, or unpriced models).' : undefined}
              />
              <StatCard icon={<TrendingUp size={13} />} label="Executions"   value={String(metrics.execution_count)}   color="var(--neural)"      />
              <StatCard icon={<Activity size={13} />}   label="Avg / Exec"   value={avgPerExec > 0 ? fmtTokens(avgPerExec) : '–'} color="var(--success)" />
            </div>

            {/* ── 2. Token split bar ── */}
            {totalTokens > 0 && (
              <div className="flex flex-col gap-4">
                <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>Token breakdown</span>
                <div
                  className="rounded-2xl p-5 flex flex-col gap-4"
                  style={{ background: 'var(--bg-surface1)', border: '1px solid var(--border-dim)' }}
                >
                  <div className="flex rounded-full overflow-hidden" style={{ height: 8, gap: 2 }}>
                    <div
                      className="transition-all duration-700"
                      style={{
                        width: `${Math.round(((metrics.prompt_tokens ?? 0) / totalTokens) * 100)}%`,
                        background: 'var(--eva-primary)',
                        opacity: 0.75,
                        borderRadius: 999,
                      }}
                    />
                    <div style={{ flex: 1, background: 'var(--neural)', opacity: 0.75, borderRadius: 999 }} />
                  </div>
                  <div className="flex items-center gap-8">
                    <div className="flex items-center gap-2">
                      <div className="rounded-sm" style={{ width: 10, height: 10, background: 'var(--eva-primary)', opacity: 0.75 }} />
                      <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Prompt</span>
                      <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--eva-primary)', marginLeft: 4 }}>
                        {fmtTokens(metrics.prompt_tokens ?? 0)}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="rounded-sm" style={{ width: 10, height: 10, background: 'var(--neural)', opacity: 0.75 }} />
                      <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Completion</span>
                      <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--neural)', marginLeft: 4 }}>
                        {fmtTokens(metrics.completion_tokens ?? 0)}
                      </span>
                    </div>
                    {/* Section 103 - cached is a SUBSET of prompt (discounted), not a third slice */}
                    {(metrics.cached_tokens ?? 0) > 0 && (
                      <div
                        className="flex items-center gap-2"
                        title="Part of the prompt tokens served from the provider's prompt cache (billed at a discount)"
                      >
                        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Cached</span>
                        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginLeft: 4 }}>
                          {fmtTokens(metrics.cached_tokens ?? 0)}
                        </span>
                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                          of prompt{(metrics.prompt_tokens ?? 0) > 0 ? ` (${Math.round(((metrics.cached_tokens ?? 0) / (metrics.prompt_tokens ?? 1)) * 100)}%)` : ''}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* ── 3. Provider / model breakdown (from /providers/costs) ── */}
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
                  By provider · model
                </span>
                {costs && (
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {costs.rows.length} {costs.rows.length === 1 ? 'entry' : 'entries'}
                    {costs.grand_total_cost_usd != null && (
                      <> · <span style={{ color: 'var(--success)', fontWeight: 600 }}>${costs.grand_total_cost_usd.toFixed(4)} total</span></>
                    )}
                  </span>
                )}
              </div>

              {costsLoading && (
                <div className="flex items-center gap-2 py-4 text-sm" style={{ color: 'var(--text-muted)' }}>
                  <Loader2 size={14} className="animate-spin" /> Loading breakdown…
                </div>
              )}

              {costsError && (
                <div className="flex items-center gap-2 text-sm py-2" style={{ color: 'var(--danger)' }}>
                  <XCircle size={14} /> Failed to load provider breakdown.
                </div>
              )}

              {costs && costs.rows.length === 0 && !costsLoading && (
                <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                  No breakdown data for the selected filters.
                </p>
              )}

              {costs && costs.rows.length > 0 && (
                <div className="flex flex-col gap-2">
                  {costs.rows.map((row, i) => (
                    <CostRow key={`${row.provider}-${row.model}-${i}`} row={row} />
                  ))}
                </div>
              )}
            </div>

            {/* Disclaimer */}
            <p style={{ fontSize: 11, color: 'var(--text-muted)', opacity: 0.6, lineHeight: 1.6 }}>
              Cost estimates are informational only. Actual billing depends on your provider contracts.
            </p>

          </div>
        ) : null}
      </div>
    </div>
  )
}
