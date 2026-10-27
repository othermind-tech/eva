import client, { get, del, patch, post } from './client'
import type { Conversation, PaginatedMessagesResponse, PromoteResponse } from '@/types/api'

export type ExportFormat = 'json' | 'markdown' | 'text'

const EXT: Record<ExportFormat, string> = { json: 'json', markdown: 'md', text: 'txt' }

export async function exportConversation(
  conversationId: string,
  format: ExportFormat,
  includeToolCalls = true,
): Promise<void> {
  const res = await client.get(`/conversations/${conversationId}/export`, {
    params:       { format, include_tool_calls: includeToolCalls },
    responseType: 'blob',
  })

  // Prefer the server-provided filename from Content-Disposition
  const cd       = (res.headers['content-disposition'] as string | undefined) ?? ''
  const nameMatch = cd.match(/filename[^;=\n]*=["']?([^"';\n]+)["']?/)
  const filename  = nameMatch?.[1]?.trim()
    ?? `conversation_${conversationId}.${EXT[format]}`

  const url = URL.createObjectURL(res.data as Blob)
  const a   = Object.assign(document.createElement('a'), { href: url, download: filename })
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export const conversationsApi = {
  list: () =>
    get<Conversation[]>('/conversations'),

  get: (conversationId: string) =>
    get<Conversation>(`/conversations/${conversationId}`),

  // V4.1 (B-013): Now returns paginated envelope {messages, has_more, next_cursor}
  // Use ?limit= and ?before= for pagination. Default limit=100.
  getMessages: (conversationId: string, options?: { limit?: number; before?: string }) => {
    const params = new URLSearchParams()
    if (options?.limit) params.set('limit', String(options.limit))
    if (options?.before) params.set('before', options.before)
    const qs = params.toString()
    return get<PaginatedMessagesResponse>(
      `/conversations/${conversationId}/messages${qs ? `?${qs}` : ''}`
    )
  },

  // V3.3 (B-015): Rename a conversation
  rename: (conversationId: string, title: string) =>
    patch<Conversation>(`/conversations/${conversationId}`, { title }),

  // B-054: Per-conversation system prompt
  setSystemPrompt: (conversationId: string, systemPrompt: string) =>
    patch<Conversation>(`/conversations/${conversationId}`, { system_prompt: systemPrompt }),

  // Truncate conversation history from fromMessageId onwards (inclusive).
  // Called before edit/retry to keep the backend DB in sync with UI truncation.
  truncateFrom: (conversationId: string, fromMessageId: string) =>
    del<{ deleted: number }>(
      `/conversations/${conversationId}/messages?from_message_id=${encodeURIComponent(fromMessageId)}`
    ),

  delete: (conversationId: string) =>
    del<void>(`/conversations/${conversationId}`),

  // B-075 (V5.2): Attach conversation to project (Scenario 1) or copy files (Scenario 2)
  // Omit `files` (or pass []) for Scenario 1 (attach); pass filenames for Scenario 2 (file copy)
  promote: (conversationId: string, projectId: string, files?: string[]) =>
    post<PromoteResponse>(`/conversations/${conversationId}/promote`, {
      project_id: projectId,
      ...(files?.length ? { files } : {}),
    }),
}
