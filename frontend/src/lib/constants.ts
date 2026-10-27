// API base URL — proxied through Vite in dev, direct in prod
export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

// SSE endpoint — backend route is GET /eva_response?conversation_id=<id>&sse_token=<token>
export const SSE_ENDPOINT = (conversationId: string) =>
  `${API_BASE}/eva_response?conversation_id=${encodeURIComponent(conversationId)}`

// Execution replay/live stream — GET /executions/{id}/stream?sse_token=<token>
export const SSE_EXECUTION_ENDPOINT = (executionId: string) =>
  `${API_BASE}/executions/${encodeURIComponent(executionId)}/stream`

// Local storage keys
export const LS_TOKEN            = 'eva_access_token'
export const LS_USER             = 'eva_user'
export const LS_REFRESH_TOKEN    = 'eva_refresh_token'
export const LS_TOKEN_EXPIRES_AT = 'eva_token_expires_at'
// Zustand persist key — must match the `name` in authStore's persist() call
export const LS_AUTH_STORE       = 'eva-auth'

// Streaming config
export const SSE_TIMEOUT_MS       = 60_000   // 60s without a ping → assume worker down

// UI config
export const NAV_RAIL_WIDTH          = 60    // px
export const SIDEBAR_WIDTH           = 280   // px
export const CONTEXT_PANEL_WIDTH     = 380   // px
export const MAX_INPUT_ROWS          = 12    // textarea max rows before scroll
export const TEXTAREA_ROW_HEIGHT_PX  = 24   // px per row for height calculation
export const SCROLL_AT_BOTTOM_PX     = 80   // px from bottom = "at bottom"
export const SCROLL_DELAY_MS         = 50   // ms after streaming start before scrollIntoView
export const HEALTH_CHECK_INTERVAL_MS = 30_000  // 30s health-poll interval in NavRail
export const SUCCESS_FLASH_MS         = 2_000   // 2s success message duration
export const RAG_SEARCH_LIMIT         = 5       // default number of RAG search results
export const RAG_POLL_INTERVAL_MS     = 2_000   // 2s ingest job polling interval
export const RAG_POLL_MAX_RETRIES     = 30      // ~60s max polling before giving up
export const RENAME_FOCUS_DELAY_MS    = 30      // ms before focusing rename input
export const TOKENS_PER_MILLION       = 1_000_000  // for cost display formatting
export const FALLBACK_SELECT_FOCUS_MS = 10      // ms before focusing searchable select
