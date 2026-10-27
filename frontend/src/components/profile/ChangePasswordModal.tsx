import { useState, useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Check, Eye, EyeOff, KeyRound, Loader2, X } from 'lucide-react'
import { authApi } from '@/api/auth'

const fieldInput: React.CSSProperties = {
  flex: 1, padding: '10px 13px',
  background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)',
  borderRadius: 9, color: 'var(--text-primary)', fontSize: 13.5,
  outline: 'none', transition: 'border-color 150ms ease',
  fontFamily: 'inherit',
}
const fieldLabel: React.CSSProperties = {
  display: 'block', fontSize: 10.5, fontWeight: 700,
  letterSpacing: '0.08em', textTransform: 'uppercase',
  color: 'var(--text-muted)', marginBottom: 6,
}

// Must stay at module level: declared inside ChangePasswordModal it is a new component type on every
// render, so React remounts each input per keystroke and focus drops after one character.
const PwdField = ({
  label, value, onChange, show, onToggle, placeholder,
  error,
}: {
  label: string; value: string; onChange: (v: string) => void
  show: boolean; onToggle: () => void; placeholder?: string
  error?: string
}) => (
  <div style={{ marginBottom: 18 }}>
    <label style={fieldLabel}>{label}</label>
    <div style={{ display: 'flex', gap: 0, position: 'relative' }}>
      <input
        type={show ? 'text' : 'password'}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder ?? '••••••••'}
        style={{
          ...fieldInput,
          paddingRight: 40,
          borderColor: error ? 'var(--danger)' : undefined,
        }}
        onFocus={e => (e.currentTarget.style.borderColor = error ? 'var(--danger)' : 'rgba(0,201,255,0.4)')}
        onBlur={e  => (e.currentTarget.style.borderColor = error ? 'var(--danger)' : 'var(--border-dim)')}
      />
      <button
        type="button"
        onClick={onToggle}
        style={{
          position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-muted)', padding: 2, display: 'flex', alignItems: 'center',
        }}
      >
        {show ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
    {error && <p style={{ fontSize: 11.5, color: 'var(--danger)', marginTop: 5 }}>{error}</p>}
  </div>
)

// ─── Change Password Sub-modal ────────────────────────────────────────────────
export default function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const [oldPwd,       setOldPwd]       = useState('')
  const [newPwd,       setNewPwd]       = useState('')
  const [confirmPwd,   setConfirmPwd]   = useState('')
  const [showOld,      setShowOld]      = useState(false)
  const [showNew,      setShowNew]      = useState(false)
  const [showConf,     setShowConf]     = useState(false)
  const [currentError, setCurrentError] = useState<string | null>(null)
  const [newPwdError,  setNewPwdError]  = useState<string | null>(null)

  // ESC closes sub-modal only
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const mismatch    = confirmPwd.length > 0 && newPwd !== confirmPwd
  const tooShort    = newPwd.length > 0 && newPwd.length < 8

  const { mutate: changePwd, isPending: changing } = useMutation({
    mutationFn: () => authApi.changePassword({
      current_password: oldPwd,
      new_password:     newPwd,
    }),
    onSuccess: () => onClose(),
    onError: (err: unknown) => {
      // client.ts flattens errors to { status, detail } - there is no `.response`
      const status = (err as { status?: number } | null)?.status
      if (status === 400) {
        setCurrentError('Current password is incorrect.')
      } else if (status === 422) {
        setNewPwdError('New password must be at least 8 characters.')
      } else {
        setCurrentError('Something went wrong. Please try again.')
      }
    },
  })

  return (
    /* Sub-modal backdrop — slightly lighter so the parent modal is still visible */
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 3000,
        background: 'rgba(6,9,15,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 440,
          background: 'var(--bg-surface1)',
          border: '1px solid var(--border-mid)',
          borderRadius: 16,
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 24px 64px rgba(0,0,0,0.8)',
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
              width: 32, height: 32, borderRadius: 9,
              background: 'rgba(0,201,255,0.08)', border: '1px solid rgba(0,201,255,0.2)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <KeyRound size={15} style={{ color: 'var(--eva-primary)' }} />
            </div>
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>Update Password</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 1 }}>Choose a strong, unique password</div>
            </div>
          </div>
          <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={onClose}>
            <X size={14} />
          </button>
        </div>

        {/* Fields */}
        <div style={{ padding: '20px 20px 4px' }}>
          <PwdField
            label="Current Password"
            value={oldPwd}
            onChange={v => { setOldPwd(v); setCurrentError(null) }}
            show={showOld} onToggle={() => setShowOld(v => !v)}
            placeholder="Enter your current password"
            error={currentError ?? undefined}
          />
          <PwdField
            label="New Password"
            value={newPwd}
            onChange={v => { setNewPwd(v); setNewPwdError(null) }}
            show={showNew} onToggle={() => setShowNew(v => !v)}
            placeholder="Choose a new password (min 8 characters)"
            error={tooShort ? 'Must be at least 8 characters' : (newPwdError ?? undefined)}
          />
          <PwdField
            label="Confirm New Password"
            value={confirmPwd} onChange={setConfirmPwd}
            show={showConf} onToggle={() => setShowConf(v => !v)}
            placeholder="Re-enter new password"
            error={mismatch ? 'Passwords do not match' : undefined}
          />
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '12px 20px 16px',
        }}>
          <button className="btn ghost" onClick={onClose} disabled={changing}>Cancel</button>
          <button
            className="btn primary"
            disabled={!oldPwd || !newPwd || !confirmPwd || mismatch || tooShort || changing}
            onClick={() => changePwd()}
          >
            {changing
              ? <><Loader2 size={13} className="animate-spin" /> Updating…</>
              : <><Check size={13} /> Update Password</>
            }
          </button>
        </div>
      </div>
    </div>
  )
}
