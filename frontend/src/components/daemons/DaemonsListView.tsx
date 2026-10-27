import { useState } from 'react'
import { Clock, Plus, Search, X, Loader2, AlertTriangle } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { daemonsApi } from '@/api/daemons'
import type { DaemonConfig } from '@/types/api'
import { useChatStore } from '@/store/chatStore'
import DaemonListCard from './DaemonListCard'

// ── Daemons list (Level 1) ────────────────────────────────────────────────────
export default function DaemonsListView({ onEdit, onCreate }: { onEdit: (name: string) => void; onCreate: () => void }) {
  const [search, setSearch] = useState('')
  const qc = useQueryClient()
  const setViewDaemon = useChatStore(s => s.setViewDaemon)

  const { data: daemons = [], isLoading, isError } = useQuery<DaemonConfig[]>({
    queryKey: ['daemons'],
    queryFn:  daemonsApi.list,
  })


  const { mutate: doDelete } = useMutation({
    mutationFn: (name: string) => daemonsApi.remove(name),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['daemons'] }),
  })

  const standalone = daemons.filter(d => !d.project_id)

  const filtered = search.trim()
    ? standalone.filter(d =>
        d.name.toLowerCase().includes(search.toLowerCase()) ||
        (d.description ?? '').toLowerCase().includes(search.toLowerCase())
      )
    : standalone

  return (
    <div className="detail">
      <div className="detail-header">
        <div className="dh-icon" style={{ background: 'rgba(0,201,255,0.1)', borderColor: 'rgba(0,201,255,0.25)', color: 'var(--eva-primary)' }}>
          <Clock size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">Daemon Agents</h2>
          <p className="dh-sub">Scheduled and event-driven background agents · {daemons.length} configured</p>
        </div>
        <div className="detail-actions">
          <button className="btn primary" onClick={onCreate}>
            <Plus size={12} /> New Daemon
          </button>
        </div>
      </div>

      <div className="detail-body" style={{ maxWidth: 'none' }}>
        <div className="search-field" style={{ marginBottom: 16 }}>
          <Search size={13} />
          <input
            placeholder="Search daemons…"
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
            <AlertTriangle size={14} /> Failed to load daemons
          </div>
        )}
        {!isLoading && !isError && filtered.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-14 text-center">
            <Clock size={28} style={{ color: 'var(--text-muted)' }} />
            <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              {search ? `No daemons match "${search}"` : 'No daemons configured yet.'}
            </p>
            {!search && (
              <button className="btn ghost" onClick={onCreate}>
                <Plus size={12} /> Create first daemon
              </button>
            )}
          </div>
        )}

        {!isLoading && filtered.map(daemon => (
          <DaemonListCard
            key={daemon.name}
            daemon={daemon}
            onEdit={() => onEdit(daemon.name)}
            onDelete={() => doDelete(daemon.name)}
            onMonitor={() => setViewDaemon(daemon.name, daemon.current_execution_id ?? null)}
          />
        ))}
      </div>
    </div>
  )
}
