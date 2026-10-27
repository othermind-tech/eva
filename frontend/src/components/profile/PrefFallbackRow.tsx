import { useState, useEffect } from 'react'
import { Trash2 } from 'lucide-react'
import { providersApi } from '@/api/tools'
import SearchableSelect from '@/components/ui/SearchableSelect'
import ProviderFieldError from '@/components/ui/ProviderFieldError'
import type { SelectOption } from '@/components/ui/SearchableSelect'
import { chatReadyProviderOptions, modelOptions } from '@/lib/providers'
import type { FallbackEntry, LiveModelOut, ProviderListItem } from '@/types/api'

// ─── Preferences tab — Fallback chain row ────────────────────────────────────
export default function PrefFallbackRow({
  entry, index, providerItems, onUpdate, onRemove, error,
}: {
  // Section 101 - the server's message for this row (field: fallback_chain[i].provider)
  error?:        string
  entry:         FallbackEntry
  index:         number
  providerItems: ProviderListItem[]
  onUpdate:      (p: Partial<FallbackEntry>) => void
  onRemove:      () => void
}) {
  const [rowModels,  setRowModels]  = useState<LiveModelOut[]>([])
  const [rowLoading, setRowLoading] = useState(false)

  useEffect(() => {
    if (!entry.provider) { setRowModels([]); return }
    let cancelled = false
    setRowLoading(true)
    providersApi.getModels(entry.provider)
      .then(d => {
        if (!cancelled) { setRowModels(d.models.filter(m => m.active)); setRowLoading(false) }
      })
      .catch(() => { if (!cancelled) { setRowModels([]); setRowLoading(false) } })
    return () => { cancelled = true }
  }, [entry.provider])

  const pOpts: SelectOption[] = chatReadyProviderOptions(providerItems, entry.provider)
  const mOpts: SelectOption[] = modelOptions(rowModels, entry.model, !rowLoading)

  return (
    <div>
    <div style={{
      display: 'grid', gridTemplateColumns: '24px 1fr 1fr auto',
      alignItems: 'center', gap: 8,
      padding: '10px 12px', borderRadius: 10,
      background: 'var(--bg-surface2)', border: `1px solid ${error ? 'rgba(255,77,109,0.45)' : 'var(--border-dim)'}`,
    }}>
      <span style={{ fontSize: 11, color: 'var(--text-faint)', textAlign: 'center' }}>{index + 1}</span>
      <SearchableSelect
        value={entry.provider}
        onChange={v => onUpdate({ provider: v as never, model: '' })}
        options={pOpts}
        placeholder="— provider —"
      />
      <SearchableSelect
        value={entry.model}
        onChange={v => onUpdate({ model: v })}
        options={mOpts}
        placeholder={rowLoading ? '— loading…' : '— model —'}
        loading={rowLoading}
        disabled={!entry.provider}
      />
      <button
        onClick={onRemove}
        style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: '4px' }}
        title="Remove"
      >
        <Trash2 size={12} />
      </button>
    </div>
    {error && <ProviderFieldError message={error} />}
    </div>
  )
}
