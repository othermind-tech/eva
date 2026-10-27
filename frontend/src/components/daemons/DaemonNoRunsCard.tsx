import { Clock } from 'lucide-react'

export default function DaemonNoRunsCard({ name }: { name: string }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[50vh] gap-5 px-6 text-center">
      <div
        className="w-14 h-14 rounded-2xl flex items-center justify-center"
        style={{ background: 'rgba(0,201,255,0.08)', border: '1px solid rgba(0,201,255,0.2)' }}
      >
        <Clock size={26} style={{ color: 'var(--eva-primary)' }} />
      </div>
      <div>
        <p className="font-mono font-semibold text-base" style={{ color: 'var(--text-primary)' }}>{name}</p>
        <p className="text-xs mt-1.5" style={{ color: 'var(--text-muted)' }}>
          No runs recorded yet.
        </p>
      </div>
      <p className="text-xs max-w-xs" style={{ color: 'var(--text-muted)', lineHeight: 1.6 }}>
        Trigger this daemon using the button below, or wait for the next scheduled run.
        Results will appear here automatically.
      </p>
    </div>
  )
}
