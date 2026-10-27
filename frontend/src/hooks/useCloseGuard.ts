import { useEffect } from 'react'
import { executionsApi } from '@/api/executions'
import { chatApi } from '@/api/chat'

/**
 * Exposes a tiny query/command surface on window for build/main.js to call via
 * webContents.executeJavaScript() right before the app truly quits (tray "Quit EVA"
 * or the in-app Quit button — the titlebar X only hides, so it doesn't need this).
 *
 * executeJavaScript runs in the page's own JS context, not the preload's isolated
 * world, so this works with contextIsolation:true without any contextBridge/IPC
 * plumbing — main.js just reads window.__evaCloseGuard directly.
 */
export function useCloseGuard() {
  useEffect(() => {
    window.__evaCloseGuard = {
      getRunning: () => executionsApi.listRunning(),
      cancelAll: async (conversationIds) => {
        await Promise.allSettled(conversationIds.map(id => chatApi.cancel(id)))
      },
    }

    return () => { delete window.__evaCloseGuard }
  }, [])
}
