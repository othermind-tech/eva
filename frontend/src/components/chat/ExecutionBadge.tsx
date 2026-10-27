import { Loader2 } from 'lucide-react'

export function ExecutionBadge() {
  return (
    <div
      className="flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium"
      style={{
        background: 'var(--eva-primary-dim)',
        border: '1px solid var(--border-mid)',
        color: 'var(--eva-primary)',
      }}
    >
      <Loader2 size={10} className="animate-spin" />
      Thinking
    </div>
  )
}
