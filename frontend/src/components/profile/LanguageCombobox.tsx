import { useState, useRef, useEffect } from 'react'
import { ChevronDown } from 'lucide-react'
import { LANGUAGE_LIST, PROFILE_INPUT, profileFocus, profileBlur } from './profileConstants'

// ─── Language Combobox ────────────────────────────────────────────────────────
export default function LanguageCombobox({
  value, onChange,
}: {
  value: string | null
  onChange: (v: string | null) => void
}) {
  // Display the label if the stored code is in our list; otherwise show the raw code
  const getLabel = (code: string | null) =>
    LANGUAGE_LIST.find(l => l.code === code)?.label ?? (code ?? '')

  const [query,   setQuery]   = useState(() => getLabel(value))
  const [open,    setOpen]    = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  // Sync display when value changes externally
  useEffect(() => { setQuery(getLabel(value)) }, [value])

  // Click outside to close
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const filtered = LANGUAGE_LIST.filter(l =>
    !query ||
    l.label.toLowerCase().includes(query.toLowerCase()) ||
    l.code.toLowerCase().includes(query.toLowerCase())
  ).slice(0, 10)

  const select = (lang: { code: string; label: string }) => {
    setQuery(lang.label)
    onChange(lang.code)
    setOpen(false)
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setQuery(e.target.value)
    setOpen(true)
    if (!e.target.value) onChange(null)
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <input
        type="text"
        value={query}
        onChange={handleChange}
        onFocus={e => { setOpen(true); profileFocus(e) }}
        onBlur={profileBlur}
        onKeyDown={e => e.key === 'Escape' && setOpen(false)}
        placeholder="Search language…"
        style={PROFILE_INPUT}
        autoComplete="off"
        spellCheck={false}
      />
      {/* small arrow hint */}
      <ChevronDown size={13} style={{
        position: 'absolute', right: 11, top: '50%', transform: 'translateY(-50%)',
        color: 'var(--text-muted)', pointerEvents: 'none',
      }} />
      {open && filtered.length > 0 && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0,
          background: 'var(--bg-surface1)', border: '1px solid var(--border-mid)',
          borderRadius: 10, zIndex: 2200, overflow: 'hidden',
          boxShadow: '0 8px 28px rgba(0,0,0,0.5)',
          maxHeight: 220, overflowY: 'auto',
        }}>
          {filtered.map((l, i) => {
            const active = value === l.code
            return (
              <div
                key={l.code}
                onMouseDown={e => { e.preventDefault(); select(l) }}
                style={{
                  padding: '9px 13px', fontSize: 13, cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  borderBottom: i < filtered.length - 1 ? '1px solid var(--border-dim)' : 'none',
                  background: active ? 'rgba(0,201,255,0.07)' : 'transparent',
                  color: active ? 'var(--eva-primary)' : 'var(--text-primary)',
                  transition: 'background 100ms',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = active ? 'rgba(0,201,255,0.1)' : 'var(--bg-surface2)')}
                onMouseLeave={e => (e.currentTarget.style.background = active ? 'rgba(0,201,255,0.07)' : 'transparent')}
              >
                <span>{l.label}</span>
                <span style={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-faint)' }}>{l.code}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
