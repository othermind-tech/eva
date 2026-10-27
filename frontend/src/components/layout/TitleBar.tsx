import { useState, useEffect, type CSSProperties } from 'react'

const IS_MAC = window.electronAPI?.platform === 'darwin'

// WebkitAppRegion is an Electron/Chromium extension that React's CSSProperties type doesn't include.
const DRAG: CSSProperties    = { WebkitAppRegion: 'drag' } as CSSProperties
const NO_DRAG: CSSProperties = { WebkitAppRegion: 'no-drag' } as CSSProperties

export default function TitleBar() {
  const [isMaximized, setIsMaximized] = useState(false)

  useEffect(() => {
    const api = window.electronAPI
    if (!api) return
    const handler = (v: boolean) => setIsMaximized(v)
    api.onMaximizeChange?.(handler)
    return () => api.offMaximizeChange?.(handler)
  }, [])

  if (IS_MAC) return null

  const minimize = () => window.electronAPI?.minimizeWindow()
  const maximize = () => window.electronAPI?.maximizeWindow()
  const close    = () => window.electronAPI?.closeWindow()

  return (
    <div
      style={{
        height: 32,
        flexShrink: 0,
        background: 'var(--bg-base)',
        borderBottom: '1px solid var(--border-dim)',
        display: 'flex',
        alignItems: 'center',
        ...DRAG,
        userSelect: 'none',
      }}
    >
      {/* EVA label */}
      <span
        style={{
          marginLeft: 12,
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.12em',
          color: 'var(--eva-primary)',
          fontFamily: 'JetBrains Mono, monospace',
          opacity: 0.7,
        }}
      >
        EVA
      </span>

      <div style={{ flex: 1 }} />

      {/* Window controls */}
      <div
        style={{
          display: 'flex',
          ...NO_DRAG,
        }}
      >
        {/* Minimize */}
        <WinBtn onClick={minimize} hoverColor="rgba(255,255,255,0.08)">
          <svg width="10" height="1" viewBox="0 0 10 1"><rect width="10" height="1" fill="currentColor"/></svg>
        </WinBtn>

        {/* Maximize / Restore */}
        <WinBtn onClick={maximize} hoverColor="rgba(255,255,255,0.08)">
          {isMaximized ? (
            /* Restore icon — two overlapping squares */
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <rect x="2.5" y="0.5" width="7" height="7" stroke="currentColor" strokeWidth="1"/>
              <path d="M0.5 2.5 H1.5 V9.5 H8.5 V8.5" stroke="currentColor" strokeWidth="1"/>
            </svg>
          ) : (
            /* Maximize icon — single square */
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <rect x="0.5" y="0.5" width="9" height="9" stroke="currentColor" strokeWidth="1"/>
            </svg>
          )}
        </WinBtn>

        {/* Close */}
        <WinBtn onClick={close} hoverColor="rgba(220,38,38,0.7)" closeBtn>
          <svg width="10" height="10" viewBox="0 0 10 10">
            <line x1="1" y1="1" x2="9" y2="9" stroke="currentColor" strokeWidth="1.5"/>
            <line x1="9" y1="1" x2="1" y2="9" stroke="currentColor" strokeWidth="1.5"/>
          </svg>
        </WinBtn>
      </div>
    </div>
  )
}

function WinBtn({
  onClick,
  hoverColor,
  closeBtn = false,
  children,
}: {
  onClick: () => void
  hoverColor: string
  closeBtn?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      style={{
        width: 46,
        height: 32,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'transparent',
        border: 'none',
        color: 'var(--text-muted)',
        cursor: 'default',
        transition: 'background 0.1s, color 0.1s',
      }}
      onMouseEnter={e => {
        (e.currentTarget as HTMLButtonElement).style.background = hoverColor
        if (closeBtn) (e.currentTarget as HTMLButtonElement).style.color = '#fff'
      }}
      onMouseLeave={e => {
        (e.currentTarget as HTMLButtonElement).style.background = 'transparent'
        ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)'
      }}
    >
      {children}
    </button>
  )
}
