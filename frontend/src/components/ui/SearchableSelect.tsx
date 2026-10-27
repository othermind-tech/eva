import { useState, useEffect, useRef } from 'react'
import { Loader2, Search, ChevronDown, X } from 'lucide-react'
import { FALLBACK_SELECT_FOCUS_MS } from '@/lib/constants'

export interface SelectOption {
  value: string
  label: string
  meta?: string   // small badge (e.g. "native", "local", "$0.03/M")
}

interface Props {
  value:        string
  onChange:     (v: string) => void
  options:      SelectOption[]
  placeholder?: string
  loading?:     boolean
  disabled?:    boolean
}

/**
 * SearchableSelect — keyboard-friendly dropdown with a filter input.
 * Used in SessionPanel (provider/model) and FallbackChainEditor (model).
 */
export default function SearchableSelect({
  value, onChange, options, placeholder = 'Select…',
  loading = false, disabled = false,
}: Props) {
  const [open, setOpen]     = useState(false)
  const [search, setSearch] = useState('')
  const containerRef        = useRef<HTMLDivElement>(null)
  const inputRef            = useRef<HTMLInputElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
        setSearch('')
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Auto-focus search input when opened
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), FALLBACK_SELECT_FOCUS_MS)
  }, [open])

  const filtered = options.filter(o =>
    !search ||
    o.label.toLowerCase().includes(search.toLowerCase()) ||
    (o.meta && o.meta.toLowerCase().includes(search.toLowerCase()))
  )
  const selected = options.find(o => o.value === value)

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger */}
      <button
        type="button"
        disabled={disabled || loading}
        onClick={() => !disabled && !loading && setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-xs transition-all duration-150"
        style={{
          background: 'var(--bg-surface2)',
          border: `1px solid ${open ? 'var(--border-bright)' : 'var(--border-dim)'}`,
          color: selected ? 'var(--text-primary)' : 'var(--text-muted)',
          opacity: (disabled || loading) ? 0.7 : 1,
          cursor: (disabled || loading) ? 'not-allowed' : 'pointer',
        }}
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {loading ? (
            <>
              <Loader2 size={11} className="animate-spin shrink-0" style={{ color: 'var(--text-muted)' }} />
              <span style={{ color: 'var(--text-muted)' }}>Loading…</span>
            </>
          ) : (
            <>
              <span className="font-mono truncate">{selected?.label ?? placeholder}</span>
              {selected?.meta && (
                <span
                  className="text-xs px-1.5 py-0.5 rounded shrink-0"
                  style={{ background: 'var(--bg-surface3)', color: 'var(--text-muted)', fontSize: 9 }}
                >
                  {selected.meta}
                </span>
              )}
            </>
          )}
        </div>
        <ChevronDown
          size={12}
          className={`shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          style={{ color: 'var(--text-muted)', marginLeft: 4 }}
        />
      </button>

      {/* Dropdown panel */}
      {open && (
        <div
          className="absolute top-full left-0 right-0 mt-1 z-50 rounded-xl overflow-hidden"
          style={{
            background:  'var(--bg-surface2)',
            border:      '1px solid var(--border-mid)',
            boxShadow:   '0 8px 32px rgba(0,0,0,0.5)',
          }}
        >
          {/* Search row */}
          <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: '1px solid var(--border-dim)' }}>
            <Search size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
            <input
              ref={inputRef}
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search…"
              className="flex-1 text-xs outline-none bg-transparent"
              style={{ color: 'var(--text-primary)' }}
              onKeyDown={e => {
                if (e.key === 'Escape') { setOpen(false); setSearch('') }
                if (e.key === 'Enter' && filtered.length === 1) {
                  onChange(filtered[0].value); setOpen(false); setSearch('')
                }
              }}
            />
            {search && (
              <button onClick={() => setSearch('')} style={{ color: 'var(--text-muted)' }}>
                <X size={10} />
              </button>
            )}
          </div>

          {/* Options list */}
          <div className="max-h-52 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <div className="px-3 py-3 text-xs text-center" style={{ color: 'var(--text-muted)' }}>
                No matches
              </div>
            ) : (
              filtered.map(o => (
                <button
                  key={o.value}
                  onClick={() => { onChange(o.value); setOpen(false); setSearch('') }}
                  className="w-full text-left flex items-center justify-between px-3 py-2 text-xs transition-colors duration-100"
                  style={{
                    background: value === o.value ? 'var(--eva-primary-dim)' : 'transparent',
                    color:      value === o.value ? 'var(--eva-primary)' : 'var(--text-secondary)',
                  }}
                  onMouseEnter={e => {
                    if (value !== o.value) e.currentTarget.style.background = 'var(--bg-surface3)'
                  }}
                  onMouseLeave={e => {
                    if (value !== o.value) e.currentTarget.style.background = 'transparent'
                  }}
                >
                  <span className="font-mono truncate">{o.label}</span>
                  {o.meta && (
                    <span
                      className="text-xs px-1.5 py-0.5 rounded shrink-0 ml-2"
                      style={{ background: 'var(--bg-surface3)', color: 'var(--text-muted)', fontSize: 9 }}
                    >
                      {o.meta}
                    </span>
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
