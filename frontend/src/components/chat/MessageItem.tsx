import React, { useState, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { useChatStore, type ChatMessageUI, type ContentBlock } from '@/store/chatStore'
import ToolCallCard from './ToolCallCard'
import CoordinatorPhaseCard from './CoordinatorPhaseCard'
import ReasoningBlock from './ReasoningBlock'
import SubagentActivityCard from './SubagentActivityCard'
import SensitiveMemoryCard from './SensitiveMemoryCard'
import PlanTraceCard from './PlanTraceCard'
import InlineDisclosure from './InlineDisclosure'
import { formatDate, formatTokens } from '@/lib/utils'
import { useAuthStore } from '@/store/authStore'
import { useSessionStore } from '@/store/sessionStore'
import { Zap, Copy, Check, AlertTriangle, Pencil, RotateCcw } from 'lucide-react'

interface Props {
  message:      ChatMessageUI
  onEditSend?:  (msgId: string, newText: string) => void
  onRetry?:     (msgId: string) => void
}

// ── Clipboard helper ──────────────────────────────────────────────────────────
function useCopy() {
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const copy = useCallback(async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopiedKey(key)
      setTimeout(() => setCopiedKey(null), 2000)
    } catch { /* ignore */ }
  }, [])
  return { copiedKey, copy }
}

// ── Extract plain text from content blocks ───────────────────────────────────
function getTextContent(blocks: ContentBlock[]): string {
  return blocks
    .filter(b => b.kind === 'text')
    .map(b => b.content)
    .join('\n\n')
}

// ── Extract text from React children (for code block copy) ───────────────────
function extractChildText(node: React.ReactNode): string {
  if (typeof node === 'string') return node
  if (typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractChildText).join('')
  if (React.isValidElement(node)) return extractChildText((node.props as { children?: React.ReactNode }).children)
  return ''
}

// ── Code block with copy button ───────────────────────────────────────────────
function CodeBlock({ children, ...props }: React.HTMLAttributes<HTMLPreElement>) {
  const [copied, setCopied] = useState(false)
  const text = extractChildText(children)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text.replace(/\n$/, ''))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* ignore */ }
  }

  return (
    <div className="relative group/code">
      <pre {...props}>
        {children}
      </pre>
      <button
        onClick={handleCopy}
        className="conv-actions absolute top-2.5 right-2.5 w-7 h-7 rounded-md flex items-center justify-center transition-all duration-150 opacity-0 group-hover/code:opacity-100"
        style={{
          background: 'var(--bg-surface2)',
          border: '1px solid var(--border-mid)',
          color: copied ? 'var(--success)' : 'var(--text-muted)',
        }}
        title={copied ? 'Copied!' : 'Copy code'}
      >
        {copied ? <Check size={11} /> : <Copy size={11} />}
      </button>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function MessageItem({ message, onEditSend, onRetry }: Props) {
  const { role, blocks, usage, createdAt, id } = message
  const isUser     = role === 'user'
  const isStreaming = useChatStore(s => s.streamingMsgId === id && s.isStreaming)
  const globalStreaming = useChatStore(s => s.isStreaming)
  const toggleReasoning = useChatStore(s => s.toggleReasoning)
  const toggleSubagent  = useChatStore(s => s.toggleSubagent)
  const username        = useAuthStore(s => s.username)
  const sessionProvider = useSessionStore(s => s.config.provider)

  const [editing, setEditing]   = useState(false)
  const [editText, setEditText] = useState('')
  const editRef = useRef<HTMLTextAreaElement>(null)
  const { copiedKey, copy }     = useCopy()

  const startEdit = () => {
    setEditText(textContent)
    setEditing(true)
    setTimeout(() => {
      if (editRef.current) {
        editRef.current.focus()
        editRef.current.selectionStart = editRef.current.value.length
      }
    }, 0)
  }

  const cancelEdit = () => setEditing(false)

  const submitEdit = () => {
    const trimmed = editText.trim()
    if (!trimmed || !onEditSend) return
    onEditSend(id, trimmed)
    setEditing(false)
  }

  const textContent  = getTextContent(blocks)
  const hasText      = textContent.length > 0
  const showThinking = isStreaming && !hasText && !isUser

  // Pre-compute for CoordinatorPhaseCard pending view — live count of delegated subagents
  const subagentCount = blocks.filter(b => b.kind === 'subagent_activity').length

  return (
    <div
      className="msg animate-fade-up"
      style={isStreaming && !isUser ? {
        borderLeft: '2px solid rgba(0,201,255,0.35)',
        boxShadow: 'inset 2px 0 12px rgba(0,201,255,0.06)',
      } : isUser ? {
        background: 'transparent',
      } : undefined}
    >
      {/* Avatar */}
      {isUser ? (
        <div className="msg-avatar user">
          {(username?.[0] ?? 'U').toUpperCase()}
        </div>
      ) : (
        <div
          className="msg-avatar eva"
          style={isStreaming ? {
            borderColor: 'rgba(0,201,255,0.5)',
            boxShadow: '0 0 14px rgba(0,201,255,0.25)',
          } : undefined}
        >
          <Zap
            size={13}
            style={{
              color: 'var(--eva-primary)',
              filter: isStreaming ? 'drop-shadow(0 0 4px rgba(0,201,255,0.6))' : 'none',
            }}
          />
        </div>
      )}

      {/* Body */}
      <div className="msg-body">
        {/* Name + timestamp */}
        <div className="msg-name">
          <span style={{ color: isUser ? 'var(--text-secondary)' : 'var(--eva-primary)' }}>
            {isUser ? (username ?? 'You') : 'EVA'}
          </span>
          <span className="time">{formatDate(createdAt.toISOString())}</span>
          {isStreaming && (
            <span style={{ fontSize: 11, color: 'rgba(0,201,255,0.6)', fontWeight: 400 }}>· generating</span>
          )}
        </div>

        {/* Thinking dots — shown before first token arrives */}
        {showThinking && (
          <div className="thinking">
            <span className="thinking-dot" style={{ '--delay': '0ms' } as React.CSSProperties} />
            <span className="thinking-dot" style={{ '--delay': '180ms' } as React.CSSProperties} />
            <span className="thinking-dot" style={{ '--delay': '360ms' } as React.CSSProperties} />
          </div>
        )}

        {/* Content blocks — or inline edit form for user messages */}
        <div className="msg-content">
          {isUser && editing ? (
            <div className="flex flex-col gap-2">
              <textarea
                ref={editRef}
                value={editText}
                onChange={e => setEditText(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitEdit() }
                  if (e.key === 'Escape') cancelEdit()
                }}
                rows={Math.max(2, editText.split('\n').length)}
                className="w-full resize-none text-sm leading-relaxed"
                style={{
                  background: 'var(--bg-surface2)',
                  border: '1px solid var(--border-mid)',
                  borderRadius: '12px',
                  padding: '12px 16px',
                  color: 'var(--text-primary)',
                  outline: 'none',
                  fontFamily: 'inherit',
                }}
              />
              <div className="flex justify-end gap-2">
                <button onClick={cancelEdit} className="btn ghost">Cancel</button>
                <button
                  onClick={submitEdit}
                  disabled={!editText.trim()}
                  className="btn primary"
                >
                  Send
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {blocks.map((block, idx) => (
                <BlockRenderer
                  key={idx}
                  block={block}
                  subagentCount={subagentCount}
                  onToggleReason={() => toggleReasoning(id, idx)}
                  onToggleSubagent={(name) => toggleSubagent(id, name)}
                  onRetry={onRetry ? () => onRetry(id) : undefined}
                />
              ))}
            </div>
          )}

          {/* Streaming cursor */}
          {isStreaming && hasText && <span className="streaming-cursor" />}
        </div>

        {/* Token usage footer */}
        {usage && !isUser && (
          <div
            className="flex items-center gap-3 mt-3 pt-2.5"
            style={{ borderTop: '1px solid var(--border-dim)' }}
          >
            <span className="text-xs" style={{ color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
              ↑ {formatTokens(usage.prompt_tokens ?? 0)} · ↓ {formatTokens(usage.completion_tokens ?? 0)} · {formatTokens(usage.total_tokens ?? 0)} total
            </span>
            {/* Fallback indicator — only shown when a different provider actually answered */}
            {usage.provider && usage.provider !== sessionProvider && (
              <span
                className="text-xs px-1.5 py-0.5 rounded flex-shrink-0"
                style={{
                  background: 'rgba(0,201,255,0.08)',
                  border: '1px solid rgba(0,201,255,0.2)',
                  color: 'var(--eva-primary)',
                  fontFamily: 'var(--font-mono)',
                }}
                title={`Answered via ${usage.provider} / ${usage.model}`}
              >
                via {usage.model ?? usage.provider}
              </span>
            )}
          </div>
        )}

        {/* Hover action buttons */}
        {!isStreaming && !editing && textContent && (
          <div className="msg-actions">
            {isUser && onEditSend && !globalStreaming && (
              <button className="icon-btn" onClick={startEdit} title="Edit message">
                <Pencil size={12} />
              </button>
            )}
            <button
              className="icon-btn"
              onClick={() => copy(textContent, id)}
              title={copiedKey === id ? 'Copied!' : 'Copy message'}
              style={{ color: copiedKey === id ? 'var(--success)' : undefined }}
            >
              {copiedKey === id ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Sensitive memory placeholder parser (B-033) ───────────────────────────────
// Backend injects [SENSITIVE: entry_id=<id> scope_key=<key> — ...] into agent
// response text when memory_recall hits a gated entry. We parse and replace it
// with the interactive SensitiveMemoryCard component.
const SENSITIVE_RE = /\[SENSITIVE:\s*entry_id=(\S+)\s+scope_key=(\S+)[^\]]*\]/g

interface SensitiveSegment { type: 'sensitive'; entryId: string; scopeKey: string }
interface TextSegment       { type: 'text';      text: string }
type TextWithSensitive = (TextSegment | SensitiveSegment)[]

function parseTextWithSensitive(content: string): TextWithSensitive {
  const segments: TextWithSensitive = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  SENSITIVE_RE.lastIndex = 0
  while ((match = SENSITIVE_RE.exec(content)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', text: content.slice(lastIndex, match.index) })
    }
    segments.push({ type: 'sensitive', entryId: match[1], scopeKey: match[2] })
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < content.length) {
    segments.push({ type: 'text', text: content.slice(lastIndex) })
  }
  return segments
}

// ── Block renderer ────────────────────────────────────────────────────────────
interface BlockProps {
  block:             ContentBlock
  subagentCount:     number
  onToggleReason:    () => void
  onToggleSubagent:  (agentName: string) => void
  onRetry?:          () => void
}

function BlockRenderer({ block, subagentCount, onToggleReason, onToggleSubagent, onRetry }: BlockProps) {
  if (block.kind === 'text') {
    const segments = parseTextWithSensitive(block.content)
    const hasSensitive = segments.some(s => s.type === 'sensitive')

    // Fast path — no sensitive placeholders; render markdown as-is
    if (!hasSensitive) {
      return (
        <div className="eva-prose message-chunk">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
            components={{
              pre: ({ children, ...props }) => (
                <CodeBlock {...props}>{children}</CodeBlock>
              ),
            }}
          >
            {block.content}
          </ReactMarkdown>
        </div>
      )
    }

    // Mixed path — interleave markdown chunks with SensitiveMemoryCard
    return (
      <div className="flex flex-col gap-2">
        {segments.map((seg, i) => {
          if (seg.type === 'sensitive') {
            return (
              <SensitiveMemoryCard
                key={`sensitive-${i}`}
                entryId={seg.entryId}
                scopeKey={seg.scopeKey}
              />
            )
          }
          // Only render non-empty text segments
          const trimmed = seg.text.trim()
          if (!trimmed) return null
          return (
            <div key={`text-${i}`} className="eva-prose message-chunk">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={{
                  pre: ({ children, ...props }) => (
                    <CodeBlock {...props}>{children}</CodeBlock>
                  ),
                }}
              >
                {seg.text}
              </ReactMarkdown>
            </div>
          )
        })}
      </div>
    )
  }

  if (block.kind === 'plan_trace') {
    return <PlanTraceCard block={block} />
  }

  if (block.kind === 'reasoning') {
    return (
      <ReasoningBlock
        content={block.content}
        collapsed={block.collapsed}
        onToggle={onToggleReason}
      />
    )
  }

  if (block.kind === 'tool_call') {
    if (block.tool_name === 'coordinate') {
      return <CoordinatorPhaseCard block={block} subagentCount={subagentCount} />
    }
    return <ToolCallCard block={block} />
  }

  if (block.kind === 'subagent_activity') {
    return (
      <SubagentActivityCard
        block={block}
        onToggle={() => onToggleSubagent(block.agentName)}
      />
    )
  }

  if (block.kind === 'error') {
    const errorLabels: Record<string, string> = {
      tool_call_failed: 'Tool call failed',
      provider_error:   'Provider error',
      timeout:          'Request timeout',
      unknown:          'Error',
      // Section 95 — a send/resend that never reached the server at all
      // (connection refused, DNS failure, timeout before any reply) vs. one
      // the server did respond to, just with something other than 403/424.
      network:      'Could not reach the server',
      send_failed:  'Message not sent',
    }
    const label = errorLabels[block.errorType] ?? 'Error'
    const firstLine = block.message.split('\n')[0]
    const summary = (
      <span className="flex items-center gap-1.5">
        <AlertTriangle size={11} style={{ color: 'var(--danger)', flexShrink: 0 }} />
        <span style={{ color: 'var(--danger)' }}>{label}</span>
        <span style={{ color: 'var(--text-dim)' }}>· {firstLine}</span>
        {block.provider && <span style={{ color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>({block.provider})</span>}
      </span>
    )
    return (
      <div className="flex items-center gap-2">
        <div style={{ flex: 1, minWidth: 0 }}>
          <InlineDisclosure summary={summary} tone="danger">
            <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)', margin: 0 }}>
              {block.message}
            </p>
          </InlineDisclosure>
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium transition-colors duration-150 flex-shrink-0"
            style={{
              background: 'rgba(255,77,109,0.1)',
              border: '1px solid rgba(255,77,109,0.3)',
              color: 'var(--danger)',
            }}
            title="Retry last message"
          >
            <RotateCcw size={10} />
            Retry
          </button>
        )}
      </div>
    )
  }

  return null
}
