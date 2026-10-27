'use strict'

const { app, BrowserWindow, WebContentsView, ipcMain, shell, Tray, Menu, nativeImage, nativeTheme, dialog, session } = require('electron')
const { autoUpdater } = require('electron-updater')
const { spawn, execFileSync } = require('child_process')
const path = require('path')
const fs = require('fs')
const http = require('http')
const { startCdpRelay } = require('./cdpRelay')

// ─── Constants ────────────────────────────────────────────────────────────────
const IS_WIN        = process.platform === 'win32'
const IS_MAC        = process.platform === 'darwin'
const BACKEND_PORT  = parseInt(process.env.EVA_BACKEND_PORT  || '8000', 10)
const FRONTEND_PORT = parseInt(process.env.EVA_FRONTEND_PORT || '5174', 10)
const CONFIG_DIR    = app.getPath('userData')

// ─── State ────────────────────────────────────────────────────────────────────
let mainWindow     = null
let splashWindow   = null
let tray           = null
let serverProcess  = null
let frontendServer = null
let isQuitting     = false

// Embedded preview pane (CDP-driven browser automation) — see cdpRelay.js
let previewView    = null   // WebContentsView, created lazily on first open
let previewRelay   = null   // { port, wsUrl, stop() } from startCdpRelay
let previewSession = null   // dedicated ephemeral partition, never the app's own

// ─── Path helpers ─────────────────────────────────────────────────────────────
function getBackendBin() {
  if (app.isPackaged) {
    const ext = IS_WIN ? '.exe' : ''
    return path.join(process.resourcesPath, 'backend', `eva-backend${ext}`)
  }
  return null
}

function getFrontendDist() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'frontend_dist')
  }
  return path.join(__dirname, '../frontend/dist')
}

// ─── Static file server (serves React SPA on localhost:5174) ─────────────────
function serveFrontend() {
  const distPath = getFrontendDist()
  const MIME = {
    '.html':  'text/html',
    '.js':    'application/javascript',
    '.mjs':   'application/javascript',
    '.css':   'text/css',
    '.png':   'image/png',
    '.jpg':   'image/jpeg',
    '.webp':  'image/webp',
    '.svg':   'image/svg+xml',
    '.ico':   'image/x-icon',
    '.woff':  'font/woff',
    '.woff2': 'font/woff2',
    '.ttf':   'font/ttf',
    '.eot':   'application/vnd.ms-fontobject',
    '.json':  'application/json',
    '.md':    'text/markdown',
  }

  return new Promise((resolve, reject) => {
    frontendServer = http.createServer((req, res) => {
      const urlPath = req.url.split('?')[0]

      // Proxy /api/* → backend on port 8000 (matches Vite dev proxy behaviour)
      if (urlPath.startsWith('/api')) {
        const backendPath = req.url.slice(4) // strip /api prefix
        const options = {
          hostname: '127.0.0.1',
          port:     BACKEND_PORT,
          path:     backendPath,
          method:   req.method,
          headers:  { ...req.headers, host: `127.0.0.1:${BACKEND_PORT}` },
        }
        const proxyReq = http.request(options, proxyRes => {
          // Strip hop-by-hop headers before forwarding to the browser.
          // Node.js's HTTP client automatically decodes Transfer-Encoding:chunked,
          // so forwarding that header while piping decoded data causes the browser
          // to mis-parse the stream (critical for SSE / text/event-stream).
          const HOP_BY_HOP = new Set([
            'transfer-encoding', 'connection', 'keep-alive',
            'te', 'trailers', 'upgrade', 'proxy-authenticate', 'proxy-authorization',
          ])
          const headers = {}
          for (const [k, v] of Object.entries(proxyRes.headers)) {
            if (!HOP_BY_HOP.has(k.toLowerCase())) headers[k] = v
          }
          res.writeHead(proxyRes.statusCode, headers)
          // Flush headers immediately so the browser's fetch() resolves without
          // waiting for the first data chunk (important for SSE connections).
          res.flushHeaders()
          proxyRes.pipe(res)
        })
        // Disable Nagle's algorithm on the proxy→backend socket so SSE chunks
        // are forwarded to Node.js immediately without TCP coalescing delay.
        // Without this, Chromium's delayed-ACK (200-500ms) interacts with
        // Nagle's algorithm on both sockets to stall SSE streams indefinitely.
        proxyReq.on('socket', socket => {
          socket.setNoDelay(true)
          socket.setTimeout(0)
        })
        proxyReq.on('error', err => {
          if (!res.headersSent) {
            res.writeHead(502, { 'Content-Type': 'text/plain' })
          }
          res.end(`Backend unavailable: ${err.message}`)
        })
        req.pipe(proxyReq)
        return
      }

      let filePath = path.join(distPath, urlPath)

      // SPA fallback — return index.html for any path that isn't a real file
      try {
        const stat = fs.statSync(filePath)
        if (stat.isDirectory()) filePath = path.join(distPath, 'index.html')
      } catch {
        filePath = path.join(distPath, 'index.html')
      }

      const ext = path.extname(filePath).toLowerCase()
      const contentType = MIME[ext] || 'application/octet-stream'

      fs.readFile(filePath, (err, data) => {
        if (err) {
          res.writeHead(404, { 'Content-Type': 'text/plain' })
          res.end('Not found')
          return
        }
        res.writeHead(200, {
          'Content-Type': contentType,
          // .md files must never be cached — Chromium's disk cache persists across
          // app updates, so a stale CHANGELOG.md from an older version would be
          // served instead of the newly bundled one, breaking the What's New modal.
          // JS/CSS/fonts are content-hashed by Vite so long caching is safe there.
          'Cache-Control': (ext === '.html' || ext === '.md') ? 'no-store' : 'max-age=31536000',
        })
        res.end(data)
      })
    })

    // Disable Nagle's algorithm on every browser→proxy socket.
    // Nagle + Chromium's delayed ACK creates a "double whammy" that stalls
    // small SSE chunks (pings, text deltas) causing the 60s WorkerDown timeout.
    frontendServer.on('connection', socket => {
      socket.setNoDelay(true)
      socket.setTimeout(0)   // never time out long-lived SSE connections
    })
    frontendServer.on('error', reject)
    frontendServer.listen(FRONTEND_PORT, '127.0.0.1', () => {
      console.log(`[frontend] Serving at http://127.0.0.1:${FRONTEND_PORT}`)
      resolve()
    })
  })
}

// ─── Content-Security-Policy ────────────────────────────────────────────────────
// Defense-in-depth backstop, not an active-vulnerability fix (no XSS path found in
// audit) — EVA renders LLM-produced Markdown from multiple providers, so a CSP
// blocks any future injected/inline <script> or eval() from actually executing.
// img-src allows any https: host since Markdown image syntax (![alt](url)) can
// legitimately point at an external provider-returned URL — that's existing,
// intended behavior, not something this policy is meant to restrict.
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "img-src 'self' data: https:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ')

function setupCSP() {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [CONTENT_SECURITY_POLICY],
      },
    })
  })
}

// ─── Splash window ────────────────────────────────────────────────────────────
function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 440,
    height: 320,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, 'splash-preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  splashWindow.loadFile(path.join(__dirname, 'splash.html'))
  splashWindow.center()
}

function updateSplash(message, percent) {
  splashWindow?.webContents.send('status-update', { message, percent })
}

// ─── Main window ──────────────────────────────────────────────────────────────
function createMainWindow() {
  Menu.setApplicationMenu(null)   // removes the File/Edit/View/Help menu bar
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    show: false,
    backgroundColor: '#06090F',
    frame: false,
    titleBarStyle: IS_MAC ? 'hiddenInset' : 'hidden',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
    },
  })

  mainWindow.loadURL(`http://127.0.0.1:${FRONTEND_PORT}`)

  mainWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close()
      splashWindow = null
    }
    mainWindow.show()
    mainWindow.focus()

    // Detect install type from the marker written by NSIS customInstall.
    // Fires on every install: fresh, reinstall, and silent auto-upgrade.
    // eva.db existence distinguishes first-ever install from returning user:
    //   no DB  -> app-installed  -> frontend shows Welcome screen
    //   DB exists -> app-updated -> frontend shows What's New modal
    // Delay 1s so React has time to mount and register IPC listeners.
    const freshInstallFile = path.join(CONFIG_DIR, 'fresh-install')
    if (fs.existsSync(freshInstallFile)) {
      try {
        const version = fs.readFileSync(freshInstallFile, 'utf8').trim()
        fs.unlinkSync(freshInstallFile)
        const isFirstEver = !fs.existsSync(path.join(CONFIG_DIR, 'eva.db'))
        setTimeout(() => {
          if (isFirstEver) {
            mainWindow?.webContents.send('app-installed', version)
          } else {
            mainWindow?.webContents.send('app-updated', version)
          }
        }, 1000)
      } catch (err) {
        console.error('[install] fresh-install read error:', err.message)
      }
    }

    // Start checking for updates 3s after the window is shown so the renderer
    // is fully loaded before any update-status events arrive.
    setTimeout(setupAutoUpdater, 3000)
  })

  mainWindow.on('maximize',   () => mainWindow.webContents.send('win-maximize-state', true))
  mainWindow.on('unmaximize', () => mainWindow.webContents.send('win-maximize-state', false))

  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault()
      mainWindow.hide()
    }
  })

  mainWindow.webContents.on('did-fail-load', () => {
    setTimeout(() => mainWindow.loadURL(`http://127.0.0.1:${FRONTEND_PORT}`), 2000)
  })
}

// ─── Embedded preview pane (CDP-driven browser automation) ────────────────────
// The agent's Playwright browser attaches to this view via a CDP relay
// (cdpRelay.js) instead of spawning its own separate OS window, so the user
// watches it navigate/click/test live, in-app. This is the Electron half of
// the feature; the backend half is backend/core/mcp/preview_registry.py.
//
// Registering/unregistering the CDP endpoint with the backend
// (POST/DELETE /session/preview-endpoints) is deliberately NOT done here —
// this process has no auth token. openPreview()/closePreview() return the
// wsUrl (or nothing) to the renderer, which already holds the token and
// makes that call itself via sessionApi.
function getPreviewSession() {
  if (!previewSession) {
    // Fresh, non-persistent partition — never the app's own default session.
    // Two independent reasons this matters, not just one:
    //   1. setupCSP() locks the app's own session down to `script-src 'self'`
    //      etc., which would break virtually any real dev-server page loaded
    //      here (it needs to run its own scripts, fetch its own APIs...).
    //      onHeadersReceived is only wired to session.defaultSession, so a
    //      separate session skips it entirely rather than needing a filter.
    //   2. Isolation from the user's real EVA session/cookies — same spirit
    //      as playwright.yaml's own --isolated flag for the standalone-
    //      browser fallback. "eva-preview" (no "persist:" prefix) is
    //      in-memory only and gone when the session object is GC'd.
    previewSession = session.fromPartition('eva-preview')
  }
  return previewSession
}

function ensurePreviewView() {
  if (previewView) return previewView
  previewView = new WebContentsView({
    webPreferences: {
      session:          getPreviewSession(),
      contextIsolation: true,
      nodeIntegration:  false,
      sandbox:          true,
      spellcheck:       false,
    },
  })
  previewView.setBackgroundColor('#06090F')
  mainWindow.contentView.addChildView(previewView)

  // Pushed to the renderer so PreviewPanel.tsx's URL bar reflects what's
  // actually loaded — including after a close/reopen, where the page itself
  // deliberately isn't reset (see closePreview()) but the URL bar was
  // showing its empty placeholder regardless, which read as "nothing is
  // loaded" while the pane still visibly had the last page in it.
  const pushUrl = () => mainWindow?.webContents.send('preview-url-changed', previewView.webContents.getURL())
  previewView.webContents.on('did-navigate', pushUrl)
  previewView.webContents.on('did-navigate-in-page', pushUrl)

  return previewView
}

// bounds: {x, y, width, height} in the main window's content-area pixel
// coordinates — the renderer reports this (ResizeObserver on the panel's own
// DOM element) since the native view has no idea where React laid it out.
async function openPreview(bounds) {
  const view = ensurePreviewView()
  if (bounds) view.setBounds(bounds)
  if (view.webContents.getURL() === '') view.webContents.loadURL('about:blank')

  if (!previewRelay) {
    if (!view.webContents.debugger.isAttached()) {
      view.webContents.debugger.attach('1.3')
    }
    previewRelay = await startCdpRelay(view.webContents)
  }
  return { wsUrl: previewRelay.wsUrl, url: view.webContents.getURL() }
}

function setPreviewBounds(bounds) {
  if (previewView && bounds) previewView.setBounds(bounds)
}

async function navigatePreview(url) {
  if (!previewView) throw new Error('Preview pane is not open')
  await previewView.webContents.loadURL(url)
}

// Stops the relay and detaches the debugger, but keeps the WebContentsView
// itself around (still attached to the window, just given empty bounds) —
// cheaper to hide than to fully tear down and rebuild on every open/close,
// and matches how the renderer treats every other panel (mount once, toggle
// visibility). Full destruction only happens at app quit.
function closePreview() {
  if (previewRelay) { previewRelay.stop(); previewRelay = null }
  if (previewView?.webContents.debugger.isAttached()) {
    try { previewView.webContents.debugger.detach() } catch { /* already gone */ }
  }
  if (previewView) previewView.setBounds({ x: 0, y: 0, width: 0, height: 0 })
}

function destroyPreview() {
  closePreview()
  if (previewView) {
    mainWindow?.contentView.removeChildView(previewView)
    previewView.webContents.close()
    previewView = null
  }
}

// ─── System tray ──────────────────────────────────────────────────────────────
// The tray icon is thin line-art, so it needs white lines on a dark taskbar and black
// lines on a light one. The taskbar theme is its own Windows setting (it can differ from
// the app theme), so read it from the registry and fall back to Electron's app theme.
function taskbarIsDark() {
  try {
    const out = execFileSync('reg', [
      'query', 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize',
      '/v', 'SystemUsesLightTheme',
    ], { encoding: 'utf8', windowsHide: true, timeout: 2000 })
    const m = out.match(/SystemUsesLightTheme\s+REG_DWORD\s+0x([0-9a-f]+)/i)
    if (m) return parseInt(m[1], 16) === 0
  } catch { /* key missing (older Windows) or reg unavailable */ }
  return nativeTheme.shouldUseDarkColors
}

// Hand-sized 16/20/24/32 px images (assets/tray-*.png) for 100/125/150/200 % display
// scaling. Loaded as buffers so it also works from inside the asar archive.
function trayIcon() {
  const name = taskbarIsDark() ? 'tray-white' : 'tray-black'
  const image = nativeImage.createEmpty()
  for (const [suffix, scaleFactor] of [['', 1], ['@1.25x', 1.25], ['@1.5x', 1.5], ['@2x', 2]]) {
    const file = path.join(__dirname, 'assets', `${name}${suffix}.png`)
    if (fs.existsSync(file)) image.addRepresentation({ scaleFactor, buffer: fs.readFileSync(file) })
  }
  return image
}

function createTray() {
  tray = new Tray(trayIcon())
  // Follow theme changes while running; also refresh on click, because a taskbar-only
  // theme switch does not always raise an Electron theme event.
  const refreshTrayIcon = () => { if (tray && !tray.isDestroyed()) tray.setImage(trayIcon()) }
  nativeTheme.on('updated', refreshTrayIcon)
  tray.on('right-click', refreshTrayIcon)
  const menu = Menu.buildFromTemplate([
    { label: 'Open EVA',  click: () => { mainWindow?.show(); mainWindow?.focus() } },
    { type: 'separator' },
    { label: 'Quit EVA', click: () => { handleCloseAttempt() } },
  ])
  tray.setToolTip('EVA')
  tray.setContextMenu(menu)
  tray.on('click', () => { refreshTrayIcon(); mainWindow?.show(); mainWindow?.focus() })
}

// ─── App-close cancellation guard ──────────────────────────────────────────────
// True-quit paths only (tray "Quit EVA" and the in-app Quit button) — the
// titlebar X just hides to tray and doesn't kill anything, so it's unaffected.
// Reads/commands window.__evaCloseGuard (set up by the renderer's useCloseGuard
// hook) via executeJavaScript, which runs in the page's own JS context and so
// works with contextIsolation:true without any preload/IPC plumbing.
async function handleCloseAttempt() {
  let running = []
  try {
    running = await Promise.race([
      mainWindow.webContents.executeJavaScript('window.__evaCloseGuard ? window.__evaCloseGuard.getRunning() : []'),
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 2000)),
    ])
  } catch (err) {
    // Renderer unresponsive/crashed, or the query itself failed — nothing
    // known to be running, so don't hold the quit hostage to it.
    console.error('[close-guard] getRunning failed:', err.message)
    running = []
  }

  if (!Array.isArray(running) || running.length === 0) {
    isQuitting = true
    app.quit()
    return
  }

  const { response } = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    title: 'Task in Progress',
    message: `EVA is running ${running.length} task${running.length > 1 ? 's' : ''}.`,
    detail: 'Quitting without stopping may leave the task incomplete.',
    buttons: ['Stop and Quit', 'Keep Running (minimize instead)', 'Cancel'],
    defaultId: 0,
    cancelId: 2,
  })

  if (response === 0) {
    try {
      const conversationIds = running.map(r => r.conversation_id)
      await Promise.race([
        mainWindow.webContents.executeJavaScript(
          `window.__evaCloseGuard ? window.__evaCloseGuard.cancelAll(${JSON.stringify(conversationIds)}) : Promise.resolve()`
        ),
        new Promise(resolve => setTimeout(resolve, 6000)),  // don't hold quit hostage to a stuck cancel
      ])
    } catch (err) {
      console.error('[close-guard] cancelAll failed:', err.message)
    }
    isQuitting = true
    app.quit()
  } else if (response === 1) {
    mainWindow.hide()
  }
  // response === 2 (Cancel, or Esc/dialog dismissed) — do nothing: window
  // stays open exactly as it was, nothing gets cancelled, quit is aborted.
}

// ─── Backend helpers ──────────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

function spawnBackend(onExit) {
  const bin = getBackendBin()
  if (!bin) {
    console.log('[DEV] Skipping backend spawn — dev mode')
    return null
  }

  // Prepend torch/internal DLL dirs to PATH so PyInstaller bundles resolve
  let envPath = process.env.PATH || ''
  if (IS_WIN) {
    const internalDir = path.join(path.dirname(bin), '_internal')
    const extra = [
      path.join(internalDir, 'torch', 'lib'),
      path.join(internalDir, 'torch', 'bin'),
      internalDir,
    ].filter(d => fs.existsSync(d))
    if (extra.length) envPath = extra.join(path.delimiter) + path.delimiter + envPath
  }

  const env = {
    ...process.env,
    PATH:                    envPath,
    EVA_CONFIG_DIR:          CONFIG_DIR,
    PYTHONUNBUFFERED:        '1',
    PYTHONDONTWRITEBYTECODE: '1',
    // Allow sentence_transformers model to download on first RAG use
    HF_HUB_OFFLINE:          '0',
    TRANSFORMERS_OFFLINE:    '0',
    // Store DB in AppData/Roaming/EVA — survives reinstalls
    DATABASE_URL:            `sqlite+aiosqlite:///${CONFIG_DIR.replace(/\\/g, '/')}/eva.db`,
    // Allow CORS from the React static server
    CORS_ORIGINS:            `http://127.0.0.1:${FRONTEND_PORT}`,
    // Point tiktoken to bundled BPE cache — avoids internet download on first run
    TIKTOKEN_CACHE_DIR:      path.join(path.dirname(bin), '_internal', 'tiktoken_cache'),
    // Write backend logs to AppData/Roaming/EVA/logs/ — survives reinstalls
    EVA_LOG_DIR:             path.join(CONFIG_DIR, 'logs'),
  }

  const cwd  = path.dirname(bin)
  const proc = spawn(bin, [], { env, cwd, stdio: ['ignore', 'pipe', 'pipe'] })

  proc.stdout?.on('data', d => console.log(`[server] ${d.toString().trimEnd()}`))
  proc.stderr?.on('data', d => console.error(`[server] ${d.toString().trimEnd()}`))
  proc.on('exit', (code, signal) => {
    console.log(`[server] exited — code=${code} signal=${signal}`)
    if (!isQuitting) onExit?.(code)
  })
  return proc
}

async function waitForBackend(timeoutMs = 30000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    const ok = await new Promise(resolve => {
      const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, res => {
        resolve(res.statusCode === 200)
      })
      req.on('error', () => resolve(false))
      req.setTimeout(1000, () => { req.destroy(); resolve(false) })
    })
    if (ok) return true
    await sleep(1000)
  }
  return false
}

async function stopBackend(timeoutMs = 6000) {
  if (!serverProcess || serverProcess.exitCode !== null) return

  // Ask the backend to sweep background processes and shut down gracefully.
  // Uses plain http (already required) with a 3 s timeout -- any failure
  // (backend dead, not yet up, network error) falls through to the SIGTERM
  // safety net below without blocking the quit.
  await new Promise(resolve => {
    try {
      const req = http.request(
        { hostname: '127.0.0.1', port: BACKEND_PORT, path: '/internal/shutdown', method: 'POST', timeout: 3000 },
        res => { res.resume(); resolve() }
      )
      req.on('error', resolve)
      req.on('timeout', () => { req.destroy(); resolve() })
      req.end()
    } catch { resolve() }
  })

  // Safety net: SIGTERM -> SIGKILL-after-timeout, unchanged.
  return new Promise(resolve => {
    if (!serverProcess || serverProcess.exitCode !== null) { resolve(); return }
    const timer = setTimeout(() => {
      try { serverProcess.kill('SIGKILL') } catch {}
      resolve()
    }, timeoutMs)
    serverProcess.once('exit', () => { clearTimeout(timer); resolve() })
    try { serverProcess.kill('SIGTERM') } catch {}
  })
}

// ─── Auto-updater ─────────────────────────────────────────────────────────────
function setupAutoUpdater() {
  if (!app.isPackaged) return  // no update checks in dev mode

  autoUpdater.autoDownload         = true   // download silently in background
  autoUpdater.autoInstallOnAppQuit = false  // we control install via badge click

  autoUpdater.on('update-available', () => {
    console.log('[updater] update available — downloading in background')
    mainWindow?.webContents.send('update-status', { status: 'available' })
  })

  autoUpdater.on('download-progress', (progress) => {
    mainWindow?.webContents.send('update-status', {
      status:  'downloading',
      percent: Math.round(progress.percent),
    })
  })

  autoUpdater.on('update-downloaded', () => {
    console.log('[updater] update downloaded — ready to install')
    mainWindow?.webContents.send('update-status', { status: 'ready' })
  })

  autoUpdater.on('update-not-available', () => {
    console.log('[updater] app is up to date')
  })

  autoUpdater.on('error', (err) => {
    console.error('[updater] error:', err.message)
  })

  autoUpdater.checkForUpdates()
}

// ─── Startup ──────────────────────────────────────────────────────────────────
async function startup() {
  setupCSP()
  createSplashWindow()
  await sleep(300)

  fs.mkdirSync(CONFIG_DIR, { recursive: true })

  try {
    // Step 1 — Serve React frontend
    updateSplash('Loading interface...', 15)
    await serveFrontend()

    // Step 2 — Start Python backend
    updateSplash('Starting EVA server...', 35)
    serverProcess = spawnBackend((code) => {
      if (!isQuitting) {
        dialog.showErrorBox(
          'EVA Server Stopped',
          `The backend server exited unexpectedly (code ${code}).\n\nPlease restart EVA.`
        )
      }
    })

    // Step 3 — Wait for backend to be ready
    updateSplash('Waiting for EVA to be ready... (first launch may take ~60s)', 55)
    const ready = await waitForBackend(180000)
    if (!ready) {
      dialog.showErrorBox(
        'Startup Failed',
        `EVA server did not respond on port ${BACKEND_PORT}.\n\nIf another application is already using port ${BACKEND_PORT}, close it and restart EVA.\n\nTo use a different port, set the EVA_BACKEND_PORT environment variable before launching.`
      )
      app.quit(); return
    }

    // Step 4 — Show the app
    updateSplash('Launching EVA...', 95)
    await sleep(400)
    createMainWindow()
    createTray()

  } catch (err) {
    console.error('Startup error:', err)
    dialog.showErrorBox('Startup Error', String(err))
    app.quit()
  }
}

// ─── IPC ──────────────────────────────────────────────────────────────────────
ipcMain.handle('open-path',    async (_e, p) => shell.showItemInFolder(p))
ipcMain.handle('get-version',  ()             => app.getVersion())
ipcMain.handle('win-minimize', ()             => mainWindow?.minimize())
ipcMain.handle('win-maximize', ()             => { mainWindow?.isMaximized() ? mainWindow.unmaximize() : mainWindow?.maximize() })
ipcMain.handle('win-close',    ()             => handleCloseAttempt())

// Silent install + relaunch.
// NSIS customInstall macro writes the fresh-install marker on every run,
// so main.js will detect the update on next launch without any extra work here.
ipcMain.handle('install-update', () => {
  autoUpdater.quitAndInstall(true, true)  // silent NSIS (/S) + force relaunch
})

// Preview pane — see the "Embedded preview pane" section above.
// preview-open returns { wsUrl } so the renderer can register it with the
// backend itself (this process holds no auth token); preview-close does not
// unregister anything on its own for the same reason — the renderer is
// expected to call sessionApi.unregisterPreviewEndpoint() around this.
ipcMain.handle('preview-open',        (_e, bounds) => openPreview(bounds))
ipcMain.handle('preview-close',       ()            => closePreview())
ipcMain.handle('preview-set-bounds',  (_e, bounds) => setPreviewBounds(bounds))
ipcMain.handle('preview-navigate',    (_e, url)    => navigatePreview(url))

// Reads a local image file (e.g. a Playwright screenshot under a
// conversation's _browser_output/ dir) as a data: URL, for ToolCallCard.tsx's
// screenshot thumbnails — the renderer has no filesystem access of its own
// (nodeIntegration: false), so this is the one narrow hole for it, scoped to
// image extensions only.
const IMAGE_EXT_MIME = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' }
ipcMain.handle('read-image-file', async (_e, filePath) => {
  const ext = path.extname(filePath).toLowerCase()
  const mime = IMAGE_EXT_MIME[ext]
  if (!mime) throw new Error(`Not a supported image type: ${filePath}`)
  const data = await fs.promises.readFile(filePath)
  return `data:${mime};base64,${data.toString('base64')}`
})

// ─── Lifecycle ────────────────────────────────────────────────────────────────
app.whenReady().then(startup)

let shutdownInProgress = false
app.on('before-quit', (event) => {
  if (shutdownInProgress) return
  shutdownInProgress = true
  isQuitting = true
  event.preventDefault()

  destroyPreview()
  frontendServer?.close()
  stopBackend(6000).then(() => app.exit(0))
})

app.on('window-all-closed', () => { if (!IS_MAC) { if (isQuitting) app.quit() } })
app.on('activate', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus() } })
