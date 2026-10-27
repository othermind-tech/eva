import { ChevronDown } from 'lucide-react'
import { useAuthStore } from '@/store/authStore'
import { useUiStore } from '@/store/uiStore'

// ─── User Pill ────────────────────────────────────────────────────────────────
export default function UserPill() {
  const username         = useAuthStore(s => s.username)
  const openProfileModal = useUiStore(s => s.openProfileModal)

  return (
    <div className="user-pill" onClick={() => openProfileModal('profile')} style={{ cursor: 'pointer' }}>
      <div className="up-avatar">
        {(username?.[0] ?? '?').toUpperCase()}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="up-name">{username ?? 'User'}</div>
        <div className="up-tier mono">{username ?? 'user'}@local · ready</div>
      </div>
      <ChevronDown size={14} className="up-chev" />
    </div>
  )
}
