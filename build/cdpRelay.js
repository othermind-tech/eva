'use strict'

// ─── CDP relay for the embedded preview pane ───────────────────────────────
//
// Electron's webContents.debugger gives you a PAGE-level CDP session (one
// target, pre-attached) — commands/events for exactly that page's Page,
// Runtime, DOM, Network, etc. domains. Playwright's chromium.connectOverCDP()
// expects a BROWSER-level endpoint: it needs the Target domain (discover
// targets, attach to one, get a sessionId, then operate "flat" within that
// session) before it'll touch anything page-level.
//
// This module is the shim between those two shapes: a small local WebSocket
// server that speaks just enough of the browser-level Target domain to
// satisfy that handshake for exactly ONE fixed target (this pane's page),
// and forwards everything else straight through to webContents.debugger.
//
// This is genuinely the highest-risk piece of the whole preview-pane
// feature — CDP wire-format details here are easy to get subtly wrong, and
// the only way to know for sure is a live connection from the real
// @playwright/mcp subprocess. Logging is intentionally verbose (behind
// EVA_CDP_RELAY_DEBUG) so a live session together has enough to diagnose
// against.
//
// Known v1 limitation: only the pane's single top-level page is exposed as
// a target. A popup or target="_blank" link opened from within the pane
// creates a second, separate webContents that this relay does not surface
// — Playwright would have nothing to attach to for it. Not attempted here.

const { WebSocketServer } = require('./ws-vendor')
const crypto = require('crypto')

const DEBUG = !!process.env.EVA_CDP_RELAY_DEBUG
function log(...args) { if (DEBUG) console.log('[cdp-relay]', ...args) }

/**
 * @param {import('electron').WebContents} webContents  already
 *   `.debugger.attach('1.3')`-ed by the caller; this module never attaches
 *   or detaches the debugger itself, only sends/receives through it.
 * @returns {Promise<{ port: number, wsUrl: string, stop: () => void }>}
 */
async function startCdpRelay(webContents) {
  if (!webContents.debugger.isAttached()) {
    throw new Error('startCdpRelay: webContents.debugger must already be attached')
  }

  // One fixed synthetic target representing this pane's page. targetId is
  // NOT arbitrary, despite CDP's Target domain docs not spelling this out
  // anywhere obvious: for a "page"-type target, real Chrome always sets
  // targetId equal to that page's main frame's own frameId. Playwright's
  // internal Frame-tracking relies on that equality to correlate the
  // Page.frameAttached/frameNavigated events it gets from the session with
  // the Target.attachedToTarget it already has — confirmed live: using an
  // unrelated random UUID here passed the whole attach handshake (1 context,
  // 1 page) but made every subsequent navigation fail immediately with
  // "Frame has been detached", because Playwright's frame model considered
  // the real frame it was hearing about unrelated to the target it attached
  // to. Fixed by asking the real page for its real main-frame id up front,
  // via the one CDP command (Page.getFrameTree) that's answerable before any
  // client has attached or enabled anything.
  const targetId = (await webContents.debugger.sendCommand('Page.getFrameTree')).frameTree.frame.id
  let sessionId = null   // set once Target.attachToTarget has been handled
  let sessionSocket = null // the ws client currently attached to that session

  function targetInfo() {
    return {
      targetId,
      type: 'page',
      title: webContents.getTitle() || '',
      url: webContents.getURL() || 'about:blank',
      attached: sessionId !== null,
      canAccessOpener: false,
      browserContextId: 'eva-preview',
    }
  }

  // ws's WebSocketServer binds its underlying http.Server asynchronously —
  // address() returns null until the 'listening' event fires, so the port
  // below must wait for that rather than reading it immediately.
  const wss = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await new Promise((resolve, reject) => {
    wss.once('listening', resolve)
    wss.once('error', reject)
  })

  // Forward page-level CDP events out to whichever client is attached,
  // tagged with the session they belong to (flat-session framing).
  const onDebuggerMessage = (_event, method, params) => {
    if (!sessionSocket || sessionSocket.readyState !== sessionSocket.OPEN) return
    log('event <-', method)
    sessionSocket.send(JSON.stringify({ method, params, sessionId }))
  }
  webContents.debugger.on('message', onDebuggerMessage)

  // Keep the synthetic targetInfo's title/url roughly current so a client
  // that re-queries Target.getTargetInfo mid-session doesn't see stale data.
  const onNav = () => {
    if (!sessionSocket || sessionSocket.readyState !== sessionSocket.OPEN) return
    sessionSocket.send(JSON.stringify({
      method: 'Target.targetInfoChanged',
      params: { targetInfo: targetInfo() },
    }))
  }
  webContents.on('did-navigate', onNav)
  webContents.on('did-navigate-in-page', onNav)
  webContents.on('page-title-updated', onNav)

  // Idempotent per-socket: returns the existing sessionId if this ws is
  // already attached (setAutoAttach can fire more than once), refuses a
  // second, different client while one is already attached.
  function attachSession(ws) {
    if (sessionSocket === ws) return sessionId
    if (sessionSocket && sessionSocket !== ws) {
      return null   // already attached to a different client — caller decides how to report this
    }
    sessionId     = crypto.randomBytes(16).toString('hex')
    sessionSocket = ws
    ws.send(JSON.stringify({
      method: 'Target.attachedToTarget',
      params: { sessionId, targetInfo: targetInfo(), waitingForDebugger: false },
    }))
    log('attached, sessionId =', sessionId)
    return sessionId
  }

  wss.on('connection', (ws) => {
    log('client connected')

    ws.on('message', async (raw) => {
      let msg
      try {
        msg = JSON.parse(raw.toString())
      } catch (err) {
        log('bad JSON from client, dropping:', err.message)
        return
      }

      const { id, method, params, sessionId: msgSessionId } = msg
      log('cmd ->', method, msgSessionId ? `(session ${msgSessionId})` : '(browser-level)')

      const reply = (result) => {
        ws.send(JSON.stringify({ id, result: result ?? {}, sessionId: msgSessionId }))
      }
      const replyError = (message) => {
        ws.send(JSON.stringify({ id, error: { code: -32000, message }, sessionId: msgSessionId }))
      }

      // ── Session-scoped: forward straight through to the real page ──────
      if (msgSessionId) {
        if (msgSessionId !== sessionId) {
          replyError(`Unknown sessionId: ${msgSessionId}`)
          return
        }
        try {
          const result = await webContents.debugger.sendCommand(method, params || {})
          reply(result)
        } catch (err) {
          replyError(err.message || String(err))
        }
        return
      }

      // ── Browser-level: the handful of Target/Browser methods Playwright
      //    needs before it'll operate within a session ────────────────────
      switch (method) {
        case 'Browser.getVersion':
          reply({
            protocolVersion: '1.3',
            product:         `Chrome/${process.versions.chrome || 'unknown'}`,
            userAgent:       webContents.getUserAgent ? webContents.getUserAgent() : '',
            jsVersion:       process.versions.v8,
          })
          break

        case 'Target.setDiscoverTargets':
          reply({})
          // Announce the one target we have right now, as Target.setDiscoverTargets
          // implies a Target.targetCreated for every existing target.
          if (params?.discover) {
            ws.send(JSON.stringify({
              method: 'Target.targetCreated',
              params: { targetInfo: targetInfo() },
            }))
          }
          break

        case 'Target.setAutoAttach':
          // This, not a manual Target.attachToTarget call, is how modern
          // Playwright's connectOverCDP actually discovers/attaches pages in
          // practice: it turns on auto-attach and expects the BROWSER to
          // proactively emit Target.attachedToTarget for matching targets
          // itself, rather than requesting each one explicitly. Confirmed
          // live — Playwright never sent Target.attachToTarget at all; a
          // relay that only reacted to that call (this one's first version)
          // completed the handshake but produced 0 pages.
          reply({})
          if (params?.autoAttach) {
            const sid = attachSession(ws)
            if (sid === null) log('setAutoAttach: pane already attached to a different client, not re-attaching')
          }
          break

        case 'Target.getBrowserContexts':
          reply({ browserContextIds: ['eva-preview'] })
          break

        case 'Target.getTargets':
          reply({ targetInfos: [targetInfo()] })
          break

        case 'Target.getTargetInfo':
          if (params?.targetId && params.targetId !== targetId) {
            replyError(`Unknown targetId: ${params.targetId}`)
            break
          }
          reply({ targetInfo: targetInfo() })
          break

        case 'Target.attachToTarget':
          // Kept for robustness even though live testing shows Playwright's
          // connectOverCDP doesn't actually call this — see setAutoAttach.
          if (params?.targetId !== targetId) {
            replyError(`Unknown targetId: ${params?.targetId}`)
            break
          }
          {
            const sid = attachSession(ws)
            if (sid === null) replyError('Preview pane already has an attached debugger session')
            else reply({ sessionId: sid })
          }
          break

        case 'Target.detachFromTarget':
          if (sessionSocket === ws) { sessionSocket = null; sessionId = null }
          reply({})
          break

        default:
          // Playwright's connectOverCDP setup issues a handful of other
          // Browser.* calls beyond Browser.getVersion (e.g.
          // setDownloadBehavior, grantPermissions) to configure a browser it
          // assumes it has full control of. None of them have a per-target
          // webContents.debugger equivalent, and none are essential to this
          // relay's one job (proxy Target/session traffic to the pane) — accept
          // and no-op rather than fail the whole connection over them, but log
          // so an unexpected one is still visible during live testing.
          if (method.startsWith('Browser.')) {
            log('accepted no-op for unhandled Browser.* method:', method)
            reply({})
          } else {
            log('unhandled browser-level method:', method)
            replyError(`Not supported by EVA's CDP relay: ${method}`)
          }
      }
    })

    ws.on('close', () => {
      log('client disconnected')
      if (sessionSocket === ws) { sessionSocket = null; sessionId = null }
    })
    ws.on('error', (err) => log('client socket error:', err.message))
  })

  const address = wss.address()
  const port    = address.port
  const wsUrl   = `ws://127.0.0.1:${port}/`
  log('listening at', wsUrl)

  function stop() {
    webContents.debugger.removeListener('message', onDebuggerMessage)
    webContents.removeListener('did-navigate', onNav)
    webContents.removeListener('did-navigate-in-page', onNav)
    webContents.removeListener('page-title-updated', onNav)
    for (const client of wss.clients) client.terminate()
    wss.close()
    log('stopped')
  }

  return { port, wsUrl, stop }
}

module.exports = { startCdpRelay }
