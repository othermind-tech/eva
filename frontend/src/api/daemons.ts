import { get, post, put, del } from './client'
import type {
  DaemonConfig,
  DaemonConfigRequest,
  DaemonTriggerResponse,
  DaemonDeleteResponse,
  DaemonWebhook,
  DaemonExecutionSummary,
} from '@/types/api'

export const daemonsApi = {
  /** List all daemons for the current user */
  list: () =>
    get<DaemonConfig[]>('/daemons'),

  /** Get a single daemon config by name */
  getOne: (name: string) =>
    get<DaemonConfig>(`/daemons/${encodeURIComponent(name)}`),

  /** Create or update a daemon by name (upsert — POST /daemons) */
  create: (data: DaemonConfigRequest) =>
    post<DaemonConfig>('/daemons', data),

  /** Update an existing daemon (PUT /daemons/{name}) */
  update: (name: string, data: Partial<DaemonConfigRequest>) =>
    put<DaemonConfig>(`/daemons/${encodeURIComponent(name)}`, data),

  /** Delete a daemon (DELETE /daemons/{name}) */
  remove: (name: string) =>
    del<DaemonDeleteResponse>(`/daemons/${encodeURIComponent(name)}`),

  /** Fire daemon immediately — optional context_message passed to the run */
  trigger: (name: string, contextMessage?: string) =>
    post<DaemonTriggerResponse>(
      `/daemons/${encodeURIComponent(name)}/trigger`,
      contextMessage ? { context_message: contextMessage } : {},
    ),

  /** Pause a scheduled daemon (lifecycle) */
  pause: (name: string) =>
    post<DaemonConfig>(`/daemons/${encodeURIComponent(name)}/pause`, {}),

  /** Resume a paused daemon (lifecycle) */
  resume: (name: string) =>
    post<DaemonConfig>(`/daemons/${encodeURIComponent(name)}/resume`, {}),

  /** Reset failure count on a permanently-failed daemon */
  resetFailures: (name: string) =>
    post<DaemonConfig>(`/daemons/${encodeURIComponent(name)}/reset-failures`, {}),

  /** Create a webhook for a daemon */
  createWebhook: (name: string) =>
    post<DaemonWebhook>(`/daemons/${encodeURIComponent(name)}/webhook`, {}),

  /** Get existing webhook for a daemon (404 if none) */
  getWebhook: (name: string) =>
    get<DaemonWebhook>(`/daemons/${encodeURIComponent(name)}/webhook`),

  /** Delete (revoke) the webhook for a daemon */
  deleteWebhook: (name: string) =>
    del<{ status: 'deleted' }>(`/daemons/${encodeURIComponent(name)}/webhook`),

  /** List past executions for a daemon (B-062) */
  getExecutions: (name: string, limit = 20) =>
    get<DaemonExecutionSummary[]>(
      `/daemons/${encodeURIComponent(name)}/executions?limit=${limit}`,
    ),

  /** Cancel a currently-running daemon execution (B-063) */
  cancel: (name: string) =>
    post<{ cancelled: boolean; execution_id: string }>(
      `/daemons/${encodeURIComponent(name)}/cancel`,
      {},
    ),

  /** Clone a daemon — new copy starts disabled (V5.7) */
  cloneDaemon: (name: string, newName: string, projectId?: string) =>
    post<DaemonConfig>(`/daemons/${encodeURIComponent(name)}/clone`, {
      new_name: newName,
      ...(projectId ? { project_id: projectId } : {}),
    }),
}
