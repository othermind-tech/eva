import { Brain, Plus } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { memoryApi } from '@/api/memory'
import { useChatStore } from '@/store/chatStore'
import UserFactsView from './UserFactsView'
import AgentMemoryView from './AgentMemoryView'

export default function MemoryBrowseCard() {
  const memoryTab     = useChatStore(s => s.memoryTab)
  const setMemoryTab  = useChatStore(s => s.setMemoryTab)
  const memoryAdding  = useChatStore(s => s.memoryAdding)
  const setMemoryAdding = useChatStore(s => s.setMemoryAdding)

  const { data: facts = [] } = useQuery({
    queryKey: ['memory-user-facts'],
    queryFn:  memoryApi.getUserFacts,
  })
  const { data: scopes = [] } = useQuery({
    queryKey: ['memory-agent-scopes'],
    queryFn:  memoryApi.listAgentScopes,
  })

  const totalEntries = scopes.reduce((n, s) => n + s.entry_count, 0)

  return (
    <div className="detail">
      {/* ── Shared header ── */}
      <div className="detail-header">
        <div className="dh-icon" style={{ background: 'rgba(124,108,243,0.12)', borderColor: 'rgba(124,108,243,0.25)', color: 'var(--neural)' }}>
          <Brain size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">Memory</h2>
          <p className="dh-sub">
            {memoryTab === 'user'
              ? `${facts.length} user fact${facts.length !== 1 ? 's' : ''}`
              : `${totalEntries} agent entr${totalEntries !== 1 ? 'ies' : 'y'} · ${scopes.length} scope${scopes.length !== 1 ? 's' : ''}`}
          </p>
        </div>

        {/* Tab switcher */}
        <div className="detail-tabs flex items-center gap-0.5 p-0.5 rounded-lg shrink-0" style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}>
          {(['user', 'agent'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setMemoryTab(tab)}
              className="px-3 py-1.5 rounded-md text-xs font-medium transition-all"
              style={memoryTab === tab
                ? { background: 'var(--bg-surface3)', color: 'var(--text-primary)', border: '1px solid var(--border-mid)' }
                : { color: 'var(--text-muted)', border: '1px solid transparent' }
              }
            >
              {tab === 'user' ? 'User Facts' : 'Agent Memory'}
            </button>
          ))}
        </div>

        <div className="detail-actions">
          {memoryTab === 'user' && !memoryAdding && (
            <button className="btn primary" onClick={() => setMemoryAdding(true)}>
              <Plus size={12} /> Add Fact
            </button>
          )}
        </div>
      </div>

      {/* ── Tab body ── */}
      {memoryTab === 'user'  ? <UserFactsView />  : <AgentMemoryView />}
    </div>
  )
}
