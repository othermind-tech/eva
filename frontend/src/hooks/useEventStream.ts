import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { openEventStream } from '@/api/events'
import { useNotificationStore } from '@/store/notificationStore'
import { useChatStore } from '@/store/chatStore'
import type { Conversation } from '@/types/api'

export function useEventStream(): void {
  const push        = useNotificationStore((s) => s.push)
  const queryClient = useQueryClient()
  const esRef       = useRef<EventSource | null>(null)

  useEffect(() => {
    let cancelled  = false
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    async function connect() {
      if (cancelled) return

      // Close any stale connection before opening a fresh one with a new token
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }

      // Defined before openEventStream so the closure is ready when onerror fires
      const onError = () => {
        if (cancelled || retryTimer !== null) return
        // Native EventSource reconnects to the same expired-token URL — useless.
        // Instead: schedule a fresh connect() that will fetch a new SSE token.
        retryTimer = setTimeout(() => { retryTimer = null; void connect() }, 3_000)
      }

      try {
        const es = await openEventStream(
          (evt) => {
            if (evt.event === 'conversation.titled') {
              // Optimistic in-place patch for the list cache
              queryClient.setQueryData<Conversation[]>(['conversations'], old =>
                old?.map(c =>
                  c.id === evt.conversation_id ? { ...c, title: evt.title } : c
                ) ?? old
              )
              // Always invalidate — recovers from race where the new conversation
              // wasn't in cache yet when the title event arrived (setQueryData no-ops silently)
              void queryClient.invalidateQueries({ queryKey: ['conversations'] })
              queryClient.setQueriesData<{ id: string; title?: string; created_at: string }[]>(
                { queryKey: ['project-conversations'] },
                old =>
                  old?.map(c =>
                    c.id === evt.conversation_id ? { ...c, title: evt.title } : c
                  ) ?? old
              )
              queryClient.setQueryData<Conversation>(
                ['conversation', evt.conversation_id],
                old => old ? { ...old, title: evt.title } : old,
              )
              // Same race as the list cache: if ChatHeader's query for this exact
              // conversation wasn't mounted/fetched yet when the event arrived, the
              // patch above no-ops. Invalidate so it's correct whenever it does mount.
              void queryClient.invalidateQueries({ queryKey: ['conversation', evt.conversation_id] })
              return
            }

            // Section 85 (P-19) — keep the Activity tab's queries fresh as these
            // arrive, in addition to (not instead of) the normal notification
            // push below. Invalidate rather than optimistic-patch: none of these
            // payloads carry the full row shape the Activity tab needs to render,
            // so a refetch is the only way to get an accurate one anyway.
            if (evt.event === 'execution.awaiting_wakeup') {
              // Both — the row moves from 'running' shape to 'paused' shape,
              // and the paused (wakeups) list is what actually gains a new
              // entry here. Missing this one left new paused rows invisible
              // until something else (e.g. reopening the drawer) refetched.
              void queryClient.invalidateQueries({ queryKey: ['wakeups'] })
              void queryClient.invalidateQueries({ queryKey: ['executions-running'] })
            }
            if (evt.event === 'wakeup.resolved') {
              void queryClient.invalidateQueries({ queryKey: ['wakeups'] })
              void queryClient.invalidateQueries({ queryKey: ['executions-running'] })
            }
            if (evt.event === 'execution.completed' || evt.event === 'execution.failed') {
              void queryClient.invalidateQueries({ queryKey: ['executions-running'] })
            }
            // Section 90 (V6.49 Phase 3) — background-process lifecycle. Same
            // refresh-only treatment as above: neither payload carries the
            // full row shape the Activity tab needs, so refetch rather than patch.
            if (evt.event === 'process.stopped' || evt.event === 'process.crashed') {
              void queryClient.invalidateQueries({ queryKey: ['executions-running'] })
            }

            // Section 102 - the session's primary was unusable at run time and a fallback
            // is answering. Remember it for the live turn (ExecutionStatusBar matches on
            // execution id), and still push it to the drawer below.
            if (evt.event === 'provider.fallback_promoted') {
              useChatStore.getState().setProviderPromotion({ executionId: evt.execution_id, summary: evt.summary })
            }

            push(evt)
          },
          onError,
        )
        if (cancelled) { es.close(); return }
        esRef.current = es
      } catch {
        // Token fetch failed — retry after a longer delay
        if (!cancelled && retryTimer === null) {
          retryTimer = setTimeout(() => { retryTimer = null; void connect() }, 5_000)
        }
      }
    }

    void connect()

    return () => {
      cancelled = true
      if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
      if (esRef.current) { esRef.current.close(); esRef.current = null }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []) // push and queryClient are stable Zustand/TanStack refs
}
