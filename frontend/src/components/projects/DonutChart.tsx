import { useState } from 'react'

// ── Donut chart ───────────────────────────────────────────────────────────────
export interface DonutSegment { value: number; color: string }

// ── Tooltip types shared by both chart components ────────────────────────────
export interface TipLine  { color?: string; label: string; value: string }
export interface TipSection { heading?: string; lines: TipLine[] }

export function DonutTip({ sections }: { sections: TipSection[] }) {
  return (
    <div style={{
      position: 'absolute',
      bottom: 'calc(100% + 8px)',
      left: '50%',
      transform: 'translateX(-50%)',
      background: '#06090f',
      border: '1px solid var(--border-mid)',
      borderRadius: 8,
      padding: '7px 10px',
      zIndex: 60,
      pointerEvents: 'none',
      whiteSpace: 'nowrap',
      boxShadow: '0 8px 28px rgba(0,0,0,0.75)',
      minWidth: 130,
    }}>
      {sections.map((sec, si) => (
        <div key={si}>
          {si > 0 && <div style={{ height: 1, background: 'var(--border-dim)', margin: '5px 0' }} />}
          {sec.heading && (
            <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 4, fontFamily: 'var(--font-mono)' }}>
              {sec.heading}
            </div>
          )}
          {sec.lines.map((line, li) => (
            <div key={li} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '1.5px 0' }}>
              {line.color && (
                <span style={{ width: 6, height: 6, borderRadius: 99, background: line.color, flexShrink: 0 }} />
              )}
              <span style={{ fontSize: 10.5, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                {line.label}
              </span>
              <span style={{ fontSize: 10.5, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', marginLeft: 'auto', paddingLeft: 10 }}>
                {line.value}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

export function DonutChart({
  segments, size = 65, thickness = 5.5, center, tooltip,
}: {
  segments:   DonutSegment[]
  size?:      number
  thickness?: number
  center?:    string
  tooltip?:   TipSection[]
}) {
  const [showTip, setShowTip] = useState(false)
  const r     = (size - thickness) / 2
  const C     = 2 * Math.PI * r
  const cx    = size / 2
  const cy    = size / 2
  const total = segments.reduce((s, g) => s + (g.value || 0), 0)
  const activeCount = segments.filter(g => g.value > 0).length
  const gap   = activeCount > 1 ? 1.5 : 0
  let accumulated = 0

  return (
    <div style={{ position: 'relative', flexShrink: 0 }}
      onMouseEnter={() => setShowTip(true)}
      onMouseLeave={() => setShowTip(false)}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--bg-surface3)" strokeWidth={thickness} />
        {total > 0 && segments.map((seg, i) => {
          if (!seg.value) return null
          const pct  = seg.value / total
          const dash = Math.max(0, pct * C - gap)
          const off  = -(accumulated * C)
          accumulated += pct
          return (
            <circle key={i} cx={cx} cy={cy} r={r}
              fill="none" stroke={seg.color} strokeWidth={thickness}
              strokeDasharray={`${dash} ${C - dash}`}
              strokeDashoffset={off}
              style={{ transform: 'rotate(-90deg)', transformOrigin: `${cx}px ${cy}px` }}
            />
          )
        })}
        {center && (
          <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central"
            style={{ fontSize: size > 44 ? 11 : 9, fontWeight: 700, fill: 'var(--text-primary)', fontFamily: 'Inter, sans-serif', letterSpacing: '-0.02em' }}>
            {center}
          </text>
        )}
      </svg>
      {showTip && tooltip && tooltip.length > 0 && <DonutTip sections={tooltip} />}
    </div>
  )
}

// Two concentric rings with per-ring hover tooltip sections.
export function ConcentricDonut({
  outer, inner, size = 72, thickness = 5, gap = 3, outerTip, innerTip,
}: {
  outer:      DonutSegment[]
  inner:      DonutSegment[]
  size?:      number
  thickness?: number
  gap?:       number
  outerTip?:  TipSection
  innerTip?:  TipSection
}) {
  const [showTip, setShowTip] = useState(false)
  const cx     = size / 2
  const cy     = size / 2
  const rOuter = (size - thickness) / 2
  const rInner = rOuter - thickness - gap

  const renderRing = (segs: DonutSegment[], r: number) => {
    const C     = 2 * Math.PI * r
    const total = segs.reduce((s, g) => s + (g.value || 0), 0)
    const nSegs = segs.filter(g => g.value > 0).length
    const segGap = nSegs > 1 ? 1.5 : 0
    let accumulated = 0
    return (
      <>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--bg-surface3)" strokeWidth={thickness} />
        {total > 0 && segs.map((seg, i) => {
          if (!seg.value) return null
          const pct  = seg.value / total
          const dash = Math.max(0, pct * C - segGap)
          const off  = -(accumulated * C)
          accumulated += pct
          return (
            <circle key={i} cx={cx} cy={cy} r={r}
              fill="none" stroke={seg.color} strokeWidth={thickness}
              strokeDasharray={`${dash} ${C - dash}`}
              strokeDashoffset={off}
              style={{ transform: 'rotate(-90deg)', transformOrigin: `${cx}px ${cy}px` }}
            />
          )
        })}
      </>
    )
  }

  const tipSections = [outerTip, innerTip].filter(Boolean) as TipSection[]

  return (
    <div style={{ position: 'relative', flexShrink: 0 }}
      onMouseEnter={() => setShowTip(true)}
      onMouseLeave={() => setShowTip(false)}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {renderRing(outer, rOuter)}
        {renderRing(inner, rInner)}
      </svg>
      {showTip && tipSections.length > 0 && <DonutTip sections={tipSections} />}
    </div>
  )
}
