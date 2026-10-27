import { useState, type ReactNode } from 'react'
import { ChevronRight, ChevronDown } from 'lucide-react'

interface Props {
  summary:      ReactNode
  children?:    ReactNode
  defaultOpen?: boolean
  open?:        boolean            // controlled mode — pass together with onOpenChange
  onOpenChange?: (open: boolean) => void
  tone?:        'default' | 'danger'
  disabled?:    boolean   // no chevron/click when there's nothing to expand (e.g. still running)
}

// Shared collapsed-line + expandable-body primitive for EVA's passive internal-process
// surfaces (tool calls, reasoning, subagent activity, coordinator phases, errors).
// Deliberately has no background fill, border, or icon badge in the collapsed state —
// it should read as a single muted text line, not a card, so it doesn't compete for
// attention with the actual streamed answer.
export default function InlineDisclosure({ summary, children, defaultOpen = false, open: controlledOpen, onOpenChange, tone = 'default', disabled = false }: Props) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen)
  const isControlled = controlledOpen !== undefined
  const open = isControlled ? controlledOpen : uncontrolledOpen
  const setOpen = (next: boolean) => {
    if (onOpenChange) onOpenChange(next)
    if (!isControlled) setUncontrolledOpen(next)
  }
  const canToggle = !disabled && children != null

  const summaryColor = tone === 'danger' ? 'var(--danger)' : 'var(--text-muted)'

  return (
    <div style={{ margin: '2px 0' }}>
      <button
        onClick={() => canToggle && setOpen(!open)}
        disabled={!canToggle}
        className="flex items-center gap-1.5 w-full text-left rounded-md transition-colors duration-100"
        style={{
          padding: '3px 4px',
          marginLeft: -4,
          cursor: canToggle ? 'pointer' : 'default',
          fontSize: 12.5,
          color: summaryColor,
        }}
        onMouseEnter={e => { if (canToggle) e.currentTarget.style.background = 'rgba(255,255,255,0.02)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
      >
        {canToggle ? (
          open
            ? <ChevronDown  size={11} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
            : <ChevronRight size={11} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
        ) : (
          <span style={{ width: 11, flexShrink: 0 }} />
        )}
        <span className="truncate" style={{ minWidth: 0 }}>{summary}</span>
      </button>

      {open && canToggle && (
        <div
          style={{
            marginLeft: 15,
            paddingLeft: 10,
            marginTop: 2,
            borderLeft: '1px solid var(--border-dim)',
          }}
        >
          {children}
        </div>
      )}
    </div>
  )
}
