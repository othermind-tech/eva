import { get, post, put, del } from './client'
import type {
  SubagentConfigRequest,
  SubagentConfigResponse,
  SubagentDeleteResponse,
} from '@/types/api'

export interface SeedDefaultsResponse {
  status:         string
  agents_seeded:  number
  agents_skipped: number
  errors:         string[]
}

export const agentsApi = {
  /** List all configured subagents — optionally filtered by project (V5.7) */
  list: (projectId?: string) =>
    get<SubagentConfigResponse[]>(
      projectId ? `/agents?project_id=${encodeURIComponent(projectId)}` : '/agents'
    ),

  /** Get a single subagent config by name */
  getOne: (name: string) =>
    get<SubagentConfigResponse>(`/agents/${encodeURIComponent(name)}`),

  /** Create a new subagent (upsert — POST /agents) */
  create: (data: SubagentConfigRequest) =>
    post<SubagentConfigResponse>('/agents', data),

  /** Update an existing subagent (PUT /agents/{name} — 404 if not found) */
  update: (name: string, data: Partial<SubagentConfigRequest>) =>
    put<SubagentConfigResponse>(`/agents/${encodeURIComponent(name)}`, data),

  /** Delete a subagent (DELETE /agents/{name} — 404 if not found) */
  remove: (name: string) =>
    del<SubagentDeleteResponse>(`/agents/${encodeURIComponent(name)}`),

  // ── V5.7 — Default agent management ───────────────────────
  seedDefaults: (force = false) =>
    post<SeedDefaultsResponse>(`/agents/seed-defaults${force ? '?force=true' : ''}`),

  resetAgent: (name: string) =>
    post<SubagentConfigResponse>(`/agents/${encodeURIComponent(name)}/reset`),

  cloneAgent: (name: string, newName: string) =>
    post<SubagentConfigResponse>(`/agents/${encodeURIComponent(name)}/clone`, { new_name: newName }),
}
