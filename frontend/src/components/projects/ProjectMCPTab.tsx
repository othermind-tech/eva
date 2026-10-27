import { useState } from 'react'
import { Plug, Plus, Loader2, Cpu, RefreshCw, ToggleRight, ToggleLeft, Edit3, Trash2, ChevronRight } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { mcpApi } from '@/api/mcp'
import type { MCPConfig, MCPHealth } from '@/types/api'
import { useChatStore } from '@/store/chatStore'
import { ServerDetail } from '@/components/panels/MCPPanel'

export default function ProjectMCPTab({ projectId }: { projectId: string }) {
  const qc           = useQueryClient()
  const setViewMCP   = useChatStore(s => s.setViewMCP)
  const setViewMCPEdit = useChatStore(s => s.setViewMCPEdit)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data: servers = [], isLoading } = useQuery<MCPConfig[]>({
    queryKey: ['project-mcp', projectId],
    queryFn:  () => mcpApi.list(projectId),
    staleTime: 30_000,
  })

  const { data: healthList = [] } = useQuery<MCPHealth[]>({
    queryKey: ['mcp-health'],
    queryFn:  mcpApi.healthAll,
    refetchInterval: 30_000,
    retry: false,
  })
  const healthMap = Object.fromEntries(healthList.map(h => [h.config_id, h]))

  const { mutate: doToggle, variables: toggleVars, isPending: toggling } = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      mcpApi.update(id, { enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project-mcp', projectId] })
      qc.invalidateQueries({ queryKey: ['mcp-health'] })
    },
  })

  const { mutate: doSync, variables: syncVars, isPending: syncing } = useMutation({
    mutationFn: (id: string) => mcpApi.sync(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-health'] }),
  })

  const { mutate: doDelete, variables: deleteVars, isPending: deleting } = useMutation({
    mutationFn: (id: string) => mcpApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project-mcp', projectId] })
      qc.invalidateQueries({ queryKey: ['mcp-health'] })
    },
  })

  if (isLoading) return (
    <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={12} className="animate-spin" /> Loading…
    </div>
  )

  if (servers.length === 0) return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <Plug size={24} style={{ color: 'var(--text-muted)' }} />
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No MCP servers scoped to this project yet.</p>
      <button className="btn ghost" onClick={setViewMCP}>
        <Plus size={12} /> Add MCP server
      </button>
    </div>
  )

  const TRANSPORT_COLOR: Record<string, string> = {
    stdio: 'var(--neural)',
    sse:   'var(--amber)',
    http:  'var(--eva-primary)',
  }

  return (
    <div>
      <div className="flex justify-end mb-3">
        <button className="btn primary" onClick={setViewMCP}>
          <Plus size={12} /> Add Server
        </button>
      </div>

      {servers.map(s => {
        const health  = healthMap[s.id]
        const isPaused = s.project_paused
        const isHealthy   = health?.status === 'healthy'
        const isUnhealthy = health?.status === 'unhealthy'
        const healthColor = isHealthy ? 'var(--success)' : isUnhealthy ? 'var(--danger)' : 'var(--text-dim)'
        const healthLabel = isHealthy
          ? `${health.latency_ms != null ? `${health.latency_ms}ms` : 'Healthy'}${health.tool_count != null ? ` · ${health.tool_count} tools` : ''}`
          : isUnhealthy  ? (health.error ?? 'Error')
          : health?.status === 'disabled' ? 'Disabled'
          : 'Unknown'

        return (
          <div
            key={s.id}
            className="kn-card"
            style={{ opacity: s.enabled && !isPaused ? 1 : 0.65 }}
          >
            <div className="kn-head">
              {/* Icon */}
              <div className="row-icon" style={{
                width: 32, height: 32, borderRadius: 9, flexShrink: 0,
                background: isPaused ? 'rgba(245,158,11,0.1)' : 'rgba(124,108,243,0.12)',
                color: isPaused ? 'var(--amber)' : 'var(--neural)',
              }}>
                <Cpu size={14} />
              </div>

              {/* Name + transport + health */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                  <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>
                    {s.name}
                  </span>
                  <span style={{
                    fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 999,
                    color: TRANSPORT_COLOR[s.transport] ?? 'var(--text-muted)',
                    background: 'rgba(255,255,255,0.05)',
                    border: `1px solid ${TRANSPORT_COLOR[s.transport] ?? 'var(--border-dim)'}44`,
                  }}>
                    {s.transport.toUpperCase()}
                  </span>
                  {isPaused && (
                    <span style={{
                      fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 4,
                      background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)',
                      color: 'var(--amber)',
                    }}>
                      ⊘ PAUSED
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11.5, color: healthColor }}>
                  <span style={{ width: 6, height: 6, borderRadius: 999, background: healthColor, flexShrink: 0, display: 'inline-block' }} />
                  {healthLabel}
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center" style={{ gap: 1 }}>
                {/* Sync */}
                <button
                  className="icon-btn"
                  style={{
                    width: 26, height: 26, padding: 0,
                    color: syncing && syncVars === s.id ? 'var(--eva-primary)' : 'var(--text-muted)',
                    opacity: (!s.enabled || isPaused) ? 0.35 : 1,
                  }}
                  title={!s.enabled || isPaused ? 'Cannot sync — server inactive' : 'Sync tools'}
                  disabled={!s.enabled || isPaused || (syncing && syncVars === s.id)}
                  onClick={() => doSync(s.id)}
                >
                  <RefreshCw size={12} className={syncing && syncVars === s.id ? 'animate-spin' : ''} />
                </button>

                {/* Enable toggle */}
                {toggling && toggleVars?.id === s.id ? (
                  <span style={{ width: 26, height: 26, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Loader2 size={12} className="animate-spin" style={{ color: 'var(--text-dim)' }} />
                  </span>
                ) : (
                  <button
                    className="icon-btn"
                    style={{ width: 26, height: 26, padding: 0, color: s.enabled ? 'var(--success)' : 'var(--text-muted)', opacity: isPaused ? 0.4 : 1 }}
                    title={isPaused ? 'Paused by project' : s.enabled ? 'Disable' : 'Enable'}
                    disabled={isPaused}
                    onClick={() => doToggle({ id: s.id, enabled: !s.enabled })}
                  >
                    {s.enabled
                      ? <ToggleRight size={17} />
                      : <ToggleLeft  size={17} />}
                  </button>
                )}

                {/* Edit — opens MCP panel with this server's form pre-loaded */}
                <button
                  className="icon-btn"
                  style={{ width: 26, height: 26, padding: 0 }}
                  title="Edit server"
                  onClick={() => setViewMCPEdit(s.id, projectId)}
                >
                  <Edit3 size={12} />
                </button>

                {/* Delete */}
                <button
                  className="icon-btn"
                  style={{ width: 26, height: 26, padding: 0 }}
                  title="Delete server"
                  disabled={deleting && deleteVars === s.id}
                  onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
                  onMouseLeave={e => (e.currentTarget.style.color = '')}
                  onClick={() => doDelete(s.id)}
                >
                  {deleting && deleteVars === s.id
                    ? <Loader2 size={12} className="animate-spin" />
                    : <Trash2 size={12} />}
                </button>

                {/* Expand chevron */}
                <button
                  className="icon-btn"
                  style={{ width: 26, height: 26, padding: 0, color: expandedId === s.id ? 'var(--eva-primary)' : 'var(--text-muted)' }}
                  title={expandedId === s.id ? 'Collapse' : 'Browse resources & prompts'}
                  onClick={() => setExpandedId(expandedId === s.id ? null : s.id)}
                >
                  <ChevronRight size={14} style={{ transform: expandedId === s.id ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }} />
                </button>
              </div>
            </div>

            {expandedId === s.id && <ServerDetail server={s} />}
          </div>
        )
      })}
    </div>
  )
}
