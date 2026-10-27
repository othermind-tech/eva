import { useState, useEffect } from 'react'
import { Lock, Loader2, CheckCircle2 } from 'lucide-react'
import { memoryApi } from '@/api/memory'

// ── Types ─────────────────────────────────────────────────────────────────────
interface Props {
  entryId:  string
  scopeKey: string
}

type CardState = 'checking' | 'idle' | 'granted' | 'dismissed'

// ── Component ─────────────────────────────────────────────────────────────────
// Rendered inline wherever [SENSITIVE: entry_id=... scope_key=...] appears
// in an assistant message. Replaces the raw placeholder text with an
// actionable card.
export default function SensitiveMemoryCard({ entryId, scopeKey }: Props) {
  const [state,   setState]   = useState<CardState>('checking')
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  // Check whether a grant is already active (e.g. user granted it earlier this hour)
  useEffect(() => {
    memoryApi.checkGrant(scopeKey, entryId)
      .then(res => setState(res.granted ? 'granted' : 'idle'))
      .catch(() => setState('idle'))
  }, [scopeKey, entryId])

  const handleGrant = async () => {
    setLoading(true)
    setError(null)
    try {
      await memoryApi.grantAccess(scopeKey, entryId)
      setState('granted')
    } catch {
      setError('Failed to grant access. The memory entry may no longer exist.')
    } finally {
      setLoading(false)
    }
  }

  const handleRevoke = async () => {
    setLoading(true)
    try {
      await memoryApi.revokeAccess(scopeKey, entryId)
      setState('idle')
    } catch {
      // Grant may have already expired — reset to idle anyway
      setState('idle')
    } finally {
      setLoading(false)
    }
  }

  // ── Dismissed or still checking: render nothing ──────────────────────────
  if (state === 'dismissed' || state === 'checking') return null

  // ── Granted state ─────────────────────────────────────────────────────────
  if (state === 'granted') {
    return (
      <div style={{
        display:      'flex',
        alignItems:   'center',
        gap:          10,
        background:   'rgba(0,212,160,0.08)',
        border:       '1px solid rgba(0,212,160,0.2)',
        borderRadius: 10,
        padding:      '10px 14px',
        marginTop:    2,
        marginBottom: 2,
      }}>
        <CheckCircle2 size={14} style={{ color: 'var(--success)', flexShrink: 0 }} />
        <span style={{ fontSize: 13, color: 'var(--success)', flex: 1 }}>
          Access granted for 1 hour. Send another message to retry.
        </span>
        <button
          onClick={handleRevoke}
          disabled={loading}
          style={{
            fontSize:   11,
            color:      'var(--text-muted)',
            background: 'none',
            border:     'none',
            cursor:     loading ? 'default' : 'pointer',
            padding:    '2px 8px',
            borderRadius: 6,
            display:    'flex',
            alignItems: 'center',
            gap:        4,
            opacity:    loading ? 0.6 : 1,
          }}
          title="Revoke access immediately"
        >
          {loading
            ? <Loader2 size={10} className="animate-spin" />
            : 'Revoke'}
        </button>
      </div>
    )
  }

  // ── Idle state (default) ──────────────────────────────────────────────────
  return (
    <div style={{
      background:   'rgba(245,158,11,0.06)',
      border:       '1px solid rgba(245,158,11,0.25)',
      borderRadius: 12,
      padding:      '14px 16px',
      marginTop:    4,
      marginBottom: 4,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <Lock size={14} style={{ color: 'var(--amber)', flexShrink: 0 }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--amber)' }}>
          Sensitive Memory — Access Restricted
        </span>
      </div>

      {/* Description */}
      <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6, marginBottom: 12 }}>
        The agent tried to read a sensitive memory entry but doesn't have access yet.
        Granting access allows the agent to use this entry for the next hour.
      </p>

      {/* Inline error */}
      {error && (
        <p style={{ fontSize: 11, color: 'var(--danger)', marginBottom: 10, lineHeight: 1.5 }}>
          {error}
        </p>
      )}

      {/* Action buttons */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button
          onClick={handleGrant}
          disabled={loading}
          className="btn primary"
          style={{ fontSize: 12, padding: '6px 14px', display: 'flex', alignItems: 'center', gap: 6 }}
        >
          {loading && <Loader2 size={11} className="animate-spin" />}
          Allow Access for 1 hour
        </button>
        <button
          onClick={() => setState('dismissed')}
          className="btn ghost"
          style={{ fontSize: 12, padding: '6px 12px' }}
        >
          Dismiss
        </button>
      </div>

      {/* Entry reference (JetBrains Mono — mirrors Memory panel style) */}
      <p style={{
        fontSize:    10,
        color:       'var(--text-dim)',
        marginTop:   10,
        fontFamily:  "'JetBrains Mono', monospace",
        lineHeight:  1.5,
      }}>
        entry: {entryId} · scope: {scopeKey}
      </p>
    </div>
  )
}
