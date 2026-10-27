'use strict'

// ─── Automated CDP-relay test (Tier A) ─────────────────────────────────────
//
// What this proves, fully automated, no human, no @playwright/mcp subprocess:
// that build/cdpRelay.js's hand-written CDP Target-domain shim genuinely
// satisfies a REAL, independent Playwright client's connectOverCDP handshake
// against the REAL Electron app — not a mock of either side.
//
// Launches the actual EVA Electron app (build/), drives its real renderer to
// open the preview pane (same click path a user would take), reads the
// registered ws:// endpoint back from the real backend (GET
// /session/preview-endpoints — same one build/main.js's IPC handler and
// PreviewPanel.tsx use), then opens a SECOND, wholly separate Playwright
// connection to that endpoint via chromium.connectOverCDP() — exactly what
// @playwright/mcp does internally, just without the npx subprocess wrapper
// around it. A round-trip that correctly reflects a real navigation and real
// page content can only happen if the relay is genuinely proxying to the
// real embedded webContents, not just answering the handshake and stopping.
//
// What this deliberately does NOT test:
// the real @playwright/mcp subprocess, the backend's resync-on-register
// orchestration end to end, or anything about how the pane actually looks.
//
// Requires: the real backend already running on 127.0.0.1:8000 (this script
// does not start one), and frontend/dist built fresh (npm run build in
// frontend/) so the Electron shell serves today's PreviewPanel.tsx.
//
// Run: node tests/electron/test_preview_relay.js   (from the repo root; needs `npm ci` in build/,
// where playwright is a devDependency, and a built Electron app in build/)

const path = require('path')
const assert = require('assert')

// The app under test lives in build/; playwright is one of its devDependencies, so resolve it from there.
const BUILD_DIR = path.join(__dirname, '..', '..', 'build')
const { _electron: electron, chromium } = require(require.resolve('playwright', { paths: [BUILD_DIR] }))

const BACKEND = 'http://127.0.0.1:8000'
const USERNAME = process.env.EVA_TEST_USER || 'test'
const PASSWORD = process.env.EVA_TEST_PASS || 'test'

function log(...args) { console.log('[test]', ...args) }

async function backendLogin() {
  const form = new URLSearchParams({ username: USERNAME, password: PASSWORD })
  const res = await fetch(`${BACKEND}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
  })
  if (!res.ok) throw new Error(`backend login failed: ${res.status} ${await res.text()}`)
  return res.json()   // { access_token, refresh_token, expires_in, ... }
}

async function getRegisteredPreviewEndpoint(token) {
  const res = await fetch(`${BACKEND}/session/preview-endpoints`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error(`GET /session/preview-endpoints failed: ${res.status}`)
  const { endpoints } = await res.json()
  return endpoints.preview ?? null
}

async function waitFor(fn, { timeoutMs = 15000, intervalMs = 300, label = 'condition' } = {}) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    const result = await fn()
    if (result) return result
    await new Promise(r => setTimeout(r, intervalMs))
  }
  throw new Error(`Timed out waiting for: ${label}`)
}

async function main() {
  let electronApp
  let cdpBrowser

  try {
    // ── 0. Clear any stale registration left by an earlier crashed run ──
    // preview_registry is in-memory and tied to the BACKEND process's
    // lifetime, not this test's Electron process — a run that throws before
    // reaching step 7's cleanup leaves its (now-dead) endpoint registered
    // indefinitely. Confirmed live: without this, a fresh run's poll below
    // can match that stale entry before the new one overwrites it, and then
    // connectOverCDP fails with ECONNREFUSED against a port nothing is
    // listening on anymore.
    try {
      const { access_token: cleanupToken } = await backendLogin()
      await fetch(`${BACKEND}/session/preview-endpoints/preview`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${cleanupToken}` },
      })
    } catch { /* backend may not be reachable yet on a cold start — fine, nothing to clear */ }

    // ── 1. Launch the real app ──────────────────────────────────────────
    log('launching Electron app...')
    electronApp = await electron.launch({
      args: ['.'], cwd: BUILD_DIR,
      env: { ...process.env, EVA_CDP_RELAY_DEBUG: '1' },
    })
    electronApp.process().stdout.on('data', d => process.stdout.write(`[electron] ${d}`))
    electronApp.process().stderr.on('data', d => process.stderr.write(`[electron] ${d}`))

    const window = await waitFor(async () => {
      for (const w of electronApp.windows()) {
        if (w.url().includes(':5174')) return w
      }
      return null
    }, { timeoutMs: 60000, label: 'main window loaded' })
    log('main window found:', window.url())
    await window.waitForLoadState('domcontentloaded')
    window.on('console', msg => { if (msg.type() === 'error') console.error('[renderer console]', msg.text()) })

    // ── 2. Log in through the real UI (skip if a persisted session from an
    //      earlier run is still valid — Electron's default session storage
    //      survives between launches, unlike the dedicated ephemeral
    //      eva-preview partition) ─────────────────────────────────────────
    const alreadyAuthed = await window.locator('button').filter({ hasText: 'Preview' }).count() > 0
    if (alreadyAuthed) {
      log('session already authenticated from a previous run — skipping login UI')
    } else {
      const usernameInput = window.getByPlaceholder('your_username')
      await usernameInput.waitFor({ timeout: 20000 })
      await usernameInput.fill(USERNAME)
      await window.locator('input[type="password"]').fill(PASSWORD)
      await window.locator('button[type="submit"]').getByText('Sign In').click()
      await waitFor(async () => {
        const err = await window.locator('text=Network Error').count()
        if (err > 0) throw new Error('Login failed: "Network Error" shown in UI')
        return (await window.locator('button').filter({ hasText: 'Preview' }).count()) > 0
      }, { timeoutMs: 20000, label: 'post-login UI (NavRail visible)' })
      log('logged in through the real UI')
    }
    const { access_token: token } = await backendLogin()   // separate token, for the test's own backend checks

    // ── 3. Open the preview pane via the real NavRail button ───────────
    const previewBtn = window.locator('button').filter({ hasText: 'Preview' }).first()
    await previewBtn.waitFor({ timeout: 15000 })
    await previewBtn.click()
    log('clicked Preview')

    // ── 4. Confirm the backend actually got the registration ───────────
    const entry = await waitFor(
      () => getRegisteredPreviewEndpoint(token),
      { timeoutMs: 15000, label: 'preview endpoint registered with backend' },
    )
    assert.ok(entry.cdp_endpoint.startsWith('ws://127.0.0.1:'), `unexpected cdp_endpoint shape: ${entry.cdp_endpoint}`)
    log('backend confirms registration:', entry.cdp_endpoint)

    // ── 5. A SEPARATE, independent Playwright client attaches over CDP ──
    log('connecting a fresh Playwright client via connectOverCDP...')
    cdpBrowser = await chromium.connectOverCDP(entry.cdp_endpoint)
    const contexts = cdpBrowser.contexts()
    assert.strictEqual(contexts.length, 1, `expected exactly 1 context, got ${contexts.length}`)
    const pages = contexts[0].pages()
    assert.strictEqual(pages.length, 1, `expected exactly 1 page, got ${pages.length}`)
    const previewPage = pages[0]
    log('attached — got 1 context, 1 page, as the relay promises')

    // ── 6. Round-trip proof: navigate via CDP, verify real page state ──
    const marker = `EVA-relay-test-${Date.now()}`
    log('calling page.goto (data: URL) first, to isolate from setContent...')
    await previewPage.goto(`data:text/html,<html><head><title>${marker}</title></head><body><h1 id="x">${marker}</h1></body></html>`, { timeout: 15000 })
    log('goto succeeded')
    const title = await previewPage.title()
    assert.strictEqual(title, marker, `title mismatch: got "${title}"`)
    const text = await previewPage.locator('#x').textContent()
    assert.strictEqual(text, marker, `content mismatch: got "${text}"`)
    log('round-trip verified: real navigation, real page content, both reflected back through the relay')

    await cdpBrowser.close()
    cdpBrowser = null

    // ── 7. Close the pane via the real UI, confirm backend unregisters ──
    await previewBtn.click()
    await waitFor(
      async () => (await getRegisteredPreviewEndpoint(token)) === null,
      { timeoutMs: 15000, label: 'preview endpoint unregistered after close' },
    )
    log('backend confirms unregistration after closing the pane')

    log('ALL CHECKS PASSED')
    process.exitCode = 0
  } catch (err) {
    console.error('[test] FAILED:', err.message)
    try {
      const w = electronApp?.windows().find(w => w.url().includes(':5174'))
      if (w) {
        await w.screenshot({ path: __dirname + '/failure.png' })
        console.error('[test] screenshot saved to tests/electron/failure.png')
      }
    } catch { /* best effort */ }
    process.exitCode = 1
  } finally {
    try { await cdpBrowser?.close() } catch { /* already closed */ }
    try { await electronApp?.close() } catch { /* already gone */ }
  }
}

main()
