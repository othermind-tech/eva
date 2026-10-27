import { AlertTriangle, X } from 'lucide-react'
import { useUiStore } from '@/store/uiStore'

/**
 * Shown whenever a user tries to send/trigger/enhance without a configured
 * provider (Section 82 / V6.22 — EVA no longer has a hardcoded default
 * provider). Shared by MessageInput (conversation/agent/daemon sends) and
 * SystemPromptField (enhance) via uiStore's providerRequiredOpen flag.
 */
export default function ProviderRequiredModal() {
  const open                  = useUiStore(s => s.providerRequiredOpen)
  const closeProviderRequired = useUiStore(s => s.closeProviderRequired)
  const openProfileModal      = useUiStore(s => s.openProfileModal)

  if (!open) return null

  const handleConfigure = () => {
    closeProviderRequired()
    openProfileModal('providers')
  }

  return (
    <div
      onClick={closeProviderRequired}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.55)',
        zIndex: 200,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 400,
          background: 'var(--bg-surface2)',
          border: '1px solid var(--border-mid)',
          borderRadius: 14,
          boxShadow: '0 24px 64px rgba(0,0,0,0.6)',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '18px 20px 14px',
          borderBottom: '1px solid var(--border-dim)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              width: 30, height: 30, borderRadius: 8,
              background: 'rgba(245,158,11,0.12)',
              border: '1px solid rgba(245,158,11,0.3)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--amber)', flexShrink: 0,
            }}>
              <AlertTriangle size={15} />
            </div>
            <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
              Provider Required
            </p>
          </div>
          <button
            onClick={closeProviderRequired}
            style={{
              width: 28, height: 28,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              borderRadius: 6,
              border: '1px solid var(--border-dim)',
              background: 'var(--bg-surface1)',
              color: 'var(--text-muted)',
              cursor: 'pointer',
            }}
          >
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '20px 24px' }}>
          <p style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-secondary)', margin: 0 }}>
            Please configure at least one provider to proceed.
          </p>
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 10,
          padding: '14px 20px',
          borderTop: '1px solid var(--border-dim)',
        }}>
          <button
            onClick={closeProviderRequired}
            style={{
              padding: '7px 14px', borderRadius: 7,
              border: '1px solid var(--border-mid)',
              background: 'transparent',
              color: 'var(--text-secondary)',
              fontSize: 13, cursor: 'pointer',
            }}
          >
            Close
          </button>
          <button
            onClick={handleConfigure}
            style={{
              padding: '7px 18px', borderRadius: 7,
              border: 'none',
              background: 'var(--eva-primary)',
              color: '#04080E',
              fontSize: 13, fontWeight: 600, cursor: 'pointer',
            }}
          >
            Configure
          </button>
        </div>
      </div>
    </div>
  )
}
