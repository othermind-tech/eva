import type { ViewMode } from '@/store/chatStore'

// Full-view panels rendered outside the normal chat chrome (header, message
// list, input) — shared by the 3 call sites in ChatView.tsx that need to know
// whether the current viewMode is one of these panels.
const PANEL_VIEW_MODES = new Set<ViewMode>([
  'memory', 'rag', 'projects', 'skills', 'agents', 'daemons',
  'analytics', 'settings', 'mcp', 'hooks', 'docs', 'welcome',
])

export function isPanelViewMode(viewMode: ViewMode): boolean {
  return PANEL_VIEW_MODES.has(viewMode)
}
