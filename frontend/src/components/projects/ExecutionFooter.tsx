import { useState } from 'react'
import { ChevronUp, ChevronDown } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import type { ExecutionMetrics, FallbackDetail, ToolCallMetric } from '@/types/api'
import { executionsApi } from '@/api/executions'
import { formatTokens } from '@/lib/utils'
import { fmtDuration } from './projectFormat'

// Module-level: declared inside ExecutionFooter it was a new component type on every render (react-hooks/static-components).
function SecLabel({ children }: { children: string }) {
  return (
    <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', marginBottom: 6 }}>
      {children}
    </div>
  )
}

// ── Execution footer (Section 76 — Gaps 8–11) ────────────────────────────────
// Shown below the message list once an execution completes. Fetches
// GET /executions/{id}/metrics and displays a collapsible summary bar.
export function ExecutionFooter({ executionId }: { executionId: string }) {
  const [open, setOpen] = useState(false)

  const { data: m } = useQuery<ExecutionMetrics>({
    queryKey: ['exec-metrics', executionId],
    queryFn:  () => executionsApi.getMetrics(executionId),
    staleTime: Infinity,
    retry: 1,
  })

  if (!m) return null

  const model      = m.model_used    || m.model
  const provider   = m.provider_used || m.provider
  const durStr     = fmtDuration(m.duration_ms ?? null)
  const costStr    = m.computed_cost_usd != null
    ? m.computed_cost_usd < 0.001 ? '<$0.001' : `$${m.computed_cost_usd.toFixed(3)}`
    : null

  // Section 103 - the part of the prompt served from the provider's cache (discounted)
  const cached     = m.cached_tokens ?? 0
  const cachedTip  = `${cached.toLocaleString()} of ${(m.prompt_tokens ?? 0).toLocaleString()} prompt tokens served from the provider's prompt cache (billed at a discount)`

  const fallbacks  = m.fallback_details  ?? []
  const toolRows   = m.tool_calls        ?? []
  const subCalls   = m.subagent_calls    ?? 0
  const subFails   = m.subagent_failures ?? 0

  const hasFallbacks = fallbacks.length > 0
  const hasSubagents = subCalls > 0
  const hasTools     = toolRows.length > 0
  const hasDetail    = hasFallbacks || hasSubagents || hasTools

  return (
    <div style={{ marginTop: 16, borderTop: '1px solid var(--border-dim)' }}>

      {/* ── Collapsed summary bar ── */}
      <div
        onClick={() => hasDetail && setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
          padding: '7px 2px',
          cursor: hasDetail ? 'pointer' : 'default',
        }}
      >
        {/* model/provider */}
        <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
          {provider}/{model}
        </span>
        <span style={{ color: 'var(--border-mid)' }}>·</span>

        {/* tokens in/out */}
        <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
          ↑{formatTokens(m.prompt_tokens ?? 0)} · ↓{formatTokens(m.completion_tokens ?? 0)}
        </span>

        {/* cached prompt tokens (Section 103) — only when the provider actually reported a cache hit */}
        {cached > 0 && <>
          <span style={{ color: 'var(--border-mid)' }}>·</span>
          <span title={cachedTip} style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            {formatTokens(cached)} cached
          </span>
        </>}

        {/* cost */}
        {costStr && <>
          <span style={{ color: 'var(--border-mid)' }}>·</span>
          <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{costStr}</span>
        </>}

        {/* duration — Gap 11: uses duration_ms (DB-persisted) */}
        <span style={{ color: 'var(--border-mid)' }}>·</span>
        <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{durStr}</span>

        {/* tool count */}
        {toolRows.length > 0 && <>
          <span style={{ color: 'var(--border-mid)' }}>·</span>
          <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            {toolRows.length} tool{toolRows.length !== 1 ? 's' : ''}
          </span>
        </>}

        {/* Gap 8 chip — fallbacks occurred */}
        {hasFallbacks && (
          <span style={{ fontSize: 9.5, fontWeight: 700, padding: '1px 6px', borderRadius: 999, background: 'rgba(245,158,11,0.12)', color: 'var(--amber)', border: '1px solid rgba(245,158,11,0.3)', whiteSpace: 'nowrap' }}>
            {fallbacks.length} fallback{fallbacks.length > 1 ? 's' : ''}
          </span>
        )}

        {/* Gap 10 chip — subagents used */}
        {hasSubagents && (
          <span style={{ fontSize: 9.5, fontWeight: 700, padding: '1px 6px', borderRadius: 999, background: 'rgba(124,108,243,0.12)', color: 'var(--neural)', border: '1px solid rgba(124,108,243,0.3)', whiteSpace: 'nowrap' }}>
            {subCalls} agent{subCalls > 1 ? 's' : ''}
          </span>
        )}

        {/* expand chevron */}
        {hasDetail && (
          <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', display: 'flex' }}>
            {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </span>
        )}
      </div>

      {/* ── Expanded detail panel ── */}
      {open && hasDetail && (
        <div style={{ borderTop: '1px solid var(--border-dim)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Gap 8 — Provider fallback chain */}
          {hasFallbacks && (
            <div>
              <SecLabel>Provider fallbacks</SecLabel>
              {fallbacks.map((f: FallbackDetail, i: number) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '3px 0', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 9.5, fontWeight: 700, padding: '1px 5px', borderRadius: 4, background: 'rgba(245,158,11,0.12)', color: 'var(--amber)', border: '1px solid rgba(245,158,11,0.25)', fontFamily: 'var(--font-mono)' }}>
                    {f.failure_reason}
                  </span>
                  <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                    {f.failed_provider}/{f.failed_model}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>→</span>
                  <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                    {f.fallback_provider}/{f.fallback_model}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Gap 10 — Subagent counts */}
          {hasSubagents && (
            <div>
              <SecLabel>Subagents</SecLabel>
              <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                {subCalls} called{subFails > 0
                  ? <span style={{ color: 'var(--danger)' }}> · {subFails} failed</span>
                  : ' · all succeeded'}
              </span>
            </div>
          )}

          {/* Gap 9 — Tool breakdown with denial reasons */}
          {hasTools && (
            <div>
              <SecLabel>{`Tools (${toolRows.length})`}</SecLabel>
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                {toolRows.map((t: ToolCallMetric) => (
                  <div key={t.tool_name} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '3px 0', borderBottom: '1px solid var(--border-dim)' }}>
                    <span style={{ fontSize: 10.5, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.tool_name}
                    </span>
                    <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', flexShrink: 0 }}>
                      {t.call_count}×
                    </span>
                    {t.avg_latency_ms != null && (
                      <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', flexShrink: 0 }}>
                        {Math.round(t.avg_latency_ms)}ms
                      </span>
                    )}
                    {t.last_denial_reason ? (
                      <span title={t.last_denial_reason} style={{ fontSize: 9.5, fontFamily: 'var(--font-mono)', color: 'var(--amber)', flexShrink: 0, cursor: 'help' }}>
                        ⚠ denied
                      </span>
                    ) : t.failure_count > 0 ? (
                      <span style={{ fontSize: 9.5, fontFamily: 'var(--font-mono)', color: 'var(--danger)', flexShrink: 0 }}>
                        {t.failure_count} failed
                      </span>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          )}

        </div>
      )}
    </div>
  )
}
