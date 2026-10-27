// V4.4 (B-025): Write-only secrets management + Ollama host management
// Design note: there is intentionally NO GET /secrets/{provider} — keys are write-only.
// The Ollama base URL is NOT a secret and IS returned in responses.

import { del, get, post } from './client'
import type { SecretWriteResponse, SecretDeleteResponse, OllamaBaseUrlResponse } from '@/types/api'

export const secretsApi = {
  // POST /secrets/{provider} — upsert API key; activates in running server (no restart)
  // Supported providers are whatever GET /providers/list returns (Section 98) — don't
  // hardcode the set here. Aliases are accepted (together -> together_ai).
  // Errors: 400 (ollama / unknown provider), 401 (unauth), 422 (empty key)
  setProviderKey: (provider: string, api_key: string) =>
    post<SecretWriteResponse>(`/secrets/${provider}`, { api_key }),

  // DELETE /secrets/{provider} (Section 99) — removes the key from the OS credential
  // store, which is shared by ALL EVA accounts on this machine. Idempotent: 200 with
  // deleted:false if nothing was stored. Errors: 400 (ollama / unknown), 429, 503
  // (credential store unreachable — the key was NOT removed).
  deleteProviderKey: (provider: string) =>
    del<SecretDeleteResponse>(`/secrets/${provider}`),

  // GET /secrets/ollama/base-url — read current OLLAMA_HOST + reachability probe
  getOllamaBaseUrl: () =>
    get<OllamaBaseUrlResponse>('/secrets/ollama/base-url'),

  // POST /secrets/ollama/base-url — update OLLAMA_HOST in .env + env; probes reachability
  setOllamaBaseUrl: (base_url: string) =>
    post<OllamaBaseUrlResponse>('/secrets/ollama/base-url', { base_url }),
}
