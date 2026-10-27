import { useState, useRef, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown, Eye, EyeOff, Key, CheckCircle2, AlertTriangle, Info, XCircle, Server, Loader2, Trash2,
} from 'lucide-react'
import { providersApi } from '@/api/tools'
import { secretsApi } from '@/api/secrets'
import { extractErrorDetail } from '@/lib/errors'
import type { SecretDeleteResponse } from '@/types/api'

export default function AccountTabContent({
  fieldLabel, fieldInput, fieldHint, sectionDivider, sectionTitle, onFocus, onBlur,
}: {
  fieldLabel:     React.CSSProperties
  fieldInput:     React.CSSProperties
  fieldHint:      React.CSSProperties
  sectionDivider: React.CSSProperties
  sectionTitle:   React.CSSProperties
  onFocus: (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => void
  onBlur:  (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => void
}) {
  // ── API Keys state ────────────────────────────────────────────
  const [keyProviders,    setKeyProviders]    = useState<{ id: string; label: string; configured: boolean }[]>([])
  const [providersLoaded, setProvidersLoaded] = useState(false)
  const [selectedId,      setSelectedId]      = useState('')
  const [providerOpen,    setProviderOpen]    = useState(false)
  const providerDropRef = useRef<HTMLDivElement>(null)
  const [keyValue,        setKeyValue]        = useState('')
  const [showKey,         setShowKey]         = useState(false)
  const [keySaving,       setKeySaving]       = useState(false)
  const [keyResult,       setKeyResult]       = useState<{ valid: boolean | null; message: string } | null>(null)
  const [keyError,        setKeyError]        = useState<string | null>(null)

  // Section 99 - remove a stored key. The OS credential store is shared by every EVA
  // account on this machine, so the confirm step says so explicitly.
  const queryClient = useQueryClient()
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [removing,      setRemoving]      = useState(false)
  const [removeResult,  setRemoveResult]  = useState<SecretDeleteResponse | null>(null)
  const [removeError,   setRemoveError]   = useState<string | null>(null)

  // Re-read the list after a save/remove so the green dot is truthful, and invalidate
  // the shared ['providers'] cache so every selector re-evaluates chat_ready.
  const loadProviders = (keepSelection: boolean) =>
    providersApi.list()
      .then(list => {
        const cloud = list
          .filter(p => !p.is_local)
          .map(p => ({ id: p.provider, label: p.display_name, configured: p.is_configured }))
        setKeyProviders(cloud)
        if (!keepSelection && cloud.length > 0) setSelectedId(cloud[0].id)
        void queryClient.invalidateQueries({ queryKey: ['providers'] })
      })

  useEffect(() => {
    loadProviders(false)
      .catch(() => {})
      .finally(() => setProvidersLoaded(true))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!providerOpen) return
    const handler = (e: MouseEvent) => {
      if (providerDropRef.current && !providerDropRef.current.contains(e.target as Node))
        setProviderOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [providerOpen])

  const handleProviderChange = (id: string) => {
    setSelectedId(id); setKeyValue(''); setKeyResult(null); setKeyError(null); setShowKey(false)
    setConfirmRemove(false); setRemoveResult(null); setRemoveError(null)
  }

  const handleSaveKey = async () => {
    if (!keyValue.trim()) return
    setKeySaving(true); setKeyResult(null); setKeyError(null)
    try {
      const res = await secretsApi.setProviderKey(selectedId, keyValue.trim())
      setKeyResult({ valid: res.valid, message: res.message })
      setKeyValue('')
      setRemoveResult(null); setRemoveError(null)
      void loadProviders(true).catch(() => {})
    } catch (err: unknown) {
      setKeyError((err as { detail?: string })?.detail ?? 'Failed to save key.')
    } finally { setKeySaving(false) }
  }

  const handleRemoveKey = async () => {
    setRemoving(true); setRemoveError(null); setRemoveResult(null)
    try {
      const res = await secretsApi.deleteProviderKey(selectedId)
      setRemoveResult(res)
      setConfirmRemove(false)
      setKeyResult(null)
      await loadProviders(true)
    } catch (err: unknown) {
      // 400 (unknown provider), 429 (rate limit), 503 (credential store unreachable -
      // the key was NOT removed). The server's own sentence says which.
      setRemoveError(extractErrorDetail(err, 'Could not remove the key.'))
      setConfirmRemove(false)
    } finally { setRemoving(false) }
  }

  const selectedLabel = keyProviders.find(p => p.id === selectedId)?.label ?? selectedId
  const selectedConfigured = keyProviders.find(p => p.id === selectedId)?.configured ?? false
  const canSaveKey    = !!keyValue.trim() && !keySaving && !!selectedId

  // ── Ollama state ──────────────────────────────────────────────
  const [baseUrl,       setBaseUrl]       = useState('')
  const [reachable,     setReachable]     = useState<boolean | null>(null)
  const [ollamaLoading, setOllamaLoading] = useState(true)
  const [ollamaSaving,  setOllamaSaving]  = useState(false)
  const [ollamaError,   setOllamaError]   = useState<string | null>(null)

  useEffect(() => {
    secretsApi.getOllamaBaseUrl()
      .then(r => { setBaseUrl(r.base_url); setReachable(r.reachable) })
      .catch(() => setOllamaError('Failed to load Ollama settings.'))
      .finally(() => setOllamaLoading(false))
  }, [])

  const handleSaveOllama = async () => {
    if (!baseUrl.trim()) return
    setOllamaSaving(true); setOllamaError(null)
    try {
      const r = await secretsApi.setOllamaBaseUrl(baseUrl.trim())
      setBaseUrl(r.base_url); setReachable(r.reachable)
    } catch (err: unknown) {
      setOllamaError((err as { detail?: string })?.detail ?? 'Failed to save.')
    } finally { setOllamaSaving(false) }
  }

  return (
    <div>
      <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 5px' }}>
        API Keys
      </h3>
      <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: '0 0 28px', lineHeight: 1.55 }}>
        Configure provider keys and local model endpoints. Keys are write-only — there is no way to read a key back once saved.
      </p>

      {/* ── API Keys section ──────────────────────────── */}
      <p style={sectionTitle}>Provider Keys</p>

      {/* Provider dropdown */}
      <div style={{ marginBottom: 14 }}>
        <label style={fieldLabel}>Provider</label>
        <div ref={providerDropRef} style={{ position: 'relative' }}>
          {/* Trigger */}
          <button
            onClick={() => providersLoaded && setProviderOpen(o => !o)}
            style={{
              ...fieldInput,
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              cursor: providersLoaded ? 'pointer' : 'default',
              width: '100%', textAlign: 'left',
            } as React.CSSProperties}
          >
            <span style={{ color: providersLoaded ? 'var(--text-primary)' : 'var(--text-muted)' }}>
              {providersLoaded ? (selectedLabel || 'Select provider') : 'Loading…'}
            </span>
            <ChevronDown size={13} style={{ color: 'var(--text-muted)', flexShrink: 0, transition: 'transform 150ms', transform: providerOpen ? 'rotate(180deg)' : 'none' }} />
          </button>

          {/* Options panel */}
          {providerOpen && (
            <div style={{
              position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, zIndex: 200,
              background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)',
              borderRadius: 9, overflow: 'hidden',
              boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
              maxHeight: 240, overflowY: 'auto',
            }}>
              {keyProviders.map(p => (
                <button
                  key={p.id}
                  onClick={() => { handleProviderChange(p.id); setProviderOpen(false) }}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    width: '100%', padding: '9px 14px', border: 'none',
                    cursor: 'pointer', textAlign: 'left',
                    color: p.id === selectedId ? 'var(--eva-primary)' : 'var(--text-primary)',
                    fontSize: 13.5,
                    background: p.id === selectedId ? 'rgba(0,201,255,0.06)' : 'transparent',
                  } as React.CSSProperties}
                  onMouseEnter={e => { if (p.id !== selectedId) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.04)' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = p.id === selectedId ? 'rgba(0,201,255,0.06)' : 'transparent' }}
                >
                  <span>{p.label}</span>
                  {p.configured && (
                    <span style={{
                      width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                      background: 'var(--success)',
                      boxShadow: '0 0 6px 2px rgba(0,212,160,0.5)',
                    }} />
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
        <p style={{ ...fieldHint, display: 'flex', alignItems: 'center', gap: 5, marginTop: 6 }}>
          <span style={{
            display: 'inline-block', width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
            background: 'var(--success)', boxShadow: '0 0 5px 1px rgba(0,212,160,0.5)',
          }} />
          Green dot — key already saved for this provider
        </p>
      </div>

      {/* Key input */}
      <div style={{ marginBottom: 14 }}>
        <label style={fieldLabel}>API Key</label>
        <div style={{
          display: 'flex', alignItems: 'center',
          background: 'var(--bg-surface2)',
          border: `1px solid ${keyValue ? 'rgba(0,201,255,0.35)' : 'var(--border-dim)'}`,
          borderRadius: 9, overflow: 'hidden',
          transition: 'border-color 150ms ease',
        }}>
          <input
            type={showKey ? 'text' : 'password'}
            value={keyValue}
            onChange={e => { setKeyValue(e.target.value); setKeyResult(null); setKeyError(null) }}
            onKeyDown={e => e.key === 'Enter' && handleSaveKey()}
            placeholder={`Paste ${selectedLabel} key…`}
            style={{
              flex: 1, padding: '10px 13px', background: 'transparent',
              border: 'none', outline: 'none',
              color: 'var(--text-primary)', fontSize: 13.5,
              fontFamily: 'JetBrains Mono, monospace',
            }}
          />
          <button
            onClick={() => setShowKey(s => !s)}
            style={{ padding: '0 12px', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex', alignItems: 'center' }}
            title={showKey ? 'Hide key' : 'Reveal key'}
          >
            {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
        <p style={fieldHint}>Keys take effect immediately — no restart required.</p>
      </div>

      {/* Save row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <button
          onClick={handleSaveKey}
          disabled={!canSaveKey}
          className="btn primary"
          style={{
            opacity: keySaving ? 0.7 : 1,
            cursor: canSaveKey ? 'pointer' : 'not-allowed',
            background: canSaveKey ? 'var(--eva-primary)' : 'var(--bg-surface3)',
            color:      canSaveKey ? '#06090F' : 'var(--text-muted)',
          }}
        >
          {keySaving
            ? <><Loader2 size={12} className="animate-spin" /> Saving…</>
            : <><Key size={12} /> Save Key</>
          }
        </button>

        {/* Validity badge */}
        {keyResult && (
          <span
            className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full"
            style={keyResult.valid === true
              ? { background: 'rgba(0,212,160,0.1)', color: 'var(--success)', border: '1px solid rgba(0,212,160,0.2)' }
              : keyResult.valid === false
              ? { background: 'rgba(245,158,11,0.1)', color: 'var(--amber)', border: '1px solid rgba(245,158,11,0.2)' }
              : { background: 'var(--bg-surface3)', color: 'var(--text-muted)', border: '1px solid var(--border-dim)' }
            }
          >
            {keyResult.valid === true
              ? <><CheckCircle2 size={10} /> Validated</>
              : keyResult.valid === false
              ? <><AlertTriangle size={10} /> Key rejected</>
              : <><Info size={10} /> Saved</>
            }
          </span>
        )}
        {keyError && (
          <span className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--danger)' }}>
            <XCircle size={11} /> {keyError}
          </span>
        )}
      </div>

      {keyResult?.message && (
        <p style={{ ...fieldHint, marginBottom: 8 }}>{keyResult.message}</p>
      )}

      {/* ── Remove key (Section 99) ── */}
      {selectedConfigured && !confirmRemove && (
        <button
          onClick={() => { setConfirmRemove(true); setRemoveResult(null); setRemoveError(null) }}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 4,
            fontSize: 12, fontWeight: 600, padding: '6px 11px', borderRadius: 8, cursor: 'pointer',
            background: 'rgba(255,77,109,0.08)', border: '1px solid rgba(255,77,109,0.28)', color: 'var(--danger)',
          }}
        >
          <Trash2 size={12} /> Remove key
        </button>
      )}
      {confirmRemove && (
        <div style={{
          padding: '12px 14px', borderRadius: 9, marginTop: 4,
          background: 'rgba(255,77,109,0.05)', border: '1px solid rgba(255,77,109,0.25)',
        }}>
          <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 6px' }}>
            Remove the {selectedLabel} key?
          </p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 12px', lineHeight: 1.55 }}>
            This deletes the key from this computer's credential store, so it is removed for <strong>all EVA accounts</strong> on
            this machine. It does <strong>not</strong> revoke the key at {selectedLabel} — if it was exposed, revoke it in your
            {' '}{selectedLabel} account too.
          </p>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn ghost" onClick={() => setConfirmRemove(false)} disabled={removing}>Cancel</button>
            <button
              className="btn ghost"
              onClick={handleRemoveKey}
              disabled={removing}
              style={{ color: 'var(--danger)', borderColor: 'rgba(255,77,109,0.35)' }}
            >
              {removing ? <><Loader2 size={12} className="animate-spin" /> Removing…</> : <><Trash2 size={12} /> Remove key</>}
            </button>
          </div>
        </div>
      )}
      {removeError && (
        <span className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--danger)', marginTop: 8 }}>
          <XCircle size={11} /> {removeError}
        </span>
      )}
      {removeResult && (
        <div style={{
          padding: '10px 13px', borderRadius: 9, marginTop: 8,
          background: removeResult.warnings.length ? 'rgba(245,158,11,0.07)' : 'rgba(0,212,160,0.06)',
          border: `1px solid ${removeResult.warnings.length ? 'rgba(245,158,11,0.25)' : 'rgba(0,212,160,0.2)'}`,
        }}>
          <p style={{ fontSize: 12.5, fontWeight: 600, margin: 0, color: removeResult.deleted ? 'var(--success)' : 'var(--text-secondary)' }}>
            {removeResult.deleted ? `${selectedLabel} key removed` : `No ${selectedLabel} key was stored`}
          </p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '4px 0 0', lineHeight: 1.5 }}>{removeResult.message}</p>
          {removeResult.warnings.length > 0 && (
            <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--amber)', lineHeight: 1.55 }}>
              {removeResult.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}
          {removeResult.in_use.session_primary && (
            <p style={{ fontSize: 12, color: 'var(--amber)', margin: '8px 0 0', fontWeight: 600 }}>
              Choose a new provider under the Providers tab.
            </p>
          )}
        </div>
      )}

      {/* Info note */}
      <div style={{
        display: 'flex', alignItems: 'flex-start', gap: 9,
        padding: '10px 13px', borderRadius: 9, marginTop: 4,
        background: 'rgba(0,201,255,0.04)', border: '1px solid rgba(0,201,255,0.1)',
      }}>
        <Info size={12} style={{ color: 'var(--eva-primary)', flexShrink: 0, marginTop: 2 }} />
        <span style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.5 }}>
          Each key is stored server-side and takes effect immediately. The validation status shows whether the provider accepted the key on a test call.
        </span>
      </div>

      <hr style={sectionDivider} />

      {/* ── Ollama section ────────────────────────────── */}
      <p style={sectionTitle}>Local Models — Ollama</p>
      <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: '0 0 18px', lineHeight: 1.55 }}>
        Ollama is a local provider — it uses a host URL instead of an API key. The host is probed for reachability after saving.
      </p>

      {ollamaLoading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', fontSize: 12.5 }}>
          <Loader2 size={13} className="animate-spin" /> Loading…
        </div>
      ) : (
        <>
          {/* Reachability indicator */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <div style={{
              width: 8, height: 8, borderRadius: 999, flexShrink: 0,
              background: reachable === true ? 'var(--success)' : reachable === false ? 'var(--danger)' : 'var(--text-muted)',
              boxShadow: reachable === true
                ? '0 0 6px rgba(0,212,160,0.6)'
                : reachable === false ? '0 0 6px rgba(255,77,109,0.6)' : 'none',
            }} />
            <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
              {reachable === true
                ? 'Ollama server is reachable'
                : reachable === false
                  ? 'Server not reachable — check that Ollama is running'
                  : 'Reachability unknown'}
            </span>
          </div>

          {/* Base URL */}
          <div style={{ marginBottom: 14 }}>
            <label style={fieldLabel}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <Server size={10} style={{ display: 'inline' }} /> Ollama Base URL
              </span>
            </label>
            <input
              type="text"
              value={baseUrl}
              onChange={e => setBaseUrl(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSaveOllama()}
              placeholder="http://localhost:11434"
              style={{ ...fieldInput, fontFamily: 'JetBrains Mono, monospace' }}
              onFocus={onFocus} onBlur={onBlur}
            />
            <p style={fieldHint}>Default is <code style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>http://localhost:11434</code>.</p>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button
              onClick={handleSaveOllama}
              disabled={!baseUrl.trim() || ollamaSaving}
              className="btn ghost"
              style={{ opacity: ollamaSaving ? 0.7 : 1 }}
            >
              {ollamaSaving
                ? <><Loader2 size={12} className="animate-spin" /> Saving…</>
                : <><Server size={12} /> Save URL</>
              }
            </button>
            {ollamaError && (
              <span className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--danger)' }}>
                <XCircle size={11} /> {ollamaError}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  )
}
