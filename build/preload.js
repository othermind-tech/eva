'use strict'

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  openPath:       (filePath) => ipcRenderer.invoke('open-path', filePath),
  getVersion:     ()         => ipcRenderer.invoke('get-version'),
  platform:       process.platform,
  minimizeWindow:      ()       => ipcRenderer.invoke('win-minimize'),
  maximizeWindow:      ()       => ipcRenderer.invoke('win-maximize'),
  closeWindow:         ()       => ipcRenderer.invoke('win-close'),
  onMaximizeChange:    (cb)     => ipcRenderer.on('win-maximize-state', (_e, v) => cb(v)),
  offMaximizeChange:   (cb)     => ipcRenderer.removeListener('win-maximize-state', cb),

  // Auto-updater bridge
  // status arrives as { status: 'available' | 'downloading' | 'ready', percent?: number }
  onUpdateStatus:  (cb) => ipcRenderer.on('update-status', (_e, v) => cb(v)),
  offUpdateStatus: (cb) => ipcRenderer.removeListener('update-status', cb),
  // Call when the user clicks the badge — silently installs and relaunches
  installUpdate:   ()   => ipcRenderer.invoke('install-update'),
  // Fires on first launch after any install where existing user data was found
  // (reinstall keeping data, or silent auto-update) — show What's New modal
  onAppUpdated:    (cb) => ipcRenderer.on('app-updated', (_e, v) => cb(v)),
  offAppUpdated:   (cb) => ipcRenderer.removeListener('app-updated', cb),
  // Fires on first launch after a fresh install or reinstall with data wiped
  // (no eva.db found) — show Welcome screen regardless of localStorage state
  onAppInstalled:  (cb) => ipcRenderer.on('app-installed', (_e, v) => cb(v)),
  offAppInstalled: (cb) => ipcRenderer.removeListener('app-installed', cb),

  // Embedded preview pane (CDP-driven browser automation) — see cdpRelay.js.
  // previewOpen resolves { wsUrl, url }: the caller (PreviewPanel.tsx)
  // registers wsUrl with the backend itself via sessionApi, since this
  // process holds no auth token to do it from the main process; url seeds
  // the address bar with whatever's actually loaded right now (the page
  // survives close/reopen — see closePreview() in main.js — so this can be
  // non-blank even on a "fresh" open).
  previewOpen:       (bounds) => ipcRenderer.invoke('preview-open', bounds),
  previewClose:      ()       => ipcRenderer.invoke('preview-close'),
  previewSetBounds:  (bounds) => ipcRenderer.invoke('preview-set-bounds', bounds),
  previewNavigate:   (url)    => ipcRenderer.invoke('preview-navigate', url),
  // Fires on every navigation while the pane exists, open or not — lets the
  // address bar track reality live instead of only at open time.
  onPreviewUrlChanged:  (cb) => ipcRenderer.on('preview-url-changed', (_e, url) => cb(url)),
  offPreviewUrlChanged: (cb) => ipcRenderer.removeListener('preview-url-changed', cb),

  // ToolCallCard.tsx's screenshot thumbnails — see the 'read-image-file'
  // handler in main.js for why this narrow hole exists.
  readImageFile:     (filePath) => ipcRenderer.invoke('read-image-file', filePath),
})
