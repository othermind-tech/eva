// Embedded preview pane — a persistent side panel (not a ViewMode; coexists
// with whatever's currently showing rather than replacing it) whose content
// area is intentionally left empty in React. Electron overlays a real
// WebContentsView on top of that empty area at the pixel bounds this
// component reports — see build/main.js's openPreview()/setPreviewBounds()
// and build/cdpRelay.js for why. Electron-only: window.electronAPI is
// undefined in browser/dev mode (e.g. testing via Vite), so this renders a
// clear fallback there instead of a hole with nothing behind it.
//
// A normal flex sibling of <main> in AppShell.tsx, not a fixed overlay —
// each gets flex: 1 so opening the pane genuinely splits the width with the
// chat rather than covering it. Live-tested feedback: an earlier fixed/
// absolute version visually covered the chat instead of sharing space.
import { useEffect, useRef, useState } from 'react'
import { X, Globe, ArrowRight, Loader2 } from 'lucide-react'
import { sessionApi } from '@/api/session'

const PREVIEW_ENDPOINT_NAME = 'preview'   // matches core/mcp/preview_registry.py's hardcoded v1 name

interface Props {
  open:    boolean
  onClose: () => void
}

export default function PreviewPanel({ open, onClose }: Props) {
  const contentRef        = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)   // relay up + endpoint registered with the backend
  const [error, setError] = useState<string | null>(null)
  const [urlInput, setUrlInput] = useState('')
  const hasElectron = typeof window !== 'undefined' && !!window.electronAPI?.previewOpen

  // Open/close lifecycle — register with the backend on open, unregister on
  // close. The renderer (not build/main.js) makes these two calls because it
  // already holds the auth token; main.js only knows the ws:// URL.
  useEffect(() => {
    if (!open || !hasElectron) return
    let cancelled = false

    ;(async () => {
      try {
        const rect = contentRef.current?.getBoundingClientRect()
        const bounds = rect
          ? { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) }
          : { x: 0, y: 0, width: 0, height: 0 }
        const { wsUrl, url } = await window.electronAPI!.previewOpen(bounds)
        if (cancelled) return
        await sessionApi.registerPreviewEndpoint(PREVIEW_ENDPOINT_NAME, wsUrl)
        if (cancelled) return
        // The page itself survives close/reopen (see closePreview() in
        // main.js) — seed the address bar from what's actually loaded rather
        // than leaving it on the empty placeholder, which read as "nothing
        // loaded" while the pane still visibly showed the last page.
        if (url && url !== 'about:blank') setUrlInput(url)
        setReady(true)
        setError(null)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to open preview pane')
      }
    })()

    return () => {
      cancelled = true
      setReady(false)
      void window.electronAPI?.previewClose()
      // Best-effort — if this fails (e.g. app closing), the backend falls
      // back to a standalone browser on the next connection attempt anyway
      // (resolve_connection_args() degrades gracefully with no registration).
      void sessionApi.unregisterPreviewEndpoint(PREVIEW_ENDPOINT_NAME).catch(() => {})
    }
  }, [open, hasElectron])

  // Keep the native view's bounds in sync with this div's actual on-screen
  // rect — window resize, panel resize, or the panel's own content changing
  // (e.g. the URL bar wrapping) can all move it.
  useEffect(() => {
    if (!open || !ready || !hasElectron || !contentRef.current) return

    const report = () => {
      const rect = contentRef.current?.getBoundingClientRect()
      if (!rect) return
      void window.electronAPI?.previewSetBounds({
        x: Math.round(rect.left), y: Math.round(rect.top),
        width: Math.round(rect.width), height: Math.round(rect.height),
      })
    }

    report()
    const ro = new ResizeObserver(report)
    ro.observe(contentRef.current)
    window.addEventListener('resize', report)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', report)
    }
  }, [open, ready, hasElectron])

  // Live-track navigation (the agent's own browser_navigate calls, or
  // anything else that moves the page) rather than only seeding once at open.
  useEffect(() => {
    if (!hasElectron) return
    const onChange = (url: string) => setUrlInput(url)
    window.electronAPI!.onPreviewUrlChanged(onChange)
    return () => window.electronAPI!.offPreviewUrlChanged(onChange)
  }, [hasElectron])

  const handleNavigate = () => {
    const url = urlInput.trim()
    if (!url) return
    const withScheme = /^https?:\/\//i.test(url) ? url : `http://${url}`
    void window.electronAPI?.previewNavigate(withScheme)
  }

  if (!open) return null

  return (
    <div
      style={{
        flex: '1 1 0%', minWidth: 0, height: '100%',
        display: 'flex', flexDirection: 'column',
        background: 'var(--bg-surface1)',
        borderLeft: '1px solid var(--border-mid)',
      }}
    >
      {/* Header — URL bar + close */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderBottom: '1px solid var(--border-dim)', flexShrink: 0 }}>
        <Globe size={15} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
        <input
          value={urlInput}
          onChange={e => setUrlInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleNavigate() }}
          placeholder="localhost:3000"
          disabled={!ready}
          style={{
            flex: 1, minWidth: 0, fontSize: 12, padding: '6px 10px', borderRadius: 6,
            background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)',
            color: 'var(--text-primary)', outline: 'none', fontFamily: '"JetBrains Mono", monospace',
          }}
        />
        <button
          onClick={handleNavigate}
          disabled={!ready || !urlInput.trim()}
          className="icon-btn"
          style={{ width: 28, height: 28, padding: 0, opacity: ready && urlInput.trim() ? 1 : 0.4 }}
          title="Navigate"
        >
          <ArrowRight size={14} />
        </button>
        <button onClick={onClose} className="icon-btn" style={{ width: 28, height: 28, padding: 0 }} title="Close preview pane">
          <X size={15} />
        </button>
      </div>

      {/* Content area — left empty on purpose. Electron overlays the real
          WebContentsView here at contentRef's on-screen bounds; nothing in
          this div is meant to actually be visible once that's active. */}
      <div ref={contentRef} style={{ flex: 1, position: 'relative', background: '#06090F' }}>
        {!hasElectron && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, padding: 24, textAlign: 'center' }}>
            <Globe size={28} style={{ color: 'var(--text-dim)' }} />
            <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: 0 }}>
              The preview pane needs the EVA desktop app — it's not available in the browser dev build.
            </p>
          </div>
        )}
        {hasElectron && error && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, padding: 24, textAlign: 'center' }}>
            <p style={{ fontSize: 12.5, color: 'var(--danger)', margin: 0 }}>{error}</p>
          </div>
        )}
        {hasElectron && !error && !ready && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Loader2 size={20} className="animate-spin" style={{ color: 'var(--text-dim)' }} />
          </div>
        )}
      </div>
    </div>
  )
}
