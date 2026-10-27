import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ChevronRight, Loader2, AlertTriangle, HardDrive, Cpu, Globe, Key, CheckCircle2, RefreshCw,
} from 'lucide-react'
import { providersApi } from '@/api/tools'
import { useSessionStore } from '@/store/sessionStore'
import SearchableSelect from '@/components/ui/SearchableSelect'
import type { SelectOption } from '@/components/ui/SearchableSelect'
import ReasoningControl from '@/components/ui/ReasoningControl'
import { chatReadyProviderOptions, modelOptions as buildModelOptions } from '@/lib/providers'
import type { LiveModelOut, ProviderListItem } from '@/types/api'
import PrefFallbackChainEditor from './PrefFallbackChainEditor'
import ProviderFieldError from '@/components/ui/ProviderFieldError'

// Section 101 - POST /set-session can refuse the primary provider/model or a fallback
// entry; the modal passes the server's message down so it lands on the right control.
export interface ProviderSaveErrors {
  provider?: { message: string; reason?: string }
  model?:    string
  fallback?: Record<number, string>
}

// ─── Preferences tab — main content ──────────────────────────────────────────
export default function ProvidersTabContent({ errors, onEdit }: { errors?: ProviderSaveErrors; onEdit?: () => void } = {}) {
  const config    = useSessionStore(s => s.config)
  const setConfig = useSessionStore(s => s.setConfig)

  const {
    data:      providers = [],
    isLoading: providersLoading,
    isError:   providersError,
    refetch:   refetchProviders,
  } = useQuery<ProviderListItem[]>({
    queryKey: ['providers'],
    queryFn:  providersApi.list,
    retry: 2,
    staleTime: 0,
    gcTime: 0,
  })

  const [liveModels,   setLiveModels]   = useState<LiveModelOut[]>([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const [modelsError,   setModelsError]   = useState<string | null>(null)
  const [keyNeeded,     setKeyNeeded]     = useState(false)
  const [openProviders,    setOpenProviders]    = useState(true)
  const [openModel,        setOpenModel]        = useState(true)
  const [openFallback,     setOpenFallback]     = useState(true)

  // Fetch live models when provider changes
  useEffect(() => {
    if (!config.provider) { setLiveModels([]); setModelsError(null); setKeyNeeded(false); return }
    let cancelled = false
    setModelsLoading(true); setModelsError(null); setKeyNeeded(false)
    providersApi.getModels(config.provider)
      .then(d => {
        if (cancelled) return
        setLiveModels(d.models.filter(m => m.active))
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const detail = err != null && typeof err === 'object' && 'detail' in err
          ? (err as Record<string, unknown>).detail : undefined
        const code = detail != null && typeof detail === 'object' && 'code' in (detail as object)
          ? String((detail as Record<string, unknown>).code) : undefined
        const status = (err as { status?: number } | null)?.status
        if (code === 'EVA_KEY_NOT_CONFIGURED') setKeyNeeded(true)
        // Section 98 - providers EVA hasn't verified (or has since dropped, e.g. azure) now 404.
        else if (status === 404) setModelsError("This provider isn't offered in EVA any more - choose another.")
        else setModelsError('Could not load models — check backend connectivity.')
        setLiveModels([])
      })
      .finally(() => { if (!cancelled) setModelsLoading(false) })
    return () => { cancelled = true }
  }, [config.provider])

  // Per-model capabilities — shared TanStack Query cache keyed by provider+model
  const { data: modelCaps, isLoading: capsLoading } = useQuery({
    queryKey: ['capabilities', config.provider, config.model],
    queryFn:  () => providersApi.getCapabilities(config.provider!, config.model!),
    enabled:  !!(config.provider && config.model),
    staleTime: 5 * 60_000,
  })

  // Section 98 - the primary picker offers chat_ready providers only (not is_configured:
  // a provider can hold a key yet be unable to chat). The saved choice stays visible,
  // flagged, if it is no longer offered.
  const providerOptions: SelectOption[] =
    chatReadyProviderOptions(providers, providersLoading ? undefined : config.provider)
  const modelOptions: SelectOption[] =
    buildModelOptions(liveModels, config.model, !modelsLoading && !keyNeeded && !modelsError)

  const sortedProviders = [...providers].sort((a, b) =>
    (b.chat_ready ? 1 : 0) - (a.chat_ready ? 1 : 0)
  )
  const readyCount     = providers.filter(p => p.chat_ready).length
  const activeProvider = providers.find(p => p.provider === config.provider)
  // A saved primary that the backend no longer lists as chat-ready (key removed, or a
  // provider that was dropped from the list) - chat on it would be rejected.
  const primaryUnavailable = !providersLoading && !providersError && !!config.provider
    && !activeProvider?.chat_ready

  // ── local style helpers ──────────────────────────────────────────────────
  const ST: React.CSSProperties = {
    fontSize: 10, fontWeight: 700, letterSpacing: '0.1em',
    textTransform: 'uppercase', color: 'var(--text-faint)', marginBottom: 14,
    display: 'flex', alignItems: 'center', gap: 8,
  }
  const SD: React.CSSProperties = { margin: '24px 0 20px', border: 'none', borderTop: '1px solid var(--border-dim)' }
  const FL: React.CSSProperties = {
    fontSize: 10.5, fontWeight: 700, letterSpacing: '0.08em',
    textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 7,
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  }

  return (
    <div>
      <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 26px' }}>Providers</h3>

      {/* ── 1. Configured Providers ── */}
      <button
        onClick={() => setOpenProviders(o => !o)}
        style={{ ...ST, width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: 0, textAlign: 'left' }}
      >
        <ChevronRight size={11} style={{ color: 'var(--text-faint)', flexShrink: 0, transition: 'transform 150ms ease', transform: openProviders ? 'rotate(90deg)' : 'none' }} />
        Configured Providers
        <span style={{
          fontSize: 9.5, fontWeight: 700, padding: '1px 7px', borderRadius: 99,
          background: readyCount > 0 ? 'rgba(0,212,160,0.12)' : 'var(--bg-surface3)',
          color: readyCount > 0 ? 'var(--success)' : 'var(--text-faint)',
          border: `1px solid ${readyCount > 0 ? 'rgba(0,212,160,0.25)' : 'var(--border-dim)'}`,
        }}>
          {readyCount} ready
        </span>
      </button>

      {openProviders && (
        providersLoading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--text-muted)', marginBottom: 18, marginTop: 10 }}>
            <Loader2 size={12} className="animate-spin" /> Loading providers…
          </div>
        ) : (
          <div style={{ maxHeight: 220, overflowY: 'auto', marginBottom: 6, paddingRight: 4, marginTop: 10 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
            {sortedProviders.map(p => (
              <div key={p.provider} style={{
                padding: '9px 12px', borderRadius: 10,
                background: 'var(--bg-surface2)',
                border: `1px solid ${p.chat_ready ? 'var(--border-mid)' : 'var(--border-dim)'}`,
              }}>
                {/* Name + status */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
                  {p.is_local
                    ? <HardDrive size={12} style={{ color: 'var(--success)', flexShrink: 0 }} />
                    : <Cpu size={12} style={{ color: p.chat_ready ? 'var(--eva-primary)' : 'var(--text-muted)', flexShrink: 0 }} />
                  }
                  <span style={{
                    fontSize: 12.5, fontWeight: 600, flex: 1, minWidth: 0,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    color: p.chat_ready ? 'var(--text-primary)' : 'var(--text-muted)',
                  }}>
                    {p.display_name}
                  </span>
                  {p.chat_ready ? (
                    <CheckCircle2 size={12} style={{ color: 'var(--success)', flexShrink: 0 }} />
                  ) : p.is_local ? (
                    <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 99, background: 'rgba(0,212,160,0.1)', color: 'var(--success)', border: '1px solid rgba(0,212,160,0.25)', flexShrink: 0 }}>local</span>
                  ) : p.is_configured ? (
                    <span title="A key is saved, but this provider cannot be used for chat yet"><AlertTriangle size={11} style={{ color: 'var(--amber)', flexShrink: 0 }} /></span>
                  ) : (
                    <Key size={11} style={{ color: 'var(--amber)', flexShrink: 0 }} />
                  )}
                </div>
                {/* Tags */}
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {p.is_native && (
                    <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 99, background: 'rgba(0,201,255,0.07)', color: 'var(--eva-primary)', border: '1px solid rgba(0,201,255,0.18)' }}>native</span>
                  )}
                  {p.is_local && (
                    <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 99, background: 'rgba(0,212,160,0.07)', color: 'var(--success)', border: '1px solid rgba(0,212,160,0.18)' }}>no cost</span>
                  )}
                  {!p.is_native && !p.is_local && (
                    <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 99, background: 'var(--bg-surface3)', color: 'var(--text-faint)', border: '1px solid var(--border-dim)' }}>gateway</span>
                  )}
                  <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 99, background: 'var(--bg-surface3)', color: 'var(--text-faint)', border: '1px solid var(--border-dim)', fontFamily: 'JetBrains Mono, monospace' }}>
                    {p.provider}
                  </span>
                </div>
              </div>
            ))}
          </div>
          </div>
        )
      )}

      <hr style={SD} />

      {/* ── 2. Provider & Model ── */}
      <button
        onClick={() => setOpenModel(o => !o)}
        style={{ ...ST, width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: 0, textAlign: 'left', marginBottom: openModel ? 14 : 0 }}
      >
        <ChevronRight size={11} style={{ color: 'var(--text-faint)', flexShrink: 0, transition: 'transform 150ms ease', transform: openModel ? 'rotate(90deg)' : 'none' }} />
        Provider & Model
      </button>
      {openModel && <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 6 }}>

        {/* PROVIDER column */}
        <div>
          <p style={FL}>
            Provider
            <button onClick={() => refetchProviders()} disabled={providersLoading}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 2 }}>
              <RefreshCw size={10} className={providersLoading ? 'animate-spin' : ''} />
            </button>
          </p>
          {providersError && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px', borderRadius: 8, marginBottom: 7, background: 'rgba(255,77,109,0.07)', border: '1px solid rgba(255,77,109,0.2)', color: 'var(--danger)', fontSize: 11.5 }}>
              <AlertTriangle size={11} /> Failed to load providers.
            </div>
          )}
          {primaryUnavailable && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px', borderRadius: 8, marginBottom: 7, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)', color: 'var(--amber)', fontSize: 11.5 }}>
              <AlertTriangle size={11} /> {activeProvider?.is_configured === false
                ? 'No key is saved for this provider - chat will fail. Add one under API Keys, or choose another provider.'
                : 'This provider cannot be used for chat - choose another.'}
            </div>
          )}
          <SearchableSelect
            value={config.provider ?? ''}
            onChange={v => { onEdit?.(); setConfig({ provider: v as never, model: '' }) }}
            options={providerOptions}
            placeholder={providersLoading ? '— loading providers…' : '— select provider —'}
            loading={providersLoading}
          />
          {errors?.provider && <ProviderFieldError message={errors.provider.message} reason={errors.provider.reason} />}
          {activeProvider && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>
              {activeProvider.is_local && (
                <span style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 9px', borderRadius: 99, fontSize: 10.5, background: 'rgba(0,212,160,0.09)', color: 'var(--success)', border: '1px solid rgba(0,212,160,0.22)' }}>
                  <HardDrive size={9} /> Local · No API Cost
                </span>
              )}
              {activeProvider.is_native && !activeProvider.is_local && (
                <span style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 9px', borderRadius: 99, fontSize: 10.5, background: 'rgba(0,201,255,0.07)', color: 'var(--eva-primary)', border: '1px solid rgba(0,201,255,0.2)' }}>
                  <Globe size={9} /> Native provider
                </span>
              )}
              {!activeProvider.is_configured && !activeProvider.is_local && (
                <span style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 9px', borderRadius: 99, fontSize: 10.5, background: 'rgba(245,158,11,0.08)', color: 'var(--amber)', border: '1px solid rgba(245,158,11,0.25)' }}>
                  <Key size={9} /> API key needed
                </span>
              )}
              {activeProvider.is_configured && !activeProvider.chat_ready && (
                <span style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 9px', borderRadius: 99, fontSize: 10.5, background: 'rgba(245,158,11,0.08)', color: 'var(--amber)', border: '1px solid rgba(245,158,11,0.25)' }}>
                  <AlertTriangle size={9} /> Not available for chat
                </span>
              )}
              {activeProvider.chat_ready && !activeProvider.is_local && (
                <span style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 9px', borderRadius: 99, fontSize: 10.5, background: 'rgba(0,212,160,0.07)', color: 'var(--success)', border: '1px solid rgba(0,212,160,0.2)' }}>
                  <CheckCircle2 size={9} /> Configured
                </span>
              )}
            </div>
          )}
        </div>

        {/* MODEL column */}
        <div>
          <p style={FL}>
            Model
            {config.provider && (
              <button
                onClick={async () => {
                  if (!config.provider || modelsLoading) return
                  try { await providersApi.refreshModels(config.provider) } catch { /* ignore */ }
                  setLiveModels([])
                }}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: modelsLoading ? 'var(--text-muted)' : 'var(--eva-primary)', padding: 2 }}
              >
                <RefreshCw size={10} className={modelsLoading ? 'animate-spin' : ''} />
              </button>
            )}
          </p>
          {keyNeeded && (
            <div style={{ display: 'flex', gap: 7, padding: '8px 11px', borderRadius: 9, marginBottom: 7, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)' }}>
              <Key size={13} style={{ color: 'var(--amber)', flexShrink: 0, marginTop: 1 }} />
              <div>
                <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--amber)', margin: 0 }}>API key not configured</p>
                <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0' }}>Add your key in API Keys settings</p>
              </div>
            </div>
          )}
          {modelsError && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px', borderRadius: 8, marginBottom: 7, background: 'var(--bg-surface2)', color: 'var(--text-muted)', fontSize: 11.5 }}>
              <AlertTriangle size={11} /> {modelsError}
            </div>
          )}
          <SearchableSelect
            value={config.model ?? ''}
            onChange={v => { onEdit?.(); setConfig({ model: v }) }}
            options={modelOptions}
            placeholder={modelsLoading ? '— loading models…' : '— select model —'}
            loading={modelsLoading}
            disabled={!config.provider}
          />
          {errors?.model && <ProviderFieldError message={errors.model} />}

          {/* Reasoning level control */}
          {modelCaps?.supports_reasoning !== false && (
            <div style={{ marginTop: 10 }}>
              <ReasoningControl
                supports_reasoning={modelCaps?.supports_reasoning ?? true}
                levels={modelCaps?.reasoning_levels ?? null}
                canDisable={modelCaps?.reasoning_can_disable ?? null}
                value={config.reasoning}
                onChange={v => setConfig({ reasoning: v })}
              />
            </div>
          )}

          {/* Capability badges */}
          {capsLoading && config.model && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 9, fontSize: 11, color: 'var(--text-muted)' }}>
              <Loader2 size={10} className="animate-spin" /> Loading capabilities…
            </div>
          )}
          {modelCaps && !capsLoading && (
            <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 7 }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {([
                  [modelCaps.supports_tool_calls,  'Tool Calls'],
                  [modelCaps.supports_reasoning,   'Reasoning'],
                  [modelCaps.supports_vision,      'Vision'],
                  [modelCaps.supports_streaming,   'Streaming'],
                  [modelCaps.supports_json_schema, 'JSON Schema'],
                  [modelCaps.supports_file_upload, 'File Upload'],
                ] as [boolean | null, string][]).filter(([v]) => v !== null).map(([active, label]) => (
                  <span key={label} style={{
                    fontSize: 10, padding: '2px 8px', borderRadius: 99,
                    background: active ? 'rgba(0,201,255,0.08)' : 'var(--bg-surface2)',
                    border: `1px solid ${active ? 'rgba(0,201,255,0.2)' : 'var(--border-dim)'}`,
                    color: active ? 'var(--eva-primary)' : 'var(--text-muted)',
                  }}>
                    {active ? '✓' : '✗'} {label}
                  </span>
                ))}
              </div>
              {(modelCaps.max_context_tokens || modelCaps.max_output_tokens) && (
                <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: 0 }}>
                  {[
                    modelCaps.max_context_tokens && `${(modelCaps.max_context_tokens / 1000).toFixed(0)}K context`,
                    modelCaps.max_output_tokens  && `${(modelCaps.max_output_tokens  / 1000).toFixed(0)}K output`,
                  ].filter(Boolean).join(' · ')}
                  {modelCaps.source !== 'unknown' && (
                    <span style={{ marginLeft: 8, fontSize: 9.5, color: 'var(--text-faint)' }}>via {modelCaps.source}</span>
                  )}
                </p>
              )}
            </div>
          )}
        </div>
      </div>}

      <hr style={SD} />

      {/* ── 3. Fallback Chain ── */}
      <button
        onClick={() => setOpenFallback(o => !o)}
        style={{ ...ST, width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: 0, textAlign: 'left', marginBottom: openFallback ? 14 : 0 }}
      >
        <ChevronRight size={11} style={{ color: 'var(--text-faint)', flexShrink: 0, transition: 'transform 150ms ease', transform: openFallback ? 'rotate(90deg)' : 'none' }} />
        Fallback Chain
      </button>
      {openFallback && (
        <>
          <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: '0 0 14px', lineHeight: 1.55 }}>
            If the primary provider fails, EVA tries these in order.
          </p>
          <PrefFallbackChainEditor
            chain={config.fallback_chain ?? []}
            onChange={chain => { onEdit?.(); setConfig({ fallback_chain: chain }) }}
            providerItems={providers}
            errors={errors?.fallback}
          />
        </>
      )}
    </div>
  )
}
