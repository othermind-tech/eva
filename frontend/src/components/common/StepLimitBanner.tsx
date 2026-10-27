import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Zap } from 'lucide-react'
import { useChatStore } from '@/store/chatStore'
import { chatApi } from '@/api/chat'
import type { APIError } from '@/types/api'

export function StepLimitBanner() {
  const gate                = useChatStore(s => s.stepLimitGate)
  const clearStepLimitGate  = useChatStore(s => s.clearStepLimitGate)

  if (gate.status !== 'active') return null

  return <StepLimitBannerContent event={gate.event} onDone={clearStepLimitGate} />
}

function StepLimitBannerContent({
  event, onDone,
}: { event: import('@/types/api').SSEStepLimitReached; onDone: () => void }) {
  const [staleMessage, setStaleMessage] = useState<string | null>(null)

  const { mutate: extend, isPending } = useMutation({
    mutationFn: (decision: 'continue' | 'stop') =>
      chatApi.extendExecution(event.execution_id, {
        decision,
        step_extension_token: event.step_extension_token,
      }),
    onSuccess: onDone,
    onError: (e: unknown) => {
      // client.ts's response interceptor rejects with a flat { code, detail,
      // status } object, not an AxiosError — status lives at the top level.
      const status = (e as APIError)?.status
      if (status === 404) {
        // Gate already auto-stopped server-side (stale/expired token) —
        // expected, not a bug. Briefly explain, then dismiss.
        setStaleMessage('This request already timed out.')
        setTimeout(onDone, 2000)
      }
      // Any other error: leave the banner up so the user can retry.
    },
  })

  return (
    <div className="worker-banner">
      <Zap size={14} style={{ flexShrink: 0 }} />
      <span style={{ flex: 1, fontSize: 12 }}>
        {staleMessage ?? (
          <>
            Step limit reached ({event.steps_used}/{event.max_steps}). Continue for {event.max_steps} more?
            {' '}
            <span style={{ opacity: 0.75 }}>
              ({event.extensions_used} of {event.extensions_max} extensions used)
            </span>
          </>
        )}
      </span>
      {!staleMessage && (
        <>
          <button
            onClick={() => extend('stop')}
            disabled={isPending}
            style={{ marginLeft: 0, opacity: isPending ? 0.5 : 0.85 }}
          >
            Stop
          </button>
          <button
            onClick={() => extend('continue')}
            disabled={isPending}
            style={{
              marginLeft: 8, opacity: isPending ? 0.5 : 1,
              background: 'var(--amber)', color: 'var(--bg-base)',
              borderRadius: 6, padding: '3px 10px', fontWeight: 600,
            }}
          >
            Continue
          </button>
        </>
      )}
    </div>
  )
}
