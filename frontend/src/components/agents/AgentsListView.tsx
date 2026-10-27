import { useState } from 'react'
import { Loader2, ChevronDown, ChevronRight, Bot, Plus, RefreshCw, Search, X, AlertTriangle } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { agentsApi } from '@/api/agents'
import { projectsApi } from '@/api/projects'
import AgentListCard from './AgentListCard'

// ── Agents list (Level 1) ─────────────────────────────────────────────────────
export default function AgentsListView({
  onEdit, onCreate,
}: {
  onEdit:   (name: string) => void
  onCreate: () => void
}) {
  const [search,         setSearch]         = useState('')
  const [defaultsOpen,   setDefaultsOpen]   = useState(true)
  const [customOpen,     setCustomOpen]      = useState(true)
  const [projectFilter,  setProjectFilter]  = useState('')
  const qc = useQueryClient()

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn:  projectsApi.list,
    staleTime: 60_000,
  })

  const { data: agents = [], isLoading, isError } = useQuery({
    queryKey: ['agents', projectFilter],
    queryFn:  () => agentsApi.list(projectFilter || undefined),
  })

  const { mutate: syncDefaults, isPending: syncing } = useMutation({
    mutationFn: () => agentsApi.seedDefaults(true),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })

  const { mutate: doReset } = useMutation({
    mutationFn: (name: string) => agentsApi.resetAgent(name),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })

  const { mutate: doClone } = useMutation({
    mutationFn: ({ name, newName }: { name: string; newName: string }) => agentsApi.cloneAgent(name, newName),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })

  const { mutate: doDelete } = useMutation({
    mutationFn: (name: string) => agentsApi.remove(name),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })

  const standalone = agents.filter(a => !a.project_id)

  const filtered = search.trim()
    ? standalone.filter(a =>
        a.name.toLowerCase().includes(search.toLowerCase()) ||
        (a.description ?? '').toLowerCase().includes(search.toLowerCase())
      )
    : standalone

  const defaultAgents = filtered.filter(a => a.is_default)
  const customAgents  = filtered.filter(a => !a.is_default)

  return (
    <div className="detail">
      <div className="detail-header">
        <div className="dh-icon" style={{ background: 'rgba(124,108,243,0.12)', borderColor: 'rgba(124,108,243,0.25)', color: 'var(--neural)' }}>
          <Bot size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">Subagents</h2>
          <p className="dh-sub">Specialised agents you can spawn · {agents.length} configured</p>
        </div>
        <div className="detail-actions">
          {/* Project filter */}
          <select
            value={projectFilter}
            onChange={e => setProjectFilter(e.target.value)}
            className="text-xs rounded-lg px-2 py-1 outline-none"
            style={{
              background: projectFilter ? 'rgba(0,201,255,0.08)' : 'var(--bg-surface2)',
              border:     `1px solid ${projectFilter ? 'rgba(0,201,255,0.3)' : 'var(--border-dim)'}`,
              color:      projectFilter ? 'var(--eva-primary)' : 'var(--text-muted)',
              fontSize:   11,
            }}
            title="Filter agents by project"
          >
            <option value="">All Projects</option>
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <button
            className="btn ghost"
            onClick={() => syncDefaults()}
            disabled={syncing}
            title="Refresh built-in agents from latest EVA definitions"
          >
            <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} /> Sync defaults
          </button>
          <button className="btn primary" onClick={onCreate}>
            <Plus size={12} /> New
          </button>
        </div>
      </div>

      <div className="detail-body" style={{ maxWidth: 'none' }}>
        <div className="search-field" style={{ marginBottom: 16 }}>
          <Search size={13} />
          <input
            placeholder="Search subagents…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
              <X size={11} />
            </button>
          )}
        </div>

        {isLoading && (
          <div className="flex items-center justify-center gap-2 py-10" style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            <Loader2 size={16} className="animate-spin" /> Loading…
          </div>
        )}
        {isError && (
          <div className="flex items-center gap-2 py-6" style={{ color: 'var(--danger)', fontSize: 13 }}>
            <AlertTriangle size={14} /> Failed to load agents
          </div>
        )}
        {!isLoading && !isError && filtered.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-14 text-center">
            <Bot size={28} style={{ color: 'var(--text-muted)' }} />
            <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              {search ? `No agents match "${search}"` : 'No subagents configured yet.'}
            </p>
            {!search && (
              <button className="btn ghost" onClick={onCreate}>
                <Plus size={12} /> Create first subagent
              </button>
            )}
          </div>
        )}

        {/* ── EVA Built-in Agents ── */}
        {!isLoading && defaultAgents.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <button
              className="flex items-center gap-2 w-full mb-2"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px 0' }}
              onClick={() => setDefaultsOpen(o => !o)}
            >
              {defaultsOpen ? <ChevronDown size={10} style={{ color: 'var(--eva-primary)' }} /> : <ChevronRight size={10} style={{ color: 'var(--eva-primary)' }} />}
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
                EVA Built-in Agents
              </span>
              <span style={{ fontSize: 10, color: 'var(--text-muted)', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 99, padding: '0 6px' }}>
                {defaultAgents.length}
              </span>
            </button>
            {defaultsOpen && defaultAgents.map(agent => (
              <AgentListCard
                key={agent.name}
                agent={agent}
                onEdit={() => onEdit(agent.name)}
                onClone={newName => doClone({ name: agent.name, newName })}
                onReset={() => doReset(agent.name)}
                onDelete={() => doDelete(agent.name)}
              />
            ))}
          </div>
        )}

        {/* ── My Custom Agents ── */}
        {!isLoading && customAgents.length > 0 && (
          <div>
            <button
              className="flex items-center gap-2 w-full mb-2"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px 0' }}
              onClick={() => setCustomOpen(o => !o)}
            >
              {customOpen ? <ChevronDown size={10} style={{ color: 'var(--neural)' }} /> : <ChevronRight size={10} style={{ color: 'var(--neural)' }} />}
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
                My Custom Agents
              </span>
              <span style={{ fontSize: 10, color: 'var(--text-muted)', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 99, padding: '0 6px' }}>
                {customAgents.length}
              </span>
            </button>
            {customOpen && customAgents.map(agent => (
              <AgentListCard
                key={agent.name}
                agent={agent}
                onEdit={() => onEdit(agent.name)}
                onClone={newName => doClone({ name: agent.name, newName })}
                onReset={() => doReset(agent.name)}
                onDelete={() => doDelete(agent.name)}
                projectBadge={projectFilter ? (projects.find(p => p.id === agent.project_id)?.name ?? undefined) : undefined}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
