import { get } from './client'
import type { UsageSummary } from '@/types/api'

export interface UsageRangeParams {
  start?: string   // ISO 8601 datetime
  end?:   string   // ISO 8601 datetime
}

export const metricsApi = {
  // GET /metrics/usage/summary — all-time aggregate totals
  summary: () =>
    get<UsageSummary>('/metrics/usage/summary'),

  // GET /metrics/usage?start=&end= — time-range aggregate (omit params for all-time)
  usage: (params?: UsageRangeParams) =>
    get<UsageSummary>('/metrics/usage', { params }),
}
