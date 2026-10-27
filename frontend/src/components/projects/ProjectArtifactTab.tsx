import { Loader2 } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { projectsApi } from '@/api/projects'
import type { ArtifactType } from '@/api/projects'

export default function ProjectArtifactTab({ projectId, type }: { projectId: string; type: ArtifactType }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['project-artifact', projectId, type],
    queryFn:  () => projectsApi.getArtifact(projectId, type),
    retry: false,
    staleTime: 30_000,
  })
  if (isLoading) return <div className="flex items-center gap-2 text-xs py-4" style={{ color: 'var(--text-muted)' }}><Loader2 size={12} className="animate-spin" /> Loading…</div>
  if (isError || !data) return <p className="text-xs py-2" style={{ color: 'var(--text-muted)' }}>Not available</p>
  return (
    <pre
      className="text-xs leading-relaxed whitespace-pre-wrap rounded-xl p-4"
      style={{
        background: 'var(--bg-surface1)',
        border: '1px solid var(--border-dim)',
        color: 'var(--text-secondary)',
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 11,
      }}
    >
      {typeof data === 'string' ? data : JSON.stringify(data, null, 2)}
    </pre>
  )
}
