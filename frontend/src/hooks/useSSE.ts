import { useEffect, useRef, useCallback } from 'react'
import { SSE_ENDPOINT, SSE_TIMEOUT_MS } from '@/lib/constants'
import { useChatStore } from '@/store/chatStore'
import { authApi } from '@/api/auth'
import { useAuthStore } from '@/store/authStore'
import type {
  SSEEvent, SSEText, SSEReasoning, SSEToolCall,
  SSEToolResult, SSEApprovalRequired, SSEPlanApprovalRequired,
  SSEUsage, SSEStatus, SSEError, SSESubagent, SSEStepProgress,
  SSEProviderFallback, SSEStepLimitReached, SSECancelled,
} from '@/types/api'

/**
 * useSSE — fetch-based SSE reader (replaces native EventSource).
 *
 * Using fetch instead of EventSource gives us:
 *   - Actual HTTP status codes when the request fails
 *   - Authorization header support (Bearer token)
 *   - Reliable streaming through the Vite dev proxy
 *
 * Flow:
 *   1. POST /auth/sse-token → single-use UUID (30s TTL)
 *   2. fetch GET /eva_response?conversation_id=...&sse_token=...
 *   3. Stream body lines → parse SSE → dispatch events
 *
 * Only opens when BOTH conversationId AND executionId are set.
 */
export function useSSE(conversationId: string | null, executionId: string | null) {
  const abortRef         = useRef<AbortController | null>(null)
  const timeoutRef       = useRef<ReturnType<typeof setTimeout> | null>(null)
  const msgIdRef         = useRef<string | null>(null)
  const connectedExecRef = useRef<string | null>(null)

  const {
    startAssistantMessage, appendTextChunk, appendReasoningChunk,
    addToolCall, resolveToolCall, finalizeMessage, attachUsage,
    setStreaming, setWorkerDown, setApproval, setActiveExecution,
    addStreamError, appendErrorBlock, setStepProgress, applyStepProgress, setStatusMessage, setProviderFallbackNotice,
    startSubagentDelegation, appendSubagentEvent, finalizeSubagentDelegation,
    setStepLimitGate,
  } = useChatStore()

  const resetTimeout = useCallback(() => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    timeoutRef.current = setTimeout(() => {
      if (import.meta.env.DEV) console.warn('[useSSE] timeout — no ping received, aborting')
      setWorkerDown(true)
      setStreaming(false)
      abortRef.current?.abort()
    }, SSE_TIMEOUT_MS)
  }, [setWorkerDown, setStreaming])

  const connect = useCallback(async (convId: string) => {
    // Step 1 — fetch a single-use SSE token (valid for 30s)
    let sseToken: string
    try {
      const { sse_token } = await authApi.getSseToken()
      sseToken = sse_token
    } catch (err) {
      if (import.meta.env.DEV) console.error('[useSSE] failed to get SSE token', err)
      setStreaming(false)
      setWorkerDown(true)
      return
    }

    // Step 2 — open a fetch stream with the sse_token
    const url = `${SSE_ENDPOINT(convId)}&sse_token=${encodeURIComponent(sseToken)}`
    if (import.meta.env.DEV) console.debug('[useSSE] connecting (fetch) →', url)

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    let response: Response
    try {
      const accessToken = useAuthStore.getState().token
      response = await fetch(url, {
        signal: controller.signal,
        headers: {
          'Accept': 'text/event-stream',
          'Cache-Control': 'no-cache',
          ...(accessToken ? { 'Authorization': `Bearer ${accessToken}` } : {}),
        },
      })
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      if (import.meta.env.DEV) console.error('[useSSE] fetch connection failed', err)
      setStreaming(false)
      setWorkerDown(true)
      return
    }

    if (!response.ok) {
      const body = await response.text().catch(() => '(no body)')
      if (import.meta.env.DEV) console.error(`[useSSE] HTTP ${response.status} from SSE endpoint:`, body)
      setStreaming(false)
      setWorkerDown(response.status >= 500)
      return
    }

    if (!response.body) {
      if (import.meta.env.DEV) console.error('[useSSE] response has no body')
      setStreaming(false)
      return
    }

    resetTimeout()
    if (import.meta.env.DEV) console.debug('[useSSE] stream open, reading…')

    // ── Event dispatcher ────────────────────────────────────────────────────
    const dispatch = (raw: string, eventType: string) => {
      resetTimeout()
      let evt: SSEEvent
      try { evt = JSON.parse(raw) } catch { return }

      // Prefer the event type from the SSE "event:" line if the JSON doesn't have a type
      const type = evt.type ?? eventType

      switch (type) {
        case 'init':
          msgIdRef.current = startAssistantMessage()
          setWorkerDown(false)
          if (import.meta.env.DEV) console.debug('[useSSE] init → msgId', msgIdRef.current)
          break

        case 'status': {
          // Transient status message (e.g. "Summarising conversation…")
          // Arrives before the first text chunk; cleared when text starts flowing.
          const e = evt as SSEStatus
          setStatusMessage(e.message)
          break
        }

        case 'provider_fallback': {
          const e = evt as SSEProviderFallback
          setProviderFallbackNotice({ provider: e.provider, model: e.model })
          break
        }

        case 'text': {
          const e = evt as SSEText
          // Clear any status message once real content starts arriving
          setStatusMessage(null)
          if (msgIdRef.current) appendTextChunk(msgIdRef.current, e.content)
          break
        }

        case 'reasoning': {
          const e = evt as SSEReasoning
          if (msgIdRef.current) appendReasoningChunk(msgIdRef.current, e.content)
          break
        }

        case 'tool_call': {
          const raw = evt as unknown as Record<string, unknown>
          // Backend uses call_id / arguments; spec uses tool_call_id / tool_input
          const normalised: SSEToolCall = {
            type:         'tool_call',
            timestamp:    (raw.timestamp as number) ?? Date.now(),
            tool_name:    raw.tool_name as string,
            tool_call_id: (raw.tool_call_id ?? raw.call_id) as string,
            tool_input:   (raw.tool_input ?? raw.arguments ?? {}) as Record<string, unknown>,
          }
          if (msgIdRef.current) addToolCall(msgIdRef.current, normalised)
          break
        }

        case 'tool_result': {
          const raw = evt as unknown as Record<string, unknown>
          // Backend uses call_id; spec uses tool_call_id — normalise to match stored blocks
          const normalised: SSEToolResult = {
            type:         'tool_result',
            timestamp:    (raw.timestamp as number) ?? Date.now(),
            tool_name:    raw.tool_name as string,
            tool_call_id: (raw.tool_call_id ?? raw.call_id) as string,
            result:       raw.result,
            is_error:     raw.is_error as boolean | undefined,
          }
          if (import.meta.env.DEV) console.debug('[useSSE] tool_result → resolving', normalised.tool_call_id, 'msgId', msgIdRef.current)
          if (msgIdRef.current) resolveToolCall(msgIdRef.current, normalised)
          break
        }

        case 'approval_required': {
          const e = evt as SSEApprovalRequired
          setActiveExecution(e.execution_id)
          setApproval({ status: 'tool', event: e })
          break
        }

        case 'plan_approval_required': {
          const e = evt as SSEPlanApprovalRequired
          setActiveExecution(e.execution_id)
          setApproval({ status: 'plan', event: e, plan: e.plan })
          break
        }

        case 'usage': {
          // Attach token counts to the message without stopping the stream.
          // The 'done' event is authoritative for stream termination.
          // Multiple usage events may arrive (running totals from OpenAI/Groq) —
          // last one wins, which is always the final count.
          const e = evt as SSEUsage
          if (msgIdRef.current) attachUsage(msgIdRef.current, e)
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
                toolName: e.tool_name,
                ts,
              })
            }
          }
          break
        }

        case 'cancelled': {
          // Emitted the moment the execution's broker status flips to 'cancelled' —
          // arrives just before 'finish'/'done' on the same connection. Without this,
          // a cancelled run that produced no (or only partial) text ends as a silent
          // blank bubble — this gives the user an explicit acknowledgment instead.
          // isStreaming is NOT touched here; 'done' remains the sole authority for that
          // (see the 'done' case below and MessageInput.tsx's stopRequested flow).
          const e = evt as SSECancelled
          if (import.meta.env.DEV) console.debug('[useSSE] cancelled event', e)
          if (msgIdRef.current) {
            appendTextChunk(msgIdRef.current, `\n\n_Cancelled${e.reason === 'user' ? ' by user' : ''}._`)
          }
          break
        }

        case 'finish': {
          // 'finish' signals the LLM stopped generating. Usage may or may not have
          // arrived yet. Do NOT stop the stream here — 'done' is authoritative.
          // Just ensure the message is finalised if 'done' never comes.
          if (msgIdRef.current) {
            const state = useChatStore.getState()
            const msg   = state.messages.find(m => m.id === msgIdRef.current)
            // If done never fires, finalise here as a safety net
            if (!msg?.usage) {
              // Give a short grace period for a trailing usage event
              setTimeout(() => {
                finalizeMessage(msgIdRef.current!)
              }, 500)
            }
          }
          break
        }

        case 'done':
          if (timeoutRef.current) clearTimeout(timeoutRef.current)
          // Finalise the message — usage should already be attached via attachUsage
          if (msgIdRef.current) finalizeMessage(msgIdRef.current)
          setStreaming(false)
          connectedExecRef.current = null
          controller.abort()
          break

        case 'error': {
          const e = evt as SSEError
          if (import.meta.env.DEV) console.warn('[useSSE] error event from server', e)
          // Per-step errors (e.g. a duplicate-tool-call guard) are often
          // recoverable — the backend may replan and keep streaming on this
          // same connection. Append the error inline into the message
          // currently in progress rather than ending the stream; 'done' is
          // the only authoritative end-of-execution signal (the SSE relay
          // always sends it once the execution's status is truly terminal —
          // see eva_response.py's terminal-state handling).
          if (msgIdRef.current) {
            appendErrorBlock(msgIdRef.current, e.message ?? 'An unknown error occurred.', e.error_type ?? 'unknown', e.provider)
          } else {
            addStreamError(
              e.message ?? 'An unknown error occurred.',
              e.error_type ?? 'unknown',
              e.provider,
            )
          }
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
          if (msgIdRef.current) applyStepProgress(msgIdRef.current, e)
          break
        }

        case 'step_limit_reached': {
          const e = evt as SSEStepLimitReached
          setActiveExecution(e.execution_id)
          setStepLimitGate({ status: 'active', event: e })
          break
        }

        case 'ping':
          break

        default:
          if (import.meta.env.DEV) console.debug('[useSSE] unknown event type:', type, raw)
      }
    }

    // ── SSE line parser ──────────────────────────────────────────────────────
    const reader  = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer    = ''
    let eventType = 'message'
    let dataLines: string[] = []

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          if (import.meta.env.DEV) console.debug('[useSSE] stream ended (done)')
          // Safety net: the SSE 'done' event case already handles the normal
          // close path (finalize + setStreaming(false) + abort()). If the
          // HTTP body just ends without ever sending that event (e.g. an
          // unhandled backend exception), this is the only signal we get —
          // without it isStreaming would stay stuck true forever.
          setStreaming(false)
          break
        }

        buffer += decoder.decode(value, { stream: true })

        // Process complete lines
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''   // last incomplete line stays in buffer

        for (const line of lines) {
          if (line === '' || line === '\r') {
            // Blank line = dispatch accumulated event
            if (dataLines.length > 0) {
              dispatch(dataLines.join('\n'), eventType)
            }
            eventType = 'message'
            dataLines = []
          } else if (line.startsWith('event:')) {
            eventType = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim())
          }
          // Ignore id: and retry: lines
        }
      }
    } catch (err: unknown) {
      if (!(err instanceof Error && err.name === 'AbortError')) {
        if (import.meta.env.DEV) console.error('[useSSE] stream read error', err)
        setStreaming(false)
      }
    } finally {
      reader.releaseLock()
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      setStreaming(false)
    }
  }, [
    resetTimeout, startAssistantMessage, appendTextChunk, appendReasoningChunk,
    addToolCall, resolveToolCall, finalizeMessage, setStreaming,
    setWorkerDown, setApproval, setActiveExecution, addStreamError, setStepProgress,
    applyStepProgress, setStatusMessage, setProviderFallbackNotice,
    startSubagentDelegation, appendSubagentEvent, finalizeSubagentDelegation,
  ])

  // Only open SSE when we have BOTH a conversationId AND a fresh executionId
  useEffect(() => {
    if (!conversationId || !executionId) return
    if (connectedExecRef.current === executionId) return
    connectedExecRef.current = executionId
    connect(conversationId)

    return () => {
      abortRef.current?.abort()
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [conversationId, executionId, connect])
}
