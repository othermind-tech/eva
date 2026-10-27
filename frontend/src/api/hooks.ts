import client from './client'
import type { HookScript, HookScriptListResponse } from '@/types/api'

// ─── Hook Scripts API (Section 56 — B-064 V4.45) ─────────────────────────────
// Backend endpoints: POST/GET/PUT/DELETE /hooks/scripts

export const hooksApi = {
  /** List all user hook scripts */
  listScripts: async (): Promise<HookScript[]> => {
    const res = await client.get<HookScriptListResponse>('/hooks/scripts')
    return res.data.scripts
  },

  /** Upload a new script (multipart: name, description?, file) */
  uploadScript: async (
    name: string,
    file: File,
    description?: string,
  ): Promise<HookScript> => {
    const form = new FormData()
    form.append('name', name)
    form.append('file', file)
    if (description) form.append('description', description)
    // client's instance default is application/json; every multipart call must override it
    // (see attachments.ts / projects.ts) or the server can't parse the form and answers 422.
    const res = await client.post<HookScript>('/hooks/scripts', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return res.data
  },

  /** Get raw script content (plain text) */
  getScriptContent: async (name: string): Promise<string> => {
    const res = await client.get<string>(
      `/hooks/scripts/${encodeURIComponent(name)}/content`,
      { responseType: 'text' },
    )
    return res.data
  },

  /** Replace script content (multipart: description?, file) */
  updateScript: async (
    name: string,
    file: File,
    description?: string,
  ): Promise<HookScript> => {
    const form = new FormData()
    form.append('file', file)
    if (description !== undefined) form.append('description', description ?? '')
    const res = await client.put<HookScript>(
      `/hooks/scripts/${encodeURIComponent(name)}`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
    return res.data
  },

  /** Delete a script */
  deleteScript: async (name: string): Promise<void> => {
    await client.delete(`/hooks/scripts/${encodeURIComponent(name)}`)
  },
}
