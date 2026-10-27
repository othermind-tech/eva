import { useEffect } from 'react'
import AppShell from '@/components/layout/AppShell'
import ChatView from '@/components/chat/ChatView'
import { sessionApi } from '@/api/session'
import { useSessionStore } from '@/store/sessionStore'
import { useSessionAutoSave } from '@/hooks/useSessionAutoSave'

export default function ChatPage() {
  const replaceConfig = useSessionStore(s => s.replaceConfig)

  // Mounted once for the whole app session — persists every setConfig() call
  // to the backend regardless of which panel (if any) triggered it.
  useSessionAutoSave()

  // B-006 — Fetch full session config from server on app load.
  // This ensures the UI reflects the persisted server-side state rather than
  // stale localStorage defaults after a server restart.
  useEffect(() => {
    sessionApi.getSession()
      .then(res => {
        // Map SessionResponse → SessionConfig (drop extra fields like status, warnings)
        const { status: _s, warnings: _w, ...config } = res
        replaceConfig(config)
      })
      .catch(() => { /* silently fall back to stored config */ })
  }, [replaceConfig])

  return (
    <AppShell>
      <ChatView />
    </AppShell>
  )
}
