import { get } from './client'
import type { ExecutionMetrics, ExecutionStatusResponse, ExecutionSummary, RunningExecutionSummary } from '@/types/api'

export const executionsApi = {
  // Lightweight status check (B-009) — poll this instead of /trace for status only
  getStatus: (executionId: string) =>
    get<ExecutionStatusResponse>(`/executions/${executionId}/status`),

  // Every live (non-terminal) execution for the user, chat + daemon alike —
  // used by the Electron app-close guard to decide whether to warn before quitting
  listRunning: () =>
    get<RunningExecutionSummary[]>('/executions/running'),

  // List executions with optional filters (B-021 + B-061)
  list: (options?: {
    conversationId?: string
    status?:         string
    triggeredBy?:    'conversation' | 'daemon'   // B-061
    daemonName?:     string                       // B-061
    limit?:          number
  }) => {
    const params = new URLSearchParams()
    if (options?.conversationId) params.set('conversation_id',  options.conversationId)
    if (options?.status)         params.set('execution_status', options.status)
    if (options?.triggeredBy)    params.set('triggered_by',     options.triggeredBy)
    if (options?.daemonName)     params.set('daemon_name',      options.daemonName)
    if (options?.limit)          params.set('limit',            String(options.limit))
    const qs = params.toString()
    return get<ExecutionSummary[]>(`/executions${qs ? `?${qs}` : ''}`)
  },

  // Full detail endpoint — only fetch once status is completed/failed
  getMetrics: (executionId: string) => get<ExecutionMetrics>(`/executions/${executionId}/metrics`),
}
