// P-19 / Section 84 — background execution wakeups (schedule_wakeup tool)
import { get, post } from './client'
import type { WakeupSummary } from '@/types/api'

export const wakeupsApi = {
  list: () =>
    get<WakeupSummary[]>('/wakeups'),

  resume: (id: string) =>
    post<{ status: 'resumed'; new_execution_id: string }>(`/wakeups/${id}/resume`),

  cancel: (id: string) =>
    post<{ status: 'cancelled' }>(`/wakeups/${id}/cancel`),
}
