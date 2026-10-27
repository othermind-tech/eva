import { useState } from 'react'
import { Bot, Plus, Loader2 } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { agentsApi } from '@/api/agents'
import { useChatStore } from '@/store/chatStore'
import AgentListCard from '@/components/agents/AgentListCard'

export default function ProjectAgentsTab({ projectId }: { projectId: string }) {
  const qc                 = useQueryClient()
  const setViewAgents      = useChatStore(s => s.setViewAgents)
  const setViewAgentDetail = useChatStore(s => s.setViewAgentDetail)
  const [filterKind, setFilterKind] = useState<'all' | 'default' | 'custom'>('all')

  const { data: agents = [], isLoading } = useQuery({
    queryKey: ['agents', projectId],
    queryFn:  () => agentsApi.list(projectId),
    staleTime: 30_000,
  })

  const { mutate: doReset } = useMutation({
    mutationFn: (name: string) => agentsApi.resetAgent(name),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })
  const { mutate: doClone } = useMutation({
    mutationFn: ({ name, newName }: { name: string; newName: string }) =>
      agentsApi.cloneAgent(name, newName),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })
  const { mutate: doDelete } = useMutation({
    mutationFn: (name: string) => agentsApi.remove(name),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })

  // Navigate to agent edit: switch view mode then open the detail form
  const goToAgent = (name: string) => { setViewAgents(); setViewAgentDetail(name) }

  const defaultCount = agents.filter(a =>  a.is_default).length
  const customCount  = agents.filter(a => !a.is_default).length

  const visible =
    filterKind === 'default' ? agents.filter(a =>  a.is_default) :
    filterKind === 'custom'  ? agents.filter(a => !a.is_default) :
    agents

  if (isLoading) return (
    <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={12} className="animate-spin" /> Loading…
    </div>
  )
  if (agents.length === 0) return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <Bot size={24} style={{ color: 'var(--text-muted)' }} />
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No agents scoped to this project yet.</p>
      <button className="btn ghost" onClick={() => goToAgent('__new__')}>
        <Plus size={12} /> Create agent
      </button>
    </div>
  )

  const FILTER_TABS: { key: 'all' | 'default' | 'custom'; label: string; count: number }[] = [
    { key: 'custom',  label: 'Custom',  count: customCount   },
    { key: 'default', label: 'Default', count: defaultCount  },
    { key: 'all',     label: 'All',     count: agents.length },
  ]

  return (
    <div>
      {/* ── Header: filter (left) + New Agent (right) ── */}
      <div className="flex items-center justify-between mb-3" style={{ gap: 8 }}>

        {/* Segmented pill filter */}
        <div style={{
          display: 'inline-flex', alignItems: 'center',
          background: 'var(--bg-surface2)',
          border: '1px solid var(--border-dim)',
          borderRadius: 9, padding: 2, gap: 1,
        }}>
          {FILTER_TABS.map(({ key, label, count }) => {
            const active = filterKind === key
            return (
              <button
                key={key}
                onClick={() => setFilterKind(key)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '3px 10px', borderRadius: 7, border: 'none',
                  fontSize: 11.5, fontWeight: active ? 600 : 400,
                  cursor: 'pointer',
                  background: active ? 'var(--bg-surface3)' : 'transparent',
                  color:      active ? 'var(--text-primary)' : 'var(--text-muted)',
                  transition: 'background 100ms ease, color 100ms ease',
                  boxShadow:  active ? '0 1px 3px rgba(0,0,0,0.25)' : 'none',
                }}
              >
                {label}
                <span style={{
                  fontSize: 9.5, fontWeight: 700, minWidth: 14, textAlign: 'center',
                  padding: '0 4px', borderRadius: 99,
                  background: active ? 'rgba(0,201,255,0.15)' : 'rgba(255,255,255,0.06)',
                  color:      active ? 'var(--eva-primary)'   : 'var(--text-faint)',
                  transition: 'background 100ms ease, color 100ms ease',
                }}>
                  {count}
                </span>
              </button>
            )
          })}
        </div>

        {/* New Agent button */}
        <button className="btn primary" onClick={() => goToAgent('__new__')}>
          <Plus size={12} /> New Agent
        </button>
      </div>

      {/* Empty filtered state */}
      {visible.length === 0 && (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <Bot size={20} style={{ color: 'var(--text-muted)' }} />
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            No {filterKind} agents in this project.
          </p>
        </div>
      )}

      {/* Agent cards */}
      {visible.map(a => (
        <AgentListCard
          key={a.name}
          agent={a}
          onEdit={() => goToAgent(a.name)}
          onClone={(newName) => doClone({ name: a.name, newName })}
          onReset={() => doReset(a.name)}
          onDelete={() => doDelete(a.name)}
        />
      ))}
    </div>
  )
}
