import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Check, CheckCircle2, KeyRound, Loader2, X } from 'lucide-react'
import { authApi } from '@/api/auth'
import { memoryApi } from '@/api/memory'
import { sessionApi } from '@/api/session'
import { useAuthStore } from '@/store/authStore'
import { useSessionStore } from '@/store/sessionStore'
import { TZ_LIST, PROFILE_INPUT, profileFocus, profileBlur } from './profileConstants'
import LanguageCombobox from './LanguageCombobox'
import ProvidersTabContent, { type ProviderSaveErrors } from './ProvidersTabContent'
import { extractErrorDetail, fallbackIndexFromField, parseProviderError } from '@/lib/errors'
import AccountTabContent from './AccountTabContent'
import ChangePasswordModal from './ChangePasswordModal'

export function UserProfileModal({ onClose, initialTab = 'profile' }: { onClose: () => void; initialTab?: 'profile' | 'providers' | 'account' }) {
  const username    = useAuthStore(s => s.username)
  const queryClient = useQueryClient()

  const [activeTab,    setActiveTab]    = useState<'profile' | 'providers' | 'account'>(initialTab)
  const [pwdModalOpen, setPwdModalOpen] = useState(false)
  const [saveError,    setSaveError]    = useState<string | null>(null)
  const [prefSaved,    setPrefSaved]    = useState(false)
  const [prefWarnings, setPrefWarnings] = useState<string[]>([])
  // Section 101 - which control the server's provider refusal belongs to
  const [providerErrors, setProviderErrors] = useState<ProviderSaveErrors>({})

  // ── Profile form state ──────────────────────────────────────
  const [fullName,      setFullName]      = useState('')
  const [preferredName, setPreferredName] = useState('')
  const [description,   setDescription]  = useState('')

  // ── Locale — mirror sessionStore (shared with SessionPanel) ─
  const sessionConfig    = useSessionStore(s => s.config)
  const setSessionConfig = useSessionStore(s => s.setConfig)

  // ── Fetch profile from GET /auth/me ─────────────────────────
  const { data: profileData, isLoading: profileLoading } = useQuery({
    queryKey: ['me'],
    queryFn:  authApi.getMe,
  })

  useEffect(() => {
    if (!profileData) return
    setFullName(profileData.full_name ?? '')
    setDescription(profileData.description ?? '')
  }, [profileData])

  // ── Seed preferredName from memory (user.name slot) ─────────
  const { data: userFacts } = useQuery({
    queryKey: ['userFacts'],
    queryFn:  memoryApi.getUserFacts,
  })

  useEffect(() => {
    if (!userFacts) return
    const nameFact = userFacts.find(f => f.slot_key === 'user.name')
    if (nameFact) setPreferredName(nameFact.fact)
  }, [userFacts])

  // ── Save mutation ────────────────────────────────────────────
  const { mutate: save, isPending: saving } = useMutation({
    mutationFn: async () => {
      await Promise.all([
        authApi.updateProfile({
          full_name:      fullName      || null,
          preferred_name: preferredName || null,
          description:    description   || null,
        }),
        sessionApi.setSession(sessionConfig),
      ])
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] })
      onClose()
    },
    onError: () => setSaveError('Failed to save profile. Please try again.'),
  })

  // ── Preferences save mutation ────────────────────────────────
  const markClean = useSessionStore(s => s.markClean)
  const { mutate: savePrefs, isPending: prefSaving } = useMutation({
    mutationFn: () => sessionApi.setSession(sessionConfig),
    onSuccess: (data) => {
      // Sync back any fields the backend adjusted (e.g. reasoning level snapped to nearest supported)
      if (data?.reasoning !== sessionConfig.reasoning) setSessionConfig({ reasoning: data?.reasoning ?? null })
      markClean()
      setPrefSaved(true)
      setTimeout(() => setPrefSaved(false), 2000)
      setSaveError(null)
      setProviderErrors({})
      setPrefWarnings(data?.warnings ?? [])
    },
    onError: (err: unknown) => {
      setPrefWarnings([])
      // Section 101 - 422/424 { code: EVA_PROVIDER_NOT_USABLE, field, reason, message }.
      // `field` is provider | model | fallback_chain[i].provider: put the message on that
      // control and keep the footer to a short pointer. A 424 with a plain-string detail
      // (primary has no key) is unchanged: it falls through to its own sentence below.
      const problem = parseProviderError(err)
      if (problem) {
        const idx = fallbackIndexFromField(problem.field)
        setProviderErrors(
          idx !== null                ? { fallback: { [idx]: problem.message } }
          : problem.field === 'model' ? { model: problem.message }
          :                             { provider: { message: problem.message, reason: problem.reason } },
        )
        setSaveError("Couldn't save — see the highlighted provider setting.")
        return
      }
      setProviderErrors({})
      const generic = 'Failed to save preferences. Please try again.'
      const status  = (err as { status?: number } | null)?.status
      // A 4xx carries a sentence meant for the user; a 5xx / dropped connection doesn't.
      setSaveError(status !== undefined && status < 500 ? extractErrorDetail(err, generic) : generic)
    },
  })

  // ── ESC to close (only when password sub-modal isn't open) ──
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape' && !pwdModalOpen) onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose, pwdModalOpen])

  const TABS = [
    { key: 'profile'   as const, label: 'Profile'   },
    { key: 'providers' as const, label: 'Providers' },
    { key: 'account'   as const, label: 'Account'   },
  ]

  // ── shared form-field styles ──
  const fieldLabel: React.CSSProperties = {
    display: 'block', fontSize: 10.5, fontWeight: 700,
    letterSpacing: '0.08em', textTransform: 'uppercase',
    color: 'var(--text-muted)', marginBottom: 6,
  }
  const fieldInput: React.CSSProperties = {
    width: '100%', padding: '10px 13px', boxSizing: 'border-box',
    background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)',
    borderRadius: 9, color: 'var(--text-primary)', fontSize: 13.5,
    outline: 'none', transition: 'border-color 150ms ease',
  }
  const fieldHint: React.CSSProperties = {
    fontSize: 12, color: 'var(--text-muted)', marginTop: 5, lineHeight: 1.4,
  }
  const sectionDivider: React.CSSProperties = {
    margin: '26px 0 22px',
    border: 'none', borderTop: '1px solid var(--border-dim)',
  }
  const sectionTitle: React.CSSProperties = {
    fontSize: 10, fontWeight: 700, letterSpacing: '0.1em',
    textTransform: 'uppercase', color: 'var(--text-faint)',
    marginBottom: 16,
  }
  const onFocusI = (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    (e.currentTarget.style.borderColor = 'rgba(0,201,255,0.4)')
  const onBlurI  = (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    (e.currentTarget.style.borderColor = 'var(--border-dim)')

  return (
    /* ── Backdrop ── */
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 2000,
        background: 'rgba(6,9,15,0.65)',
        backdropFilter: 'blur(3px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      {/* ── Modal card ── */}
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '75vw', height: '75vh',
          maxWidth: 980, maxHeight: 720,
          minWidth: 640, minHeight: 520,
          background: 'var(--bg-surface1)',
          border: '1px solid var(--border-mid)',
          borderRadius: 18,
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 32px 96px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.04)',
        }}
      >
        {/* ── Header ── */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 14,
          padding: '22px 28px 18px',
          borderBottom: '1px solid var(--border-dim)',
          flexShrink: 0,
        }}>
          <div style={{
            width: 46, height: 46, borderRadius: 999, flexShrink: 0,
            background: 'var(--eva-primary)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 19, fontWeight: 800, color: '#06090F',
          }}>
            {(username?.[0] ?? '?').toUpperCase()}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.2 }}>
              {username ?? 'User'}
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono, monospace', marginTop: 3 }}>
              {username ?? 'user'}@local · ready
            </div>
          </div>
          <button className="icon-btn" style={{ width: 32, height: 32 }} onClick={onClose} title="Close">
            <X size={16} />
          </button>
        </div>

        {/* ── Tab bar ── */}
        <div style={{
          display: 'flex', alignItems: 'flex-end',
          padding: '0 28px',
          borderBottom: '1px solid var(--border-dim)',
          flexShrink: 0,
        }}>
          {TABS.map(t => {
            const active = activeTab === t.key
            return (
              <button
                key={t.key}
                onClick={() => setActiveTab(t.key)}
                style={{
                  padding: '13px 18px 12px',
                  fontSize: 13.5, fontWeight: active ? 600 : 400,
                  color: active ? 'var(--text-primary)' : 'var(--text-muted)',
                  background: 'none', border: 'none', cursor: 'pointer',
                  borderBottom: active ? '2px solid var(--eva-primary)' : '2px solid transparent',
                  marginBottom: -1,
                  transition: 'color 150ms ease, border-color 150ms ease',
                }}
              >
                {t.label}
              </button>
            )
          })}
        </div>

        {/* ── Tab content ── */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '28px 32px' }}>

          {/* ── Profile tab ── */}
          {activeTab === 'profile' && (
            <div>
              <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 5px' }}>
                Your profile
              </h3>
              <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: '0 0 28px', lineHeight: 1.55 }}>
                Basic details EVA will use to personalise responses, schedule daemons, and format timestamps.
              </p>

              {/* ── Identity section ── */}
              <p style={sectionTitle}>Identity</p>

              {/* FULL NAME + PREFERRED NAME — two columns */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18, marginBottom: 18 }}>
                <div>
                  <label style={fieldLabel}>Full Name</label>
                  <input
                    type="text"
                    value={fullName}
                    onChange={e => setFullName(e.target.value)}
                    placeholder="e.g. Alex Morgan"
                    style={fieldInput}
                    onFocus={onFocusI} onBlur={onBlurI}
                  />
                  <p style={fieldHint}>Your formal name — shown in profile and reports.</p>
                </div>
                <div>
                  <label style={fieldLabel}>Preferred Name</label>
                  <input
                    type="text"
                    value={preferredName}
                    onChange={e => setPreferredName(e.target.value)}
                    placeholder="e.g. Alex"
                    style={fieldInput}
                    onFocus={onFocusI} onBlur={onBlurI}
                  />
                  <p style={fieldHint}>The name EVA uses in conversation. Leave blank to use first name from Full Name.</p>
                </div>
              </div>

              {/* PASSWORD — full width */}
              <div style={{ marginBottom: 4 }}>
                <label style={fieldLabel}>Password</label>
                <div style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '10px 13px',
                  background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)',
                  borderRadius: 9,
                }}>
                  <span style={{ fontSize: 13, color: 'var(--text-muted)', letterSpacing: '0.18em' }}>
                    ••••••••••••
                  </span>
                  <button
                    className="btn ghost"
                    style={{ fontSize: 11.5, padding: '4px 12px', flexShrink: 0 }}
                    onClick={() => setPwdModalOpen(true)}
                  >
                    <KeyRound size={11} /> Update Password
                  </button>
                </div>
              </div>

              <hr style={sectionDivider} />

              {/* ── About section ── */}
              <p style={sectionTitle}>About</p>

              {/* DESCRIPTION */}
              <div style={{ marginBottom: 4 }}>
                <label style={fieldLabel}>Description</label>
                <textarea
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  placeholder="Add any information about yourself — your role, expertise, working hours, preferences, or anything else you'd like EVA to know about you."
                  rows={4}
                  style={{
                    ...fieldInput,
                    resize: 'vertical',
                    minHeight: 90,
                    lineHeight: 1.55,
                    fontFamily: 'inherit',
                  } as React.CSSProperties}
                  onFocus={onFocusI} onBlur={onBlurI}
                />
              </div>

              <hr style={sectionDivider} />

              {/* ── Locale section ── */}
              <p style={sectionTitle}>Locale</p>

              {/* LANGUAGE + LOCATION — two columns */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18, marginBottom: 18 }}>
                <div>
                  <label style={fieldLabel}>Language</label>
                  <LanguageCombobox
                    value={sessionConfig.user_language ?? null}
                    onChange={v => setSessionConfig({ user_language: v })}
                  />
                  <p style={fieldHint}>EVA formats dates, units, and responses in this language.</p>
                </div>
                <div>
                  <label style={fieldLabel}>Location</label>
                  {/* Plain text on purpose: no third-party geocoding lookup, so nothing a user types here leaves the device. */}
                  <input
                    type="text"
                    value={sessionConfig.user_location ?? ''}
                    onChange={e => setSessionConfig({ user_location: e.target.value || null })}
                    onFocus={profileFocus}
                    onBlur={profileBlur}
                    placeholder="e.g. Berlin, Germany"
                    style={PROFILE_INPUT}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <p style={fieldHint}>Used for context-aware answers (weather, local time, etc.). Include city and country for the most accurate results.</p>
                </div>
              </div>

              {/* TIME ZONE — full width */}
              <div style={{ marginBottom: 8 }}>
                <label style={fieldLabel}>Time Zone</label>
                <div style={{ position: 'relative' }}>
                  <select
                    value={sessionConfig.user_timezone ?? (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone } catch { return 'UTC' } })()}
                    onChange={e => setSessionConfig({ user_timezone: e.target.value || null })}
                    style={{ ...fieldInput, appearance: 'none', paddingRight: 36, cursor: 'pointer' } as React.CSSProperties}
                    onFocus={onFocusI} onBlur={onBlurI}
                  >
                    {TZ_LIST.map(tz => (
                      <option key={tz} value={tz}>{tz.replace(/_/g, ' ')}</option>
                    ))}
                  </select>
                  <ChevronDown size={13} style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
                </div>
                <p style={fieldHint}>Determines how daemon schedules and timestamps are displayed.</p>
              </div>
            </div>
          )}

          {/* ── Providers tab ── */}
          {activeTab === 'providers' && <ProvidersTabContent errors={providerErrors} onEdit={() => { setProviderErrors({}); setSaveError(null) }} />}

          {/* ── Account tab ── */}
          {activeTab === 'account' && (
            <AccountTabContent
              fieldLabel={fieldLabel}
              fieldInput={fieldInput}
              fieldHint={fieldHint}
              sectionDivider={sectionDivider}
              sectionTitle={sectionTitle}
              onFocus={onFocusI}
              onBlur={onBlurI}
            />
          )}
        </div>

        {/* ── Footer ── */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '14px 28px',
          borderTop: '1px solid var(--border-dim)',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button className="btn ghost" onClick={onClose} disabled={saving}>Cancel</button>
            {saveError && (
              <span style={{ fontSize: 12, color: 'var(--danger)' }}>{saveError}</span>
            )}
            {prefWarnings.length > 0 && !saveError && (
              <span style={{ fontSize: 12, color: 'var(--amber)' }}>
                {prefWarnings[0]}{prefWarnings.length > 1 ? ` (+${prefWarnings.length - 1} more)` : ''}
              </span>
            )}
          </div>
          <button
            className="btn primary"
            disabled={saving || prefSaving || profileLoading}
            onClick={
              activeTab === 'profile'   ? () => save()      :
              activeTab === 'providers' ? () => savePrefs() :
              onClose
            }
          >
            {(saving || prefSaving)
              ? <Loader2 size={13} className="animate-spin" />
              : prefSaved
                ? <CheckCircle2 size={13} style={{ color: 'var(--success)' }} />
                : <Check size={13} />
            }
            {activeTab === 'account'
              ? 'Close'
              : (saving || prefSaving)
                ? 'Saving…'
                : prefSaved
                  ? 'Saved!'
                  : 'Save changes'
            }
          </button>
        </div>
      </div>

      {/* ── Change Password sub-modal ── */}
      {pwdModalOpen && <ChangePasswordModal onClose={() => setPwdModalOpen(false)} />}
    </div>
  )
}
