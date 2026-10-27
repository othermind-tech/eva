import { Plus } from 'lucide-react'
import type { FallbackEntry, ProviderListItem } from '@/types/api'
import PrefFallbackRow from './PrefFallbackRow'

// ─── Preferences tab — Fallback chain editor ──────────────────────────────────
const FALLBACK_CAP = 5

export default function PrefFallbackChainEditor({
  chain, onChange, providerItems, errors,
}: {
  errors?:       Record<number, string>
  chain:         FallbackEntry[]
  onChange:      (c: FallbackEntry[]) => void
  providerItems: ProviderListItem[]
}) {
  const atCap  = chain.length >= FALLBACK_CAP
  // Section 98 — seed a new row with a provider that can actually chat; the first
  // listed one may be unconfigured, which the backend now rejects on save (422).
  const firstReady = providerItems.find(p => p.chat_ready)?.provider
  const add    = () => { if (!atCap && firstReady) onChange([...chain, { provider: firstReady, model: '' }]) }
  const remove = (i: number) => onChange(chain.filter((_, idx) => idx !== i))
  const update = (i: number, patch: Partial<FallbackEntry>) =>
    onChange(chain.map((e, idx) => idx === i ? { ...e, ...patch } : e))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
      {chain.length === 0 && (
        <p style={{ fontSize: 12, color: 'var(--text-faint)', margin: '0 0 4px', fontStyle: 'italic' }}>
          No fallbacks configured — EVA will only use the primary provider.
        </p>
      )}
      {chain.map((e, i) => (
        <PrefFallbackRow
          key={i}
          entry={e} index={i}
          providerItems={providerItems}
          error={errors?.[i]}
          onUpdate={p => update(i, p)}
          onRemove={() => remove(i)}
        />
      ))}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <button
          onClick={add}
          disabled={atCap || !firstReady}
          title={!firstReady ? 'Add an API key first — no provider is ready to chat' : undefined}
          style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '8px 14px',
            borderRadius: 9, border: '1px dashed var(--border-dim)',
            background: 'transparent', color: 'var(--text-muted)',
            cursor: atCap || !firstReady ? 'not-allowed' : 'pointer',
            fontSize: 12.5, transition: 'border-color 150ms, color 150ms',
            opacity: atCap || !firstReady ? 0.45 : 1,
          }}
          onMouseEnter={e => { if (!atCap && firstReady) { e.currentTarget.style.borderColor = 'var(--border-mid)'; e.currentTarget.style.color = 'var(--text-secondary)' } }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-dim)'; e.currentTarget.style.color = 'var(--text-muted)' }}
        >
          <Plus size={12} /> Add Fallback
        </button>
        {chain.length > 0 && (
          <span style={{ fontSize: 11, color: atCap ? 'var(--amber)' : 'var(--text-faint)', fontVariantNumeric: 'tabular-nums' }}>
            {atCap ? 'Max 5 reached' : `${chain.length} / ${FALLBACK_CAP}`}
          </span>
        )}
      </div>
    </div>
  )
}
