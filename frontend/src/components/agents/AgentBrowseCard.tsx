import { Bot } from 'lucide-react'

// ── Agent browse idle state ──────────────────────────────────────────────────
export default function AgentBrowseCard() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 px-6 select-none">
      <div
        className="w-16 h-16 rounded-2xl flex items-center justify-center mb-2"
        style={{
          background: 'rgba(124,108,243,0.1)',
          border: '1px solid rgba(124,108,243,0.25)',
          boxShadow: '0 0 32px rgba(124,108,243,0.12)',
        }}
      >
        <Bot size={30} style={{ color: 'var(--neural)' }} />
      </div>
      <div className="text-center">
        <p
          className="font-semibold tracking-tight"
          style={{ fontSize: '1.75rem', color: 'var(--text-primary)', lineHeight: 1.15 }}
        >
          Agent Mode
        </p>
        <p
          className="mt-2"
          style={{ fontSize: '0.9rem', color: 'var(--text-muted)', letterSpacing: '0.01em' }}
        >
          Create or select an agent
        </p>
      </div>
      <p
        className="text-center max-w-xs"
        style={{ fontSize: '0.75rem', color: 'var(--text-dim)', lineHeight: 1.65, marginTop: '0.5rem' }}
      >
        Agents run autonomously with their own model, system prompt, and tool access.
        Click <strong style={{ color: 'var(--neural)' }}>Monitor</strong> on any agent to view its last execution here.
      </p>
    </div>
  )
}
