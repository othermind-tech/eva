import { get, post, put, del } from './client'
import type {
  MCPConfig, MCPHealth, MCPResource, MCPPrompt, MCPTool,
  UpdateMCPConfigResponse, SeedDefaultMCPsResponse,
} from '@/types/api'

export const mcpApi = {
  /** List MCP configs — optionally filtered by project (V5.7) */
  list: (projectId?: string) =>
    get<MCPConfig[]>(projectId ? `/mcp/?project_id=${encodeURIComponent(projectId)}` : '/mcp/'),
  create:        (body: Omit<MCPConfig, 'id' | 'project_paused' | 'created_at' | 'updated_at'>) =>
    post<MCPConfig>('/mcp/', body),
  // Section 91 — if the update leaves/makes the config enabled, the backend
  // re-syncs its tools before responding (tool_count/sync_error reflect
  // that). A cold `npx` install (e.g. first-time Playwright) can take
  // 20-60s; give this call more headroom than the global 30s default so a
  // slow-but-successful sync isn't misreported as a failure.
  update:        (configId: string, body: Partial<MCPConfig>) =>
    put<UpdateMCPConfigResponse>(`/mcp/${configId}`, body, { timeout: 120_000 }),
  delete:        (configId: string) => del<void>(`/mcp/${configId}`),
  sync:          (configId: string) => post<void>(`/mcp/${configId}/sync`),
  healthAll:     () => get<MCPHealth[]>('/mcp/health'),
  getResources:  (configId: string) => get<MCPResource[]>(`/mcp/${configId}/resources`),
  getTools:      (configId: string) => get<MCPTool[]>(`/mcp/${configId}/tools`),
  getPrompts:    (configId: string) => get<MCPPrompt[]>(`/mcp/${configId}/prompts`),
  /** (Re)seed default MCP configs (e.g. Playwright) for the current user.
   *  Insert-only and idempotent — a config with the same name is skipped. */
  seedDefaults:  () => post<SeedDefaultMCPsResponse>('/mcp/seed-defaults'),
}
