import { useUiStore } from '@/store/uiStore'
import { REASON_NOT_CONFIGURED } from '@/lib/errors'

// Sections 101/102 — the server's own sentence for a provider/model choice it
// refused, shown under the control it belongs to. When the reason is a missing
// key the fix lives in Profile -> API Keys, so offer the shortcut right there.
export default function ProviderFieldError({ message, reason }: { message: string; reason?: string | null }) {
  const openProfileModal = useUiStore(s => s.openProfileModal)
  return (
    <p style={{ fontSize: 11.5, color: 'var(--danger)', marginTop: 4, lineHeight: 1.45 }}>
      {message}
      {reason === REASON_NOT_CONFIGURED && (
        <>
          {' '}
          <button
            type="button"
            onClick={() => openProfileModal('account')}
            style={{ color: 'var(--eva-primary)', background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: 11.5, textDecoration: 'underline' }}
          >
            Add a key
          </button>
        </>
      )}
    </p>
  )
}
