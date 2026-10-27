import { Bot, Cpu } from 'lucide-react'
import type { ViewTarget } from '@/store/chatStore'

export default function AgentProfileCard({ target }: { target: ViewTarget }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[50vh] gap-5 px-6 text-center">
      <div
        className="w-14 h-14 rounded-2xl flex items-center justify-center"
        style={{ background: 'rgba(124,108,243,0.1)', border: '1px solid rgba(124,108,243,0.25)' }}
      >
        <Bot size={26} style={{ color: 'var(--neural)' }} />
      </div>

      <div>
        <p className="font-mono font-semibold text-base" style={{ color: 'var(--text-primary)' }}>{target.name}</p>
        {(target.provider || target.model) && (
          <div className="flex items-center justify-center gap-1.5 mt-1.5">
            <Cpu size={10} style={{ color: 'var(--eva-primary)' }} />
            <span className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>
              {[target.provider, target.model].filter(Boolean).join(' / ')}
            </span>
          </div>
        )}
      </div>

      {target.systemPrompt && (
        <div
          className="rounded-xl px-4 py-3 text-left max-w-sm w-full"
          style={{ background: 'rgba(124,108,243,0.06)', border: '1px solid rgba(124,108,243,0.15)' }}
        >
          <p className="text-xs font-semibold mb-1.5" style={{ color: 'var(--neural)' }}>System Prompt</p>
          <p className="text-xs leading-relaxed whitespace-pre-wrap line-clamp-5" style={{ color: 'var(--text-muted)' }}>
            {target.systemPrompt}
          </p>
        </div>
      )}

      <p className="text-xs max-w-xs" style={{ color: 'var(--text-muted)', lineHeight: 1.6 }}>
        Type a message below to start a conversation.
        This agent's configuration will be pre-loaded as your system prompt.
      </p>
    </div>
  )
}
