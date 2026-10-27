// B-051 — User-facing event SSE feed
// GET /events/stream?sse_token={token}
// Persistent SSE; push daemon + execution lifecycle events in real time.

import { authApi } from './auth'
import { API_BASE } from '@/lib/constants'
import type { AppEvent } from '@/types/api'

export type { AppEvent }

/** Acquire an SSE token then open the event stream. Returns the EventSource. */
export async function openEventStream(
  onEvent:      (evt: AppEvent) => void,
  onError?:     (err: Event) => void,
): Promise<EventSource> {
  const { sse_token } = await authApi.getSseToken()
  const url = `${API_BASE}/events/stream?sse_token=${encodeURIComponent(sse_token)}`
  const es  = new EventSource(url)

  const eventNames: AppEvent['event'][] = [
    'daemon.completed',
    'daemon.failed',
    'daemon.triggered',
    'execution.completed',
    'execution.failed',
    'provider.fallback_promoted',
    'execution.awaiting_wakeup',
    'wakeup.resolved',
    'conversation.titled',
    'process.stopped',
    'process.crashed',
  ]

  for (const name of eventNames) {
    es.addEventListener(name, (raw: MessageEvent) => {
      try {
        const data = JSON.parse(raw.data as string) as AppEvent
        onEvent(data)
      } catch { /* ignore malformed */ }
    })
  }

  if (onError) es.onerror = onError
  return es
}
