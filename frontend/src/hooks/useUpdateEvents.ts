import { useEffect } from 'react'
import { useUpdateStore } from '@/store/updateStore'
import { useChatStore } from '@/store/chatStore'

export function useUpdateEvents() {
  const setStatus        = useUpdateStore(s => s.setStatus)
  const setForceWelcome  = useChatStore(s => s.setForceWelcome)
  const setShowWhatsNew  = useChatStore(s => s.setShowWhatsNew)

  useEffect(() => {
    const handleStatus = (payload: { status: string; percent?: number }) => {
      if (payload.status === 'available')        setStatus('downloading', 0)
      else if (payload.status === 'downloading') setStatus('downloading', payload.percent ?? 0)
      else if (payload.status === 'ready')       setStatus('ready', 100)
    }

    // Fresh install — force WelcomePanel regardless of localStorage flag
    const handleInstalled = (_version: string) => {
      setForceWelcome()
    }

    // Update relaunch — route to Welcome first (same mechanism as fresh install)
    // so the What's New modal shows over Welcome instead of over the chat view.
    const handleUpdated = (version: string) => {
      setStatus('idle')
      setForceWelcome()
      setShowWhatsNew(version)
    }

    window.electronAPI?.onUpdateStatus?.(handleStatus)
    window.electronAPI?.onAppInstalled?.(handleInstalled)
    window.electronAPI?.onAppUpdated?.(handleUpdated)

    return () => {
      window.electronAPI?.offUpdateStatus?.(handleStatus)
      window.electronAPI?.offAppInstalled?.(handleInstalled)
      window.electronAPI?.offAppUpdated?.(handleUpdated)
    }
  }, [setStatus, setForceWelcome, setShowWhatsNew])
}
