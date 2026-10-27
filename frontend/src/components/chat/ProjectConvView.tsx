import { useEffect, useRef, useState } from 'react'
import { ChevronRight, MessageSquare } from 'lucide-react'
import { useChatStore } from '@/store/chatStore'
import MessageList from './MessageList'
import MessageInput from './MessageInput'

export default function ProjectConvView({ onBack }: { onBack: () => void }) {
  const messages         = useChatStore(s => s.messages)
  const isStreaming      = useChatStore(s => s.isStreaming)
  const projectConvTitle = useChatStore(s => s.projectConvTitle)
  const messagesLoadError = useChatStore(s => s.messagesLoadError)
  const [draft, setDraft] = useState('')
  const scrollRef         = useRef<HTMLDivElement>(null)
  const bottomRef         = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isStreaming])

  return (
    <div className="detail">
      {/* ── Header — same pattern as DaemonEditView / AgentEditView ── */}
      <div className="detail-header">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-xs shrink-0"
          style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '2px 6px 2px 0', marginRight: 4 }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--eva-primary)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
        >
          <ChevronRight size={11} style={{ transform: 'rotate(180deg)' }} /> Conversations
        </button>
        <div className="dh-icon" style={{ background: 'rgba(0,201,255,0.08)', borderColor: 'rgba(0,201,255,0.2)', color: 'var(--eva-primary)' }}>
          <MessageSquare size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">{projectConvTitle || 'Untitled conversation'}</h2>
          <p className="dh-sub">Project conversation</p>
        </div>
      </div>

      {/* ── Messages + input — fills remaining height ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
        <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto' }}>
          {messages.length === 0 && messagesLoadError ? (
            <p className="text-xs text-center py-8" style={{ color: 'var(--danger)' }}>
              {messagesLoadError}
            </p>
          ) : messages.length === 0 ? (
            <p className="text-xs text-center py-8" style={{ color: 'var(--text-muted)' }}>
              No messages in this conversation.
            </p>
          ) : (
            <MessageList messages={messages} />
          )}
          <div ref={bottomRef} />
        </div>
        <div className="shrink-0 px-3 pb-3 pt-2" style={{ borderTop: '1px solid var(--border-dim)' }}>
          <MessageInput value={draft} onChange={setDraft} />
        </div>
      </div>
    </div>
  )
}
