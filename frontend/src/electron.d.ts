// Type declarations for the Electron preload bridge.
// In browser/dev mode window.electronAPI is undefined — all call sites guard with `?.`

type UpdateStatusPayload = {
  status:   'available' | 'downloading' | 'ready'
  percent?: number
}

type PreviewBounds = { x: number; y: number; width: number; height: number }

interface Window {
  electronAPI?: {
    openPath:         (filePath: string) => Promise<void>
    getVersion:       () => Promise<string>
    platform:         string
    minimizeWindow:   () => Promise<void>
    maximizeWindow:   () => Promise<void>
    closeWindow:      () => Promise<void>
    onMaximizeChange: (cb: (isMaximized: boolean) => void) => void
    offMaximizeChange:(cb: (isMaximized: boolean) => void) => void
    onUpdateStatus:   (cb: (payload: UpdateStatusPayload) => void) => void
    offUpdateStatus:  (cb: (payload: UpdateStatusPayload) => void) => void
    installUpdate:    () => void
    onAppInstalled:   (cb: (version: string) => void) => void
    offAppInstalled:  (cb: (version: string) => void) => void
    onAppUpdated:     (cb: (version: string) => void) => void
    offAppUpdated:    (cb: (version: string) => void) => void
    // Embedded preview pane (CDP-driven browser automation). Electron-only —
    // window.electronAPI is undefined in browser/dev mode, so PreviewPanel.tsx
    // must render an unavailable state there rather than assume this exists.
    previewOpen:       (bounds: PreviewBounds) => Promise<{ wsUrl: string; url: string }>
    previewClose:      () => Promise<void>
    previewSetBounds:  (bounds: PreviewBounds) => Promise<void>
    previewNavigate:   (url: string) => Promise<void>
    onPreviewUrlChanged:  (cb: (url: string) => void) => void
    offPreviewUrlChanged: (cb: (url: string) => void) => void
    readImageFile:     (filePath: string) => Promise<string>   // resolves a data: URL
  }
  // Read directly from the page's own window via webContents.executeJavaScript()
  // in main.js — not routed through the contextBridge/preload isolated world,
  // since executeJavaScript targets the page context regardless of contextIsolation.
  __evaCloseGuard?: {
    getRunning: () => Promise<{ conversation_id: string; execution_id: string; execution_mode: string; status: string; started_at?: string | null }[]>
    cancelAll:  (conversationIds: string[]) => Promise<void>
  }
}
