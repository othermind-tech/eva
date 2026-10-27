import { type ReactNode, useState, useEffect, createContext, useContext } from 'react'
import NavRail, { type ActiveView } from './NavRail'
import WorkspacePanel from './WorkspacePanel'
import { UserProfileModal } from '@/components/profile/UserProfileModal'
import { ApprovalGate }       from '../approval/ApprovalGate'
import { WorkerDownBanner }   from '../common/WorkerDownBanner'
import { StepLimitBanner }    from '../common/StepLimitBanner'
import { ErrorBoundary }      from '../ui/ErrorBoundary'
import NotificationDrawer     from '../notifications/NotificationDrawer'
import PreviewPanel           from '../chat/PreviewPanel'
import { useChatStore }       from '@/store/chatStore'
import { useEventStream }     from '@/hooks/useEventStream'
import { useUpdateEvents }   from '@/hooks/useUpdateEvents'
import { useCloseGuard }     from '@/hooks/useCloseGuard'
import WhatsNewModal        from '@/components/welcome/WhatsNewModal'
import ProviderRequiredModal from '@/components/common/ProviderRequiredModal'
import { useNotificationStore } from '@/store/notificationStore'
import { useUiStore }         from '@/store/uiStore'

// Re-export PanelView for any external references (backward compat)
export type PanelView = 'memory' | 'rag' | 'settings' | 'subagents' | 'daemons' | 'skills' | 'projects' | 'analytics' | 'mcp' | null
export type { ActiveView }

// Context that lets any descendant open the workspace panel
const WorkspaceCtx = createContext<() => void>(() => {})
export const useOpenWorkspace = () => useContext(WorkspaceCtx)

interface AppShellProps {
  children: ReactNode
}

export default function AppShell({ children }: AppShellProps) {
  // 'workspace' = WorkspacePanel open, null = panel column collapsed
  const [view, setView] = useState<'workspace' | null>('workspace')
  const [notifOpen, setNotifOpen] = useState(false)

  const approval            = useChatStore(s => s.approval)
  const workerDown          = useChatStore(s => s.workerDown)
  const setViewConversation = useChatStore(s => s.setViewConversation)
  const setViewAgents       = useChatStore(s => s.setViewAgents)
  const setViewDaemons      = useChatStore(s => s.setViewDaemons)
  const setViewMemory       = useChatStore(s => s.setViewMemory)
  const setViewRAG          = useChatStore(s => s.setViewRAG)
  const setViewSkills       = useChatStore(s => s.setViewSkills)
  const setViewSettings     = useChatStore(s => s.setViewSettings)
  const setViewAnalytics    = useChatStore(s => s.setViewAnalytics)
  const setViewMCP          = useChatStore(s => s.setViewMCP)
  const setViewHooks        = useChatStore(s => s.setViewHooks)
  const setViewDocs         = useChatStore(s => s.setViewDocs)
  const setViewProjects     = useChatStore(s => s.setViewProjects)
  const viewMode            = useChatStore(s => s.viewMode)
  const previewOpen         = useChatStore(s => s.previewOpen)
  const setPreviewOpen      = useChatStore(s => s.setPreviewOpen)
  const unreadCount         = useNotificationStore(s => s.unreadCount)
  const profileModalTab     = useUiStore(s => s.profileModalTab)
  const closeProfileModal   = useUiStore(s => s.closeProfileModal)

  // Open event stream while authenticated
  useEventStream()
  // Wire Electron auto-update IPC listeners
  useUpdateEvents()
  // Expose window.__evaCloseGuard for main.js's app-close cancellation guard
  useCloseGuard()

  // Sync view state when leaving docs/welcome so workspace icon returns to inactive
  useEffect(() => {
    if (viewMode === 'docs' || viewMode === 'welcome') setView(null)
  }, [viewMode])

  // Synchronous guard — workspace must never render alongside full-screen panels
  const showWorkspace = view === 'workspace' && viewMode !== 'docs' && viewMode !== 'welcome'

  // ─── NavRail handlers ──────────────────────────────────────────────────────
  const onWorkspaceToggle = () => {
    // From docs/welcome: go to new chat and open workspace (do NOT open workspace alongside full-screen panel)
    if (viewMode === 'docs' || viewMode === 'welcome') {
      setViewConversation()
      setView('workspace')
      return
    }
    setView('workspace')
  }

  // Panel icons — only change chatStore viewMode; workspace panel stays unaffected
  const onNavPanelToggle = (panel: Exclude<ActiveView, 'workspace' | null>) => {
    switch (panel) {
      case 'memory':    setViewMemory();    break
      case 'rag':       setViewRAG();       break
      case 'subagents': setViewAgents();    break
      case 'daemons':   setViewDaemons();   break
      case 'skills':    setViewSkills();    break
      case 'analytics': setViewAnalytics(); break
      case 'settings':  setViewSettings();  break
      case 'mcp':       setViewMCP();       break
      case 'hooks':     setViewHooks();     break
      case 'projects':  setViewProjects();  break
      // Docs toggles: clicking Library when already in docs exits back to conversation
      case 'docs':
        if (viewMode === 'docs') setViewConversation()
        else setViewDocs()
        break
    }
  }

  const closePanelColumn = () => setView(null)

  return (
    <div className="flex flex-col h-full w-full overflow-hidden" style={{ background: 'var(--bg-base)' }}>

      {workerDown && <WorkerDownBanner />}
      <StepLimitBanner />

      <div className="flex flex-1 overflow-hidden">

      <NavRail
        view={showWorkspace ? 'workspace' : null}
        onWorkspaceToggle={onWorkspaceToggle}
        onPanelToggle={onNavPanelToggle}
        unreadCount={unreadCount}
        onBell={() => setNotifOpen(v => !v)}
      />

      {/* Mobile backdrop */}
      {showWorkspace && (
        <div className="eva-backdrop" onClick={closePanelColumn} aria-hidden="true" />
      )}

      <ErrorBoundary label="Panel error">
        {/* Workspace panel — never shown alongside docs or welcome */}
        {showWorkspace && (
          <WorkspacePanel onClose={closePanelColumn} />
        )}
      </ErrorBoundary>

      <main className="flex-1 min-w-0 flex flex-col overflow-hidden relative">
        <ErrorBoundary label="Chat error">
          <WorkspaceCtx.Provider value={() => setView('workspace')}>
            {children}
          </WorkspaceCtx.Provider>
        </ErrorBoundary>
      </main>

      {approval.status !== 'idle' && <ApprovalGate />}

      <NotificationDrawer open={notifOpen} onClose={() => setNotifOpen(false)} />

      <PreviewPanel open={previewOpen} onClose={() => setPreviewOpen(false)} />

      {profileModalTab !== null && (
        <UserProfileModal initialTab={profileModalTab} onClose={closeProfileModal} />
      )}

      </div>

      {/* What's New modal — overlays everything after an update relaunch */}
      <WhatsNewModal />

      {/* Provider-required modal — no provider configured, blocks send/trigger/enhance */}
      <ProviderRequiredModal />
    </div>
  )
}
