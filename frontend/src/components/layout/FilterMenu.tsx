import { useState, useRef, useEffect } from 'react'
import { ChevronDown, ChevronRight, Check } from 'lucide-react'

// ─── Filter types ─────────────────────────────────────────────────────────────
export interface FilterState {
  status:       'all' | 'active' | 'paused' | 'archived'
  lastActivity: 'all' | '1d' | '3d' | '7d' | '30d'
  sortBy:       'recency' | 'alpha' | 'created'
}

export const DEFAULT_FILTER: FilterState = { status: 'all', lastActivity: 'all', sortBy: 'recency' }

// ─── Filter Menu — accordion style (avoids overflow clipping in narrow panel) ──
export default function FilterMenu({
  filters,
  onChange,
  onClose,
  showStatus = true,
}: {
  filters:    FilterState
  onChange:   (f: FilterState) => void
  onClose:    () => void
  showStatus?: boolean
}) {
  const [openItem, setOpenItem] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const STATUS_OPTIONS:   { key: FilterState['status'];       label: string }[] = [
    { key: 'all',      label: 'All' },
    { key: 'active',   label: 'Active' },
    { key: 'paused',   label: 'Paused' },
    { key: 'archived', label: 'Archived' },
  ]
  const ACTIVITY_OPTIONS: { key: FilterState['lastActivity']; label: string }[] = [
    { key: 'all', label: 'All time' },
    { key: '1d',  label: 'Last 24h' },
    { key: '3d',  label: 'Last 3 days' },
    { key: '7d',  label: 'Last 7 days' },
    { key: '30d', label: 'Last 30 days' },
  ]
  const SORT_OPTIONS:     { key: FilterState['sortBy'];       label: string }[] = [
    { key: 'recency', label: 'Recent first' },
    { key: 'alpha',   label: 'A → Z' },
    { key: 'created', label: 'Created' },
  ]

  // Close when clicking outside the menu
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  const toggle = (key: string) => setOpenItem(prev => prev === key ? null : key)

  return (
    <div
      ref={menuRef}
      className="filter-menu"
      onMouseDown={e => e.stopPropagation()}
      style={{ top: 'calc(100% + 4px)', right: 0 }}
    >
      {/* Status — accordion (projects only) */}
      {showStatus && (
        <>
          <div
            className={`filter-item${openItem === 'status' ? ' hover' : ''}`}
            onClick={() => toggle('status')}
          >
            <span className="fi-label">Status</span>
            <span className={`fi-value${filters.status !== 'all' ? ' accent' : ''}`}>
              {STATUS_OPTIONS.find(o => o.key === filters.status)?.label ?? 'All'}
            </span>
            {openItem === 'status'
              ? <ChevronDown size={12} className="fi-chev" />
              : <ChevronRight size={12} className="fi-chev" />}
          </div>
          {openItem === 'status' && (
            <div className="filter-suboptions">
              {STATUS_OPTIONS.map(o => (
                <div
                  key={o.key}
                  className="filter-suboption"
                  onClick={() => { onChange({ ...filters, status: o.key }); setOpenItem(null); onClose() }}
                >
                  <span className="fs-label">{o.label}</span>
                  {filters.status === o.key && <Check size={12} className="fs-check" />}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Last activity — accordion */}
      <div
        className={`filter-item${openItem === 'activity' ? ' hover' : ''}`}
        onClick={() => toggle('activity')}
      >
        <span className="fi-label">Last activity</span>
        <span className={`fi-value${filters.lastActivity !== 'all' ? ' accent' : ''}`}>
          {ACTIVITY_OPTIONS.find(o => o.key === filters.lastActivity)?.label ?? 'All time'}
        </span>
        {openItem === 'activity'
          ? <ChevronDown size={12} className="fi-chev" />
          : <ChevronRight size={12} className="fi-chev" />}
      </div>
      {openItem === 'activity' && (
        <div className="filter-suboptions">
          {ACTIVITY_OPTIONS.map(o => (
            <div
              key={o.key}
              className="filter-suboption"
              onClick={() => { onChange({ ...filters, lastActivity: o.key }); setOpenItem(null); onClose() }}
            >
              <span className="fs-label">{o.label}</span>
              {filters.lastActivity === o.key && <Check size={12} className="fs-check" />}
            </div>
          ))}
        </div>
      )}

      <div className="filter-divider" />

      {/* Sort by — accordion */}
      <div
        className={`filter-item${openItem === 'sort' ? ' hover' : ''}`}
        onClick={() => toggle('sort')}
      >
        <span className="fi-label">Sort by</span>
        <span className={`fi-value${filters.sortBy !== 'recency' ? ' accent' : ''}`}>
          {SORT_OPTIONS.find(o => o.key === filters.sortBy)?.label ?? 'Recent first'}
        </span>
        {openItem === 'sort'
          ? <ChevronDown size={12} className="fi-chev" />
          : <ChevronRight size={12} className="fi-chev" />}
      </div>
      {openItem === 'sort' && (
        <div className="filter-suboptions">
          {SORT_OPTIONS.map(o => (
            <div
              key={o.key}
              className="filter-suboption"
              onClick={() => { onChange({ ...filters, sortBy: o.key }); setOpenItem(null); onClose() }}
            >
              <span className="fs-label">{o.label}</span>
              {filters.sortBy === o.key && <Check size={12} className="fs-check" />}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
