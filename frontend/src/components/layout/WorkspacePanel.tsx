/**
 * WorkspacePanel — unified Conversations + Projects browser.
 * Matches the design's workspace.jsx closely:
 *   - Chat tab: search → quicklinks (New chat, Skills, Daemons, Subagents, + More) → date-grouped recents
 *   - Projects tab: search → New project → expandable project tree
 *   - User pill at bottom
 */
import { useState } from 'react'
import { MessageSquare, FolderOpen, X } from 'lucide-react'
import { useChatStore } from '@/store/chatStore'
import { cn } from '@/lib/utils'
import UserPill from './UserPill'
import ChatTab from './ChatTab'
import ProjectsTab from './ProjectsTab'

// ─── Types ───────────────────────────────────────────────────────────────────
interface Props {
  onClose?:     () => void
}

// ─── Root component ───────────────────────────────────────────────────────────
export default function WorkspacePanel({ onClose }: Props) {
  const [tab, setTab] = useState<'chat' | 'projects'>('chat')
  const setViewConversation = useChatStore(s => s.setViewConversation)

  const switchToChat = () => {
    if (tab !== 'chat') {
      setTab('chat')
      setViewConversation()   // reset main area back to conversation view
    }
  }

  return (
    <div
      className="flex flex-col h-full shrink-0 animate-slide-in-left eva-panel"
      style={{
        width: 360,
        background: 'var(--bg-surface1)',
        borderRight: '1px solid var(--border-dim)',
        overflow: 'hidden',
      }}
    >
      {/* Top tabs — Chat | Projects | ×close */}
      <div className="top-tabs">
        <button
          className={cn('top-tab', tab === 'chat' && 'active')}
          onClick={switchToChat}
        >
          <MessageSquare size={14} /> Chat
        </button>
        <button
          className={cn('top-tab', tab === 'projects' && 'active')}
          onClick={() => setTab('projects')}
        >
          <FolderOpen size={14} /> Projects
        </button>
        <div style={{ flex: 1 }} />
        {onClose && (
          <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={onClose} title="Collapse">
            <X size={15} />
          </button>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-y-auto" style={{ padding: '4px 0 0' }}>
        {tab === 'chat'
          ? <ChatTab />
          : <ProjectsTab />
        }
      </div>

      {/* User pill */}
      <UserPill />
    </div>
  )
}

