import { AlertTriangle, X } from 'lucide-react'
import { useChatStore } from '@/store/chatStore'

export function WorkerDownBanner() {
  const setWorkerDown = useChatStore(s => s.setWorkerDown)

  return (
    <div className="worker-banner" style={{ position: 'relative', zIndex: 40 }}>
      <AlertTriangle size={14} style={{ flexShrink: 0 }} />
      <span style={{ flex: 1, fontSize: 12 }}>
        EVA's backend is unreachable. Please check that the backend service is running.
      </span>
      <button
        onClick={() => setWorkerDown(false)}
        title="Dismiss"
      >
        <X size={14} />
      </button>
    </div>
  )
}
