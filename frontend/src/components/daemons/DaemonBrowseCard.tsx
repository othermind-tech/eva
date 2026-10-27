import { Clock } from 'lucide-react'

export default function DaemonBrowseCard() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 px-6 select-none">
      <div
        className="w-16 h-16 rounded-2xl flex items-center justify-center mb-2"
        style={{
          background: 'rgba(0,201,255,0.08)',
          border: '1px solid rgba(0,201,255,0.2)',
          boxShadow: '0 0 32px rgba(0,201,255,0.1)',
        }}
      >
        <Clock size={30} style={{ color: 'var(--eva-primary)' }} />
      </div>
      <div className="text-center">
        <p
          className="font-semibold tracking-tight"
          style={{ fontSize: '1.75rem', color: 'var(--text-primary)', lineHeight: 1.15 }}
        >
          Daemon Mode
        </p>
        <p
          className="mt-2"
          style={{ fontSize: '0.9rem', color: 'var(--text-muted)', letterSpacing: '0.01em' }}
        >
          Create or select a daemon
        </p>
      </div>
      <p
        className="text-center max-w-xs"
        style={{ fontSize: '0.75rem', color: 'var(--text-dim)', lineHeight: 1.65, marginTop: '0.5rem' }}
      >
        Daemons run on a schedule or via webhook — autonomously, without user input.
        Click <strong style={{ color: 'var(--eva-primary)' }}>Monitor</strong> on any daemon to view its last execution here.
      </p>
    </div>
  )
}
