import { useState } from 'react'
import { Loader2, Search, X, Database, Lock } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { memoryApi } from '@/api/memory'
import { formatDate } from '@/lib/utils'
import { useChatStore } from '@/store/chatStore'

// ── Agent memory view ─────────────────────────────────────────────────────────
export default function AgentMemoryView() {
  const memoryScope    = useChatStore(s => s.memoryScope)
  const setMemoryScope = useChatStore(s => s.setMemoryScope)

  const [search, setSearch] = useState('')

  const { data: scopes = [] } = useQuery({
    queryKey: ['memory-agent-scopes'],
    queryFn:  memoryApi.listAgentScopes,
  })

  const { data: entries = [], isLoading } = useQuery({
    queryKey: ['memory-agent', memoryScope],
    queryFn:  () => memoryApi.getAgentEntries(memoryScope),
    enabled:  !!memoryScope,
  })

  const displayed = search.trim()
    ? entries.filter(e => e.content.toLowerCase().includes(search.toLowerCase()) || (e.key ?? '').toLowerCase().includes(search.toLowerCase()))
    : entries

  return (
    <div className="detail-body" style={{ maxWidth: 'none' }}>
      {/* Scope selector + search row */}
      <div className="flex items-center gap-3 mb-4">
        {/* Scope dropdown */}
        <div className="flex items-center gap-2 shrink-0">
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Scope</span>
          <select
            value={memoryScope}
            onChange={e => setMemoryScope(e.target.value)}
            className="form-input"
            style={{ fontSize: 12, fontFamily: 'JetBrains Mono, monospace', minWidth: 140, padding: '4px 8px' }}
          >
            {scopes.length === 0 && <option value={memoryScope}>{memoryScope || 'default'}</option>}
            {scopes.map(s => (
              <option key={s.scope_key} value={s.scope_key}>
                {s.scope_key} ({s.entry_count})
              </option>
            ))}
          </select>
        </div>

        {/* Search */}
        <div className="search-field flex-1">
          <Search size={13} />
          <input
            placeholder="Search entries…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
              <X size={11} />
            </button>
          )}
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center gap-2 py-10" style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          <Loader2 size={16} className="animate-spin" /> Loading…
        </div>
      )}

      {/* Empty */}
      {!isLoading && displayed.length === 0 && (
        <div className="flex flex-col items-center gap-3 py-14 text-center">
          <Database size={28} style={{ color: 'var(--text-muted)' }} />
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            {search
              ? `No entries match "${search}"`
              : scopes.length === 0
              ? 'No agent memory scopes yet.'
              : `No entries in scope "${memoryScope}".`}
          </p>
          {!search && scopes.length === 0 && (
            <p style={{ fontSize: 12, color: 'var(--text-dim)', maxWidth: 280, lineHeight: 1.6 }}>
              Agent memory is written automatically during conversations.
            </p>
          )}
        </div>
      )}

      {/* Entry rows */}
      {!isLoading && displayed.map(e => (
        <div key={e.id} className="kn-card">
          <div className="kn-head">
            <div className="row-icon" style={{ background: 'rgba(124,108,243,0.12)', color: 'var(--neural)', width: 32, height: 32, borderRadius: 9, flexShrink: 0 }}>
              <Database size={14} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              {e.key && (
                <p style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: 'var(--neural)', marginBottom: 2 }}>{e.key}</p>
              )}
              <p style={{ fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.6 }}>{e.content}</p>
              <div className="flex items-center gap-2 mt-1.5">
                {e.sensitive && (
                  <span style={{ fontSize: 10, color: 'var(--amber)', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)', borderRadius: 99, padding: '0 6px', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                    <Lock size={9} /> Sensitive
                  </span>
                )}
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {formatDate(e.updated_at ?? e.created_at ?? '')}
                </span>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
