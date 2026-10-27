/**
 * useExecutionReplay — connects to GET /executions/{id}/stream via fetch+SSE.
 *
 * Works for both:
 *   - Completed executions: backend replays all stored events quickly
 *   - Live executions: streams events in real-time (same endpoint)
 *
 * Reuses the same SSE dispatch logic and chatStore actions as useSSE,
 * but triggers on executionId changes (not conversationId + executionId pair).
 */
import { useEffect, useRef, useCallback } from 'react'
import { SSE_EXECUTION_ENDPOINT } from '@/lib/constants'
import { useChatStore } from '@/store/chatStore'
import { authApi } from '@/api/auth'
import { useAuthStore } from '@/store/authStore'
import type {
  SSEEvent, SSEText, SSEReasoning, SSEToolCall,
  SSEToolResult, SSEUsage, SSEError, SSESubagent, SSEStepProgress, SSECancelled,
} from '@/types/api'

export function useExecutionReplay(executionId: string | null) {
  const abortRef      = useRef<AbortController | null>(null)
  const connectedRef  = useRef<string | null>(null)
  const msgIdRef      = useRef<string | null>(null)

  const {
    startAssistantMessage,
    appendTextChunk, appendReasoningChunk,
    addToolCall, resolveToolCall, finalizeMessage,
    setStreaming, setReplayLoading, addStreamError, setStepProgress,
    startSubagentDelegation, appendSubagentEvent, finalizeSubagentDelegation,
  } = useChatStore()

  const connect = useCallback(async (execId: string) => {
    // Acquire a single-use SSE token
    let sseToken: string
    try {
      const { sse_token } = await authApi.getSseToken()
      sseToken = sse_token
    } catch {
      if (import.meta.env.DEV) console.error('[useExecutionReplay] failed to get SSE token')
      setReplayLoading(false)
      return
    }

    const url = `${SSE_EXECUTION_ENDPOINT(execId)}?sse_token=${encodeURIComponent(sseToken)}`
    if (import.meta.env.DEV) console.debug('[useExecutionReplay] connecting →', url)

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    let response: Response
    try {
      const accessToken = useAuthStore.getState().token
      response = await fetch(url, {
        signal: controller.signal,
        headers: {
          'Accept':        'text/event-stream',
          'Cache-Control': 'no-cache',
          ...(accessToken ? { 'Authorization': `Bearer ${accessToken}` } : {}),
        },
      })
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      if (import.meta.env.DEV) console.error('[useExecutionReplay] fetch failed', err)
      setReplayLoading(false)
      return
    }

    if (!response.ok || !response.body) {
      if (import.meta.env.DEV) console.error('[useExecutionReplay] HTTP', response.status)
      setReplayLoading(false)
      return
    }

    // Connection open — stop loading spinner, start streaming
    setReplayLoading(false)

    // ── Event dispatcher ────────────────────────────────────────────────────
    const dispatch = (raw: string, eventType: string) => {
      let evt: SSEEvent
      try { evt = JSON.parse(raw) } catch { return }
      const type = evt.type ?? eventType

      switch (type) {
        case 'init':
          // Start a fresh assistant message block for this execution
          msgIdRef.current = startAssistantMessage()
          if (import.meta.env.DEV) console.debug('[useExecutionReplay] init → msgId', msgIdRef.current)
          break

        case 'text': {
          const e = evt as SSEText
          if (msgIdRef.current) appendTextChunk(msgIdRef.current, e.content)
          break
        }

        case 'reasoning': {
          const e = evt as SSEReasoning
          if (msgIdRef.current) appendReasoningChunk(msgIdRef.current, e.content)
          break
        }

        case 'tool_call': {
          const e = evt as SSEToolCall
          if (msgIdRef.current) addToolCall(msgIdRef.current, e)
          break
        }

        case 'tool_result': {
          const e = evt as SSEToolResult
          if (msgIdRef.current) resolveToolCall(msgIdRef.current, e)
          break
        }

        case 'usage': {
          const e = evt as SSEUsage
          if (msgIdRef.current) finalizeMessage(msgIdRef.current, e)
          break
        }

        case 'subagent': {
          const e = evt as SSESubagent
          if (msgIdRef.current) {
            const ts = e.timestamp ?? Date.now()
            if (e.event === 'started') {
              startSubagentDelegation(msgIdRef.current, e.agent_name, ts)
            } else if (e.event === 'completed' || e.event === 'failed') {
              finalizeSubagentDelegation(msgIdRef.current, e.agent_name, e.event, ts)
            } else {
              appendSubagentEvent(msgIdRef.current, e.agent_name, {
                event:    e.event,
                content:  e.content,
                toolName: e.tool_name ?? null,
                ts,
              })
            }
          }
          break
        }

        case 'cancelled': {
          const e = evt as SSECancelled
          if (import.meta.env.DEV) console.debug('[useExecutionReplay] cancelled event', e)
          if (msgIdRef.current) {
            appendTextChunk(msgIdRef.current, `\n\n_Cancelled${e.reason === 'user' ? ' by user' : ''}._`)
          }
          break
        }

        case 'finish': {
          if (msgIdRef.current) {
            const state = useChatStore.getState()
            const msg   = state.messages.find(m => m.id === msgIdRef.current)
            if (!msg?.usage) finalizeMessage(msgIdRef.current)
          }
          break
        }

        case 'done':
          setStreaming(false)
          connectedRef.current = null
          controller.abort()
          break

        case 'error': {
          const e = evt as SSEError
          if (import.meta.env.DEV) console.warn('[useExecutionReplay] error event', e)
          addStreamError(
            e.message ?? 'Replay error.',
            e.error_type ?? 'unknown',
            e.provider,
          )
          setStreaming(false)
          break
        }

        case 'step_progress': {
          const e = evt as SSEStepProgress
          setStepProgress({
            step_index:  e.step_index,
            total_steps: e.total_steps,
            step_title:  e.step_title,
            step_status: e.step_status,
          })
          break
        }

        case 'ping':
          break

        default:
          if (import.meta.env.DEV) console.debug('[useExecutionReplay] unknown event:', type)
      }
    }

    // ── SSE line parser (same as useSSE) ────────────────────────────────────
    const reader  = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer    = ''
    let eventType = 'message'
    let dataLines: string[] = []

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          if (import.meta.env.DEV) console.debug('[useExecutionReplay] stream ended')
          break
        }

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line === '' || line === '\r') {
            if (dataLines.length > 0) dispatch(dataLines.join('\n'), eventType)
            eventType = 'message'
            dataLines = []
          } else if (line.startsWith('event:')) {
            eventType = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim())
          }
        }
      }
    } catch (err: unknown) {
      if (!(err instanceof Error && err.name === 'AbortError')) {
        if (import.meta.env.DEV) console.error('[useExecutionReplay] read error', err)
        setStreaming(false)
      }
    } finally {
      reader.releaseLock()
      setStreaming(false)
    }
  }, [
    startAssistantMessage, appendTextChunk, appendReasoningChunk,
    addToolCall, resolveToolCall, finalizeMessage,
    setStreaming, setReplayLoading, addStreamError, setStepProgress,
    startSubagentDelegation, appendSubagentEvent, finalizeSubagentDelegation,
  ])

  useEffect(() => {
    if (!executionId) return
    if (connectedRef.current === executionId) return
    connectedRef.current = executionId
    connect(executionId)

    return () => {
      abortRef.current?.abort()
    }
  }, [executionId, connect])
}
