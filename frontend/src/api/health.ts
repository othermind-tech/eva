import client from './client'
import type { HealthReadyResponse } from '@/types/api'

// B-017: /health/ready is auth-gated (exposes internal infra topology).
// Use the main Axios client so the JWT is attached automatically.
// The public /health endpoint (load-balancer liveness) has no auth — not used here.
export const healthApi = {
  checkReady: async (): Promise<HealthReadyResponse> => {
    const { data } = await client.get<HealthReadyResponse>('/health/ready')
    return data
  },
}
