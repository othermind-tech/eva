import type { ReasoningLevel } from '@/types/api'
import { REASONING_LEVELS } from '@/types/api'

function cap(s: string) { return s.charAt(0).toUpperCase() + s.slice(1) }

interface Props {
  supports_reasoning: boolean | null
  levels:             string[] | null
  canDisable:         boolean | null
  value:              ReasoningLevel | null | undefined
  onChange:           (v: ReasoningLevel | null) => void
}

/**
 * Reasoning level control for settings panels (WorkspacePanel, SessionPanel).
 *
 * Three render paths:
 *  1. supports_reasoning falsy         → renders nothing
 *  2. levels has 0–1 entries           → binary toggle row
 *  3. levels has 2+ entries            → horizontal segmented control
 *
 * Ollama special-casing removed — backend now returns proper level data for all providers.
 */
export default function ReasoningControl({ supports_reasoning, levels, canDisable, value, onChange }: Props) {
  if (!supports_reasoning) return null

  // If backend has no level data for a reasoning-capable model, offer full range as safe fallback
  const effectiveLevels: string[] = (levels && levels.length > 0) ? levels : REASONING_LEVELS

  const showToggle = effectiveLevels.length <= 1
  const showOff    = canDisable !== false

  if (showToggle) {
    // Binary path — canDisable:false forces always-ON (model requires reasoning)
    const forced = canDisable === false
    const isOn   = forced ? true : value != null
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <span style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
          Reasoning
          {forced && <span style={{ fontSize: 10, color: 'var(--text-faint)', marginLeft: 4 }}>(required)</span>}
        </span>
        <button
          role="switch"
          aria-checked={isOn}
          disabled={forced}
          onClick={() => { if (!forced) onChange(isOn ? null : (effectiveLevels[0] as ReasoningLevel | undefined) ?? 'medium') }}
          style={{
            width: 34, height: 19, borderRadius: 999, border: 'none',
            cursor: forced ? 'not-allowed' : 'pointer',
            background: isOn ? 'var(--neural)' : 'var(--bg-surface3)',
            position: 'relative', transition: 'background 150ms', flexShrink: 0,
            opacity: forced ? 0.65 : 1,
          }}
        >
          <span style={{
            position: 'absolute', top: 2, left: isOn ? 17 : 2,
            width: 15, height: 15, borderRadius: '50%',
            background: '#fff', transition: 'left 150ms',
          }} />
        </button>
      </div>
    )
  }

  // Level picker — segmented control
  const active = value ?? null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>Reasoning</span>
      <div style={{
        display: 'flex', gap: 3,
        background: 'var(--bg-surface2)',
        border: '1px solid var(--border-dim)',
        borderRadius: 9, padding: 3,
      }}>
        {showOff && (
          <SegBtn
            label="Off"
            active={active === null}
            onClick={() => onChange(null)}
          />
        )}
        {effectiveLevels.map(lvl => (
          <SegBtn
            key={lvl}
            label={cap(lvl)}
            active={active === lvl}
            onClick={() => onChange(lvl as ReasoningLevel)}
          />
        ))}
      </div>
    </div>
  )
}

function SegBtn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        flex: 1, padding: '4px 6px',
        borderRadius: 6, border: 'none', cursor: 'pointer',
        fontSize: 11, fontWeight: active ? 600 : 400,
        background: active ? 'var(--neural)' : 'transparent',
        color: active ? '#fff' : 'var(--text-muted)',
        transition: 'background 120ms, color 120ms',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </button>
  )
}
