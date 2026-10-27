import { get, post } from './client'
import type {
  Tool,
  ProviderListItem,
  LiveProviderModelsOut,
  ModelCapabilities,
  ProviderCostsOut,
} from '@/types/api'

export const toolsApi = {
  listAll:     () => get<Tool[]>('/tools'),
}

export const providersApi = {
  // V4.5: GET /providers/list — the backend's curated provider list (the only provider list the UI uses), with is_configured / chat_ready flags
  // Replaces the removed GET /providers endpoint
  list: () => get<ProviderListItem[]>('/providers/list'),

  // V4.3 (B-024): live model listing — 5-min TTL cache on backend; pass ?live=true to bypass
  // Returns 424 if provider API key is not configured (except Ollama)
  // Section 98 (V6.59): always asks for agent_usable_only=true — a model the
  // agent loop can't drive is exactly one a run now refuses, so no selector
  // should ever offer it. model_id is the BARE id; send it back unchanged.
  getModels: (provider: string, forceLive = false) =>
    get<LiveProviderModelsOut>(
      `/providers/${provider}/models?agent_usable_only=true${forceLive ? '&live=true' : ''}`
    ),

  // V4.5: per-model capability profile — supports_reasoning, supports_tool_calls, pricing, etc.
  // model_id uses {path} route type so slashes are safe (LiteLLM-prefixed IDs)
  getCapabilities: (provider: string, modelId: string) =>
    get<ModelCapabilities>(
      `/providers/${encodeURIComponent(provider)}/models/${encodeURIComponent(modelId)}/capabilities`
    ),

  // V4.3: flush the model-listing TTL cache for one provider or all
  refreshModels: (provider?: string) => {
    const qs = provider ? `?provider=${encodeURIComponent(provider)}` : ''
    return post<{ provider?: string; message: string }>(`/providers/models/refresh${qs}`)
  },

  // V4.3 (B-023): aggregated token usage + estimated USD spend
  getCosts: (options?: { provider?: string; from_date?: string; to_date?: string }) => {
    const params = new URLSearchParams()
    if (options?.provider)  params.set('provider',   options.provider)
    if (options?.from_date) params.set('from_date',  options.from_date)
    if (options?.to_date)   params.set('to_date',    options.to_date)
    const qs = params.toString()
    return get<ProviderCostsOut>(`/providers/costs${qs ? `?${qs}` : ''}`)
  },
}
