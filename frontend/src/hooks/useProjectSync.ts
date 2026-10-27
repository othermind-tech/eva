import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { projectsApi } from '@/api/projects'
import { executionsApi } from '@/api/executions'
import type { ExecutionStatus } from '@/types/api'

const POLL_MS = 2000

// Deliberately an explicit TERMINAL set, not an explicit "keep polling" set.
// Found live: a daemon run hit the step-limit ask-to-continue gate and
// returned 'awaiting_step_extension', which isn't 'pending'/'running' — an
// inverted (non-terminal-list) check silently stopped polling on it, well
// before the run had actually finished. An unknown/future status should
// default to "still going", not "must be done".
const TERMINAL_STATUSES = new Set<ExecutionStatus>(['completed', 'failed', 'cancelled'])

/**
 * Section 83 (Phase C) — manual project resync. Shared by the project detail
 * header and the sidebar project row so both trigger/poll the same way
 * rather than hand-rolling this twice. Each mounted instance tracks only
 * syncs it itself triggered (same limitation the existing Pause/Resume/
 * Archive buttons already have — no cross-surface shared state today).
 */
export function useProjectSync(projectId: string) {
  const qc = useQueryClient()
  const [executionId, setExecutionId] = useState<string | null>(null)
  const [triggerError, setTriggerError] = useState<string | null>(null)
  const prevStatusRef = useRef<ExecutionStatus | null>(null)

  const { mutate: triggerMutate, isPending: starting } = useMutation({
    mutationFn: () => projectsApi.sync(projectId),
    onSuccess: (data) => {
      setTriggerError(null)
      setExecutionId(data.execution_id)
    },
    onError: (err) => {
      const apiErr = err as { status?: number; detail?: string }
      setTriggerError(apiErr.detail || 'Sync failed to start.')
    },
  })

  const { data: statusData } = useQuery({
    queryKey: ['project-sync-status', executionId],
    queryFn:  () => executionsApi.getStatus(executionId!),
    enabled:  !!executionId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && !TERMINAL_STATUSES.has(status) ? POLL_MS : false
    },
  })

  const status  = statusData?.status ?? null
  const syncing = starting || (status !== null && !TERMINAL_STATUSES.has(status))

  // Refresh project data (and its knowledge preview, if visible) once the sync lands
  useEffect(() => {
    if (status && TERMINAL_STATUSES.has(status) && status !== prevStatusRef.current) {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['project-files', projectId] })
    }
    prevStatusRef.current = status
  }, [status, projectId, qc])

  return {
    sync: () => { setTriggerError(null); triggerMutate() },
    syncing,
    status,
    triggerError,
  }
}
