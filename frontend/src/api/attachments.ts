import client, { del, get } from './client'
import type {
  AttachmentResponse,
  ConversationAttachmentResponse,
  LinkAttachmentBody,
} from '@/types/api'

export const attachmentsApi = {
  // ── File/Image Library ──────────────────────────────────────

  // Upload a file or image to the attachment library.
  // Automatically detects 'file' vs 'image' from MIME type on the backend.
  upload: (file: File, projectId?: string): Promise<AttachmentResponse> => {
    const form = new FormData()
    form.append('file', file)
    if (projectId) form.append('project_id', projectId)
    return client.post<AttachmentResponse>('/attachments', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then(r => r.data)
  },

  // ── Conversation Links ──────────────────────────────────────

  // Link an attachment, conversation ref, or execution ref to a conversation
  linkToConversation: (
    conversationId: string,
    body: LinkAttachmentBody,
  ): Promise<ConversationAttachmentResponse> =>
    client.post<ConversationAttachmentResponse>(
      `/conversations/${conversationId}/attachments`,
      body,
    ).then(r => r.data),

  // List all items linked to a conversation
  getConversationAttachments: (conversationId: string): Promise<ConversationAttachmentResponse[]> =>
    get<ConversationAttachmentResponse[]>(`/conversations/${conversationId}/attachments`),

  // Unlink a specific item from a conversation (does NOT delete the library file)
  unlinkFromConversation: (
    conversationId: string,
    conversationAttachmentId: string,
  ): Promise<{ status: string; unlinked_ca_id: string }> =>
    del<{ status: string; unlinked_ca_id: string }>(
      `/conversations/${conversationId}/attachments/${conversationAttachmentId}`,
    ),
}
