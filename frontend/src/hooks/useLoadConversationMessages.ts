// Section 95 — extracted from ChatTab.tsx's handleSelect() so ChatView.tsx's
// retry button (shown when messagesLoadError is set) can re-run exactly the
// same load, not a second, slightly-different copy of it.
import { useCallback } from 'react'
import { conversationsApi } from '@/api/conversations'
import { useChatStore, apiMessageToUI } from '@/store/chatStore'
import { extractErrorDetail, isUnreachableError } from '@/lib/errors'

export function useLoadConversationMessages() {
  const clearMessages        = useChatStore(s => s.clearMessages)
  const setActiveConversation = useChatStore(s => s.setActiveConversation)
  const setActiveExecution   = useChatStore(s => s.setActiveExecution)
  const loadMessages         = useChatStore(s => s.loadMessages)
  const setMessagesLoadError = useChatStore(s => s.setMessagesLoadError)
  const setWorkerDown        = useChatStore(s => s.setWorkerDown)

  return useCallback(async (conversationId: string) => {
    clearMessages()
    setActiveConversation(conversationId)
    setActiveExecution(null)
    try {
      const { messages: msgs, has_more, next_cursor } = await conversationsApi.getMessages(conversationId, { limit: 100 })
      loadMessages(msgs.map(apiMessageToUI), has_more, next_cursor)
    } catch (err) {
      if (import.meta.env.DEV) console.error('[useLoadConversationMessages] load failed', err)
      if (isUnreachableError(err)) {
        setWorkerDown(true)
        setMessagesLoadError("Couldn't reach the server. Check your connection and try again.")
      } else {
        setMessagesLoadError(extractErrorDetail(err, "Couldn't load this conversation."))
      }
    }
  }, [clearMessages, setActiveConversation, setActiveExecution, loadMessages, setMessagesLoadError, setWorkerDown])
}
