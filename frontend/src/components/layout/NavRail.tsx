import { useEffect, useRef, useState, useCallback } from 'react'
import { FolderOpen, BarChart2, Bell, LogOut, Brain, BookOpen, Bot, Clock, Wand2, Webhook, Settings, Plug2, Library, Download, RefreshCw, Sparkles, Globe } from 'lucide-react'
import { useAuthStore }   from '@/store/authStore'
import { useChatStore }   from '@/store/chatStore'
import { useUiStore }     from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { authApi } from '@/api/auth'
import { healthApi } from '@/api/health'
import { cn } from '@/lib/utils'
import { HEALTH_CHECK_INTERVAL_MS } from '@/lib/constants'

export type ActiveView = 'workspace' | 'memory' | 'rag' | 'settings' | 'subagents' | 'daemons' | 'skills' | 'hooks' | 'projects' | 'analytics' | 'mcp' | 'docs' | null

interface NavRailProps {
  view:              'workspace' | null
  onWorkspaceToggle: () => void
  onPanelToggle:     (panel: Exclude<ActiveView, 'workspace' | null>) => void
  unreadCount?:      number
  onBell?:           () => void
}

export default function NavRail({
  view,
  onWorkspaceToggle,
  onPanelToggle,
  unreadCount = 0,
  onBell,
}: NavRailProps) {
  const clearAuth             = useAuthStore(s => s.clearAuth)
  const username              = useAuthStore(s => s.username)
  const chatViewMode          = useChatStore(s => s.viewMode)
  const previewOpen           = useChatStore(s => s.previewOpen)
  const setPreviewOpen        = useChatStore(s => s.setPreviewOpen)
  const setViewWelcome        = useChatStore(s => s.setViewWelcome)
  const openProfileModal      = useUiStore(s => s.openProfileModal)
  const updateStatus          = useUpdateStore(s => s.status)
  const updatePercent         = useUpdateStore(s => s.percent)
  const showWhatsNew          = useChatStore(s => s.showWhatsNew)
  const setShowWhatsNew       = useChatStore(s => s.setShowWhatsNew)
  const [systemReady, setSystemReady] = useState<boolean | null>(null)
  const [avatarPulse, setAvatarPulse] = useState(false)
  const prevShowWhatsNewRef   = useRef(false)
  const [appVersion, setAppVersion] = useState<string | null>(null)

  useEffect(() => {
    window.electronAPI?.getVersion?.()
      .then((v: string) => setAppVersion(v))
      .catch(() => {})
  }, [])

  // Pulse the avatar for 3s after the What's New modal is dismissed —
  // guides the user to where they can access changelog again
  useEffect(() => {
    if (prevShowWhatsNewRef.current && !showWhatsNew) {
      setAvatarPulse(true)
      const t = setTimeout(() => setAvatarPulse(false), 3000)
      prevShowWhatsNewRef.current = false
      return () => clearTimeout(t)
    }
    prevShowWhatsNewRef.current = showWhatsNew
  }, [showWhatsNew])

  const handleLogout = async () => {
    try { await authApi.logout() } catch { /* ignore */ }
    clearAuth()
  }

  const checkHealth = useCallback(async (signal: { cancelled: boolean }) => {
    try {
      const { status } = await healthApi.checkReady()
      if (!signal.cancelled) setSystemReady(status === 'ready')
    } catch {
      if (!signal.cancelled) setSystemReady(false)
    }
  }, [])

  useEffect(() => {
    const signal = { cancelled: false }
    checkHealth(signal)
    const interval = setInterval(() => checkHealth(signal), HEALTH_CHECK_INTERVAL_MS)
    return () => { signal.cancelled = true; clearInterval(interval) }
  }, [checkHealth])

  return (
    <nav
      className="flex flex-col items-center shrink-0 relative z-20"
      style={{
        width: 56,
        padding: '12px 0',
        gap: 4,
        background: 'var(--bg-surface1)',
        borderRight: '1px solid var(--border-dim)',
      }}
    >
      {/* Logo — click to open/close About EVA */}
      <div
        className="flex items-center justify-center mb-2"
        style={{ flexShrink: 0, cursor: 'pointer' }}
        onClick={() => chatViewMode === 'welcome' ? onWorkspaceToggle() : setViewWelcome()}
        title="About EVA"
      >
        <img src="/eva-icon.svg" alt="EVA" width={36} height={36} />
      </div>

      {/* Workspace panel toggle (conversation list) */}
      <RailBtn
        icon={<FolderOpen size={17} />}
        label="Workspace"
        active={view === 'workspace'}
        onClick={onWorkspaceToggle}
      />

      {/* ── Tool panel icons — active state tracks chatStore viewMode, not AppShell panel ── */}
      <RailBtn icon={<Brain size={17} />}     label="Memory"         active={chatViewMode === 'memory'}    onClick={() => onPanelToggle('memory')} />
      <RailBtn icon={<BookOpen size={17} />}   label="Knowledge Base" active={chatViewMode === 'rag'}       onClick={() => onPanelToggle('rag')} />
      <RailBtn icon={<Bot size={17} />}       label="Sub Agents"     active={chatViewMode === 'agents'}    onClick={() => onPanelToggle('subagents')} />
      <RailBtn icon={<Clock size={17} />}     label="Daemons"        active={chatViewMode === 'daemons'}   onClick={() => onPanelToggle('daemons')} />
      <RailBtn icon={<Wand2 size={17} />}      label="Skills"         active={chatViewMode === 'skills'}    onClick={() => onPanelToggle('skills')} />
      <RailBtn icon={<Webhook size={17} />}   label="Hooks"          active={chatViewMode === 'hooks'}     onClick={() => onPanelToggle('hooks')} />
      <RailBtn icon={<Plug2 size={17} />}     label="MCP Servers"    active={chatViewMode === 'mcp'}       onClick={() => onPanelToggle('mcp')} />
      <RailBtn icon={<Settings size={17} />}  label="Settings"       active={chatViewMode === 'settings'}  onClick={() => onPanelToggle('settings')} />
      <RailBtn icon={<BarChart2 size={17} />} label="Analytics"      active={chatViewMode === 'analytics'} onClick={() => onPanelToggle('analytics')} />

      {/* Preview pane — persistent side panel, not a ViewMode (coexists with
          chat rather than replacing it), so it toggles previewOpen directly
          instead of going through onPanelToggle. Electron-only — window
          .electronAPI is undefined in browser/dev mode; PreviewPanel.tsx
          itself renders the "desktop app required" state, this button still
          opens it either way so that state is reachable/discoverable. */}
      <RailBtn
        icon={<Globe size={17} />}
        label="Preview"
        active={previewOpen}
        onClick={() => setPreviewOpen(!previewOpen)}
      />

      {/* Spacer */}
      <div className="flex-1" />

      {/* Documentation — above notifications */}
      <RailBtn icon={<Library size={17} />}   label="Documentation"  active={chatViewMode === 'docs'}      onClick={() => onPanelToggle('docs')} />

      {/* Bell — notifications */}
      <div className="relative">
        <RailBtn
          icon={<Bell size={18} />}
          label={unreadCount > 0 ? `Notifications (${unreadCount})` : 'Notifications'}
          active={false}
          onClick={onBell}
        />
        {unreadCount > 0 && (
          <span
            className="pointer-events-none absolute flex items-center justify-center font-bold"
            style={{
              top: 5, right: 5,
              minWidth: 14, height: 14,
              background: 'var(--danger)',
              color: '#fff',
              borderRadius: 999,
              fontSize: 9,
              padding: '0 3px',
            }}
          >
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </div>

      {/* Update indicator — only rendered during download or when update is ready */}
      {updateStatus !== 'idle' && (
        <div className="group relative">
          <button
            onClick={updateStatus === 'ready' ? () => window.electronAPI?.installUpdate() : undefined}
            className={cn(
              'relative flex items-center justify-center rounded-xl transition-all duration-100',
              updateStatus === 'ready'
                ? 'text-[var(--eva-primary)] bg-[rgba(0,201,255,0.08)] hover:bg-[rgba(0,201,255,0.16)] cursor-pointer'
                : 'text-[var(--amber)] cursor-default',
            )}
            style={{ width: 40, height: 40, border: 'none' }}
          >
            {/* Pulsing ring during download */}
            {updateStatus === 'downloading' && (
              <span
                className="absolute inset-2 rounded-full animate-ping"
                style={{ border: '1.5px solid var(--amber)', opacity: 0.35 }}
              />
            )}
            {/* Teal dot when ready */}
            {updateStatus === 'ready' && (
              <span
                className="absolute"
                style={{
                  top: 7, right: 7,
                  width: 7, height: 7,
                  borderRadius: 999,
                  background: 'var(--eva-primary)',
                  boxShadow: '0 0 0 2px rgba(0,201,255,0.25)',
                }}
              />
            )}
            {updateStatus === 'downloading'
              ? <Download size={17} />
              : <RefreshCw size={17} />
            }
          </button>

          {/* Hover widget */}
          <div
            className="pointer-events-none absolute opacity-0 group-hover:opacity-100 transition-opacity z-50"
            style={{
              left: 46, bottom: 0,
              background: 'var(--bg-surface3)',
              border: '1px solid var(--border-dim)',
              borderRadius: 10,
              padding: '10px 14px',
              whiteSpace: 'nowrap',
              boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
              minWidth: 190,
            }}
          >
            {updateStatus === 'ready' ? (
              <>
                <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--eva-primary)', margin: 0 }}>
                  Update ready
                </p>
                <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '3px 0 0' }}>
                  Click to restart and install
                </p>
              </>
            ) : (
              <>
                <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--amber)', margin: 0 }}>
                  Downloading update
                </p>
                <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '3px 0 0' }}>
                  {updatePercent > 0 ? `${Math.round(updatePercent)}% complete` : 'Starting…'}
                </p>
              </>
            )}
          </div>
        </div>
      )}

      {/* Avatar — ring colour indicates server health; click opens profile modal */}
      <div className="group relative">
        <div
          className="relative flex items-center justify-center select-none"
          onClick={() => openProfileModal('profile')}
          style={{
            width: 30, height: 30,
            borderRadius: 999,
            background: 'linear-gradient(135deg, rgba(0,201,255,0.3), rgba(124,108,243,0.3))',
            border: systemReady === true
              ? '2px solid var(--success)'
              : systemReady === false
              ? '2px solid var(--danger)'
              : '2px solid var(--text-muted)',
            boxShadow: systemReady === true
              ? '0 0 0 3px rgba(0,212,160,0.18)'
              : systemReady === false
              ? '0 0 0 3px rgba(255,77,109,0.18)'
              : 'none',
            color: 'var(--eva-primary)',
            fontWeight: 600,
            fontSize: 12,
            cursor: 'pointer',
            transition: 'border-color 400ms, box-shadow 400ms',
          }}
        >
          {/* Post-dismiss pulse — signals where changelog access lives */}
          {avatarPulse && (
            <span
              className="absolute inset-0 rounded-full animate-ping pointer-events-none"
              style={{ border: '2px solid var(--eva-primary)', opacity: 0.45 }}
            />
          )}
          {(username?.[0] ?? '?').toUpperCase()}
        </div>

        {/* Hover card — pointer-events-auto so What's New link is clickable */}
        <div
          className="absolute opacity-0 group-hover:opacity-100 transition-opacity z-50"
          style={{
            left: 38, bottom: 0,
            background: 'var(--bg-surface3)',
            border: '1px solid var(--border-dim)',
            borderRadius: 8,
            padding: '8px 10px',
            whiteSpace: 'nowrap',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            minWidth: 200,
          }}
        >
          <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            {username ?? 'User'}
          </p>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 6px' }}>
            {systemReady === null
              ? 'Connecting to server…'
              : systemReady ? 'Server connected · click to open profile' : 'Server connection error'}
          </p>
          <div style={{ borderTop: '1px solid var(--border-dim)', marginBottom: 6 }} />
          <button
            onClick={() => setShowWhatsNew(appVersion ?? '')}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              background: 'none', border: 'none',
              color: 'var(--neural)', fontSize: 11,
              cursor: 'pointer', padding: '2px 0', width: '100%', textAlign: 'left',
            }}
          >
            <Sparkles size={11} />
            What's New
          </button>
        </div>
      </div>

      {/* Logout */}
      <RailBtn
        icon={<LogOut size={18} />}
        label="Sign out"
        active={false}
        onClick={handleLogout}
        danger
      />
    </nav>
  )
}

/* ─── Rail Button helper ─── */
interface RailBtnProps {
  icon:     React.ReactNode
  label:    string
  active:   boolean
  onClick?: () => void
  danger?:  boolean
}

function RailBtn({ icon, label, active, onClick, danger }: RailBtnProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'group relative flex items-center justify-center rounded-xl transition-all duration-100 cursor-pointer',
        active
          ? 'text-[var(--eva-primary)] bg-[var(--eva-primary-dim)]'
          : danger
          ? 'text-[var(--text-muted)] hover:text-[var(--danger)] hover:bg-[rgba(255,77,109,0.06)]'
          : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[rgba(255,255,255,0.04)]'
      )}
      style={{ width: 40, height: 40 }}
    >
      {icon}
      {/* CSS tooltip */}
      <span
        className="pointer-events-none absolute whitespace-nowrap px-2.5 py-1.5 rounded-lg text-xs opacity-0 group-hover:opacity-100 transition-opacity z-50"
        style={{
          left: 48, top: '50%', transform: 'translateY(-50%)',
          background: 'var(--bg-surface3)',
          border: '1px solid var(--border-mid)',
          color: 'var(--text-secondary)',
          boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        }}
      >
        {label}
      </span>
    </button>
  )
}
