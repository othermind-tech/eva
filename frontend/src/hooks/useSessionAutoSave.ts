import { useEffect, useRef } from 'react'
import { useSessionStore } from '@/store/sessionStore'
import { sessionApi } from '@/api/session'
import { SUCCESS_FLASH_MS } from '@/lib/constants'

// Single source of truth for persisting sessionStore.config to the backend.
// Mount exactly once, at the app root (ChatPage) — NOT inside a panel. `setConfig()`
// is called from many disconnected places (header Modes widget, WorkspacePanel,
// SessionPanel, HooksPanel), and any of them can flip `isDirty` while none of those
// panels are mounted to catch it. A save effect scoped to a panel component only
// fires while that panel happens to be on screen; changes made elsewhere silently
// never reach the server and get clobbered by the next `replaceConfig()` on reload.
export function useSessionAutoSave(): void {
  const config        = useSessionStore(s => s.config)
  const isDirty        = useSessionStore(s => s.isDirty)
  const setConfig       = useSessionStore(s => s.setConfig)
  const markClean        = useSessionStore(s => s.markClean)
  const setSaveStatus     = useSessionStore(s => s.setSaveStatus)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!isDirty) return
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      setSaveStatus('saving')
      sessionApi.setSession(config)
        .then(data => {
          // Sync back any fields the backend adjusted (e.g. reasoning snapped to nearest supported level)
          if (data.reasoning !== config.reasoning) setConfig({ reasoning: data.reasoning ?? null })
          markClean()
          setSaveStatus('saved')
          if (flashTimerRef.current) clearTimeout(flashTimerRef.current)
          flashTimerRef.current = setTimeout(() => setSaveStatus('idle'), SUCCESS_FLASH_MS)
        })
        .catch(() => {
          // Leave isDirty true — the next config change (or app focus) will retry.
          setSaveStatus('error')
        })
    }, 800)
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, isDirty])

  useEffect(() => () => { if (flashTimerRef.current) clearTimeout(flashTimerRef.current) }, [])
}
