// Execution status bar — shown between ChatHeader and message list while streaming
// Displays: live status label (PEV step or "Running"), active tool, elapsed time, token count
import { useEffect, useRef, useState } from 'react'
import { Wrench, Zap, RefreshCw, ArrowRightLeft } from 'lucide-react'
import { useChatStore } from '@/store/chatStore'
import type { ToolCallBlock } from '@/store/chatStore'

export default function ExecutionStatusBar() {
  const isStreaming        = useChatStore(s => s.isStreaming)
  const stepProgress       = useChatStore(s => s.stepProgress)
  const executionStartedAt = useChatStore(s => s.executionStartedAt)
  const messages           = useChatStore(s => s.messages)
  const streamingMsgId     = useChatStore(s => s.streamingMsgId)
  const statusMessage           = useChatStore(s => s.statusMessage)
  const providerFallbackNotice  = useChatStore(s => s.providerFallbackNotice)
  const providerPromotion       = useChatStore(s => s.providerPromotion)
  const activeExecutionId       = useChatStore(s => s.activeExecutionId)

  const [elapsed, setElapsed]   = useState(0)
  const [visible, setVisible]   = useState(false)
  const [leaving, setLeaving]   = useState(false)
  const leaveTimerRef           = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Derive final token usage from the last assistant message
  const lastAssistant = [...messages].reverse().find(m => m.role === 'assistant')
  const usage = lastAssistant?.usage

  // Derive active (unresolved) tool from the currently streaming message
  const streamingMsg = streamingMsgId ? messages.find(m => m.id === streamingMsgId) : null
  const activeTool = streamingMsg?.blocks
    .filter((b): b is ToolCallBlock => b.kind === 'tool_call' && b.result === undefined)
    .slice(-1)[0]?.tool_name ?? null

  // Elapsed timer — ticks every second while streaming
  useEffect(() => {
    if (!isStreaming || !executionStartedAt) {
      setElapsed(executionStartedAt ? Math.floor((Date.now() - executionStartedAt) / 1000) : 0)
      return
    }
    setElapsed(Math.floor((Date.now() - executionStartedAt) / 1000))
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - executionStartedAt) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [isStreaming, executionStartedAt])

  // Visibility: appear on stream start, stay for 3s after done, then fade out
  useEffect(() => {
    if (isStreaming) {
      if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current)
      setLeaving(false)
      setVisible(true)
    } else if (visible) {
      setLeaving(true)
      leaveTimerRef.current = setTimeout(() => setVisible(false), 3000)
    }
    return () => {
      if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming])

  if (!visible) return null

  // Build status label
  let label: string
  let labelColor = 'var(--text-secondary)'
  if (!isStreaming) {
    label = 'Done'
    labelColor = 'var(--success)'
  } else if (stepProgress?.step_status === 'planning') {
    label = 'Planning…'
    labelColor = 'var(--neural)'
  } else if (stepProgress?.step_status === 'awaiting_plan_approval') {
    label = 'Awaiting approval…'
    labelColor = 'var(--amber)'
  } else if (stepProgress?.step_status === 'cancelled') {
    label = 'Cancelled'
  } else if (stepProgress?.step_status === 'plan_complete') {
    label = 'Running'
  } else if (stepProgress && stepProgress.step_status !== 'completed') {
    const idx = stepProgress.step_index + 1
    label = `Step ${idx}/${stepProgress.total_steps}: ${stepProgress.step_title}`
    if (stepProgress.step_status === 'replanning' || stepProgress.step_status === 'replan_skipped') labelColor = 'var(--amber)'
    else if (stepProgress.step_status === 'verifying') labelColor = 'var(--neural)'
    else if (stepProgress.step_status === 'skipped') { label += ' · skipped'; labelColor = 'var(--text-dim)' }
  } else {
    label = 'Running'
  }

  const dotColor = !isStreaming
    ? 'var(--success)'
    : stepProgress?.step_status === 'replanning'
      ? 'var(--amber)'
      : 'var(--eva-primary)'

  return (
    <div
      className="flex items-center gap-3 px-4 transition-opacity duration-700"
      style={{
        height: '28px',
        borderBottom: '1px solid var(--border-low)',
        background: 'rgba(0,0,0,0.18)',
        opacity: leaving ? 0 : 1,
        fontSize: '11px',
        fontFamily: 'var(--font-mono, "JetBrains Mono", monospace)',
        letterSpacing: '0.01em',
        overflow: 'hidden',
      }}
    >
      {/* Status dot */}
      <span
        className="inline-block flex-shrink-0 rounded-full"
        style={{
          width: '6px',
          height: '6px',
          background: dotColor,
          boxShadow: isStreaming ? `0 0 5px ${dotColor}` : 'none',
          animation: isStreaming ? 'eva-pulse 1.6s ease-in-out infinite' : 'none',
        }}
      />

      {/* Status label */}
      <span
        className="flex-1 truncate"
        style={{ color: labelColor }}
      >
        {label}
      </span>

      {/* Transient status message (e.g. "Summarising conversation…") */}
      {statusMessage && isStreaming && (
        <span className="flex items-center gap-1 flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
          <RefreshCw size={9} className="animate-spin" />
          {statusMessage}
        </span>
      )}

      {/* Provider fallback notice — shown while the fallback is actively answering */}
      {providerFallbackNotice && isStreaming && (
        <span className="flex items-center gap-1 flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
          <ArrowRightLeft size={9} />
          Now using {providerFallbackNotice.model}
        </span>
      )}

      {/* Section 102 - primary provider unusable at run time; a fallback is answering.
          The server sentence is long, so truncate it and keep the full text on hover. */}
      {providerPromotion && providerPromotion.executionId === activeExecutionId && isStreaming && (
        <span
          className="flex items-center gap-1 min-w-0"
          title={providerPromotion.summary}
          style={{ color: 'var(--amber)', maxWidth: 360 }}
        >
          <ArrowRightLeft size={9} className="shrink-0" />
          <span className="truncate">{providerPromotion.summary}</span>
        </span>
      )}

      {/* Active tool */}
      {activeTool && isStreaming && (
        <span className="flex items-center gap-1 flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
          <Wrench size={9} />
          {activeTool}
        </span>
      )}

      {/* Elapsed time */}
      {executionStartedAt && (
        <span className="flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
          {elapsed}s
        </span>
      )}

      {/* Token count — show once available */}
      {usage && (
        <span
          className="flex items-center gap-1 flex-shrink-0"
          style={{ color: 'var(--text-dim)' }}
        >
          <Zap size={9} style={{ color: 'var(--neural)' }} />
          {(usage.total_tokens ?? 0).toLocaleString()} tokens
        </span>
      )}
    </div>
  )
}
