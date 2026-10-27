import { get, post, del } from './client'
import type { SessionConfig, SessionResponse, PreviewEndpointsResponse } from '@/types/api'

export const sessionApi = {
  // Read full current session config (B-006) — use on app startup to hydrate UI
  getSession: () =>
    get<SessionResponse>('/session'),

  setSession: (config: SessionConfig) =>
    post<SessionResponse>('/set-session', config),

  // CDP-embedded preview pane — called by Electron's main process (via IPC
  // round-trip through the renderer, which already holds the auth token)
  // once its CDP relay is up/torn down. See core/mcp/preview_registry.py.
  registerPreviewEndpoint: (name: string, cdpEndpoint: string) =>
    post<{ status: string }>('/session/preview-endpoints', { name, cdp_endpoint: cdpEndpoint }),

  unregisterPreviewEndpoint: (name: string) =>
    del<{ status: string }>(`/session/preview-endpoints/${encodeURIComponent(name)}`),

  listPreviewEndpoints: () =>
    get<PreviewEndpointsResponse>('/session/preview-endpoints'),
}
