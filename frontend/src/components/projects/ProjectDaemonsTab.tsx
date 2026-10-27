import { Clock, Plus, Loader2 } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { daemonsApi } from '@/api/daemons'
import type { DaemonConfig } from '@/types/api'
import { useChatStore } from '@/store/chatStore'
import DaemonListCard from '@/components/daemons/DaemonListCard'

export default function ProjectDaemonsTab({ projectId }: { projectId: string }) {
  const qc                   = useQueryClient()
  const setViewDaemons       = useChatStore(s => s.setViewDaemons)
  const setViewDaemonDetail  = useChatStore(s => s.setViewDaemonDetail)
  const setViewDaemon        = useChatStore(s => s.setViewDaemon)

  // Fetch the full daemon list (shared ['daemons'] cache) and filter client-side.
  // DaemonListCard requires DaemonConfig — the project-scoped endpoint only returns
  // a lightweight ProjectDaemon, so we pull the full list instead.
  const { data: allDaemons = [], isLoading } = useQuery<DaemonConfig[]>({
    queryKey: ['daemons'],
    queryFn:  daemonsApi.list,
    staleTime: 30_000,
  })
  const daemons = allDaemons.filter(d => d.project_id === projectId)

  const { mutate: doDelete } = useMutation({
    mutationFn: (name: string) => daemonsApi.remove(name),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['daemons'] }),
  })

  // Navigate to daemon edit: switch view mode then open the detail form
  const goToDaemon = (name: string) => { setViewDaemons(); setViewDaemonDetail(name) }

  if (isLoading) return (
    <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={12} className="animate-spin" /> Loading…
    </div>
  )
  if (daemons.length === 0) return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <Clock size={24} style={{ color: 'var(--text-muted)' }} />
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No daemons scoped to this project yet.</p>
      <button className="btn ghost" onClick={() => goToDaemon('__new__')}>
        <Plus size={12} /> Create daemon
      </button>
    </div>
  )
  return (
    <div>
      <div className="flex justify-end mb-3">
        <button className="btn primary" onClick={() => goToDaemon('__new__')}>
          <Plus size={12} /> New Daemon
        </button>
      </div>
      {daemons.map(d => (
        <DaemonListCard
          key={d.name}
          daemon={d}
          onEdit={() => goToDaemon(d.name)}
          onDelete={() => doDelete(d.name)}
          onMonitor={() => setViewDaemon(d.name, d.current_execution_id ?? null)}
        />
      ))}
    </div>
  )
}
