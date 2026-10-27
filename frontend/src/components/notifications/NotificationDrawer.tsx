import { useEffect, useState } from 'react'
import { useNotificationStore, type Notification } from '@/store/notificationStore'
import ActivityTab from './ActivityTab'
import type {
  AppEvent,
  EvtDaemonCompleted,
  EvtDaemonFailed,
  EvtDaemonTriggered,
  EvtExecutionCompleted,
  EvtExecutionFailed,
  EvtProviderFallbackPromoted,
  EvtProcessStopped,
  EvtProcessCrashed,
} from '@/types/api'

// ─── Helpers ────────────────────────────────────────────────────────────────

function dotColor(evt: AppEvent): string {
  switch (evt.event) {
    case 'daemon.completed':    return 'var(--success)'
    case 'daemon.failed':       return 'var(--danger)'
    case 'daemon.triggered':    return 'var(--eva-primary)'
    case 'execution.completed': return 'var(--neural)'
    case 'process.crashed':     return 'var(--danger)'
    case 'execution.failed':    return 'var(--danger)'
    case 'provider.fallback_promoted': return 'var(--amber)'
    default:                    return 'var(--text-muted)'
  }
}

function eventLabel(evt: AppEvent): string {
  switch (evt.event) {
    case 'daemon.completed':    return 'Daemon completed'
    case 'daemon.failed':       return 'Daemon failed'
    case 'daemon.triggered':    return 'Daemon triggered'
    case 'execution.completed': return 'Execution completed'
    case 'process.stopped':     return 'Background process stopped'
    case 'process.crashed':     return 'Background process crashed'
    case 'execution.failed':    return 'Execution failed'
    case 'provider.fallback_promoted': return 'Using a fallback provider'
    default:                    return 'Event'
  }
}

function eventDetail(evt: AppEvent): string {
  switch (evt.event) {
    case 'daemon.completed':
    case 'daemon.failed':
    case 'daemon.triggered':
      return (evt as EvtDaemonCompleted | EvtDaemonFailed | EvtDaemonTriggered).daemon_name
    case 'execution.completed':
      return (evt as EvtExecutionCompleted).execution_id
    case 'process.stopped':
    case 'process.crashed':
      return (evt as EvtProcessStopped | EvtProcessCrashed).execution_id
    case 'execution.failed':
      // A full sentence when the server sent one (it renders in the wrapping summary
      // block below); otherwise the id, like the other execution events.
      return (evt as EvtExecutionFailed).summary ? '' : (evt as EvtExecutionFailed).execution_id
    default:                    return ''
  }
}

function relativeTime(ts: Date): string {
  const diffMs  = Date.now() - ts.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60)  return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60)  return `${diffMin}m ago`
  const diffHr  = Math.floor(diffMin / 60)
  if (diffHr  < 24)  return `${diffHr}h ago`
  return `${Math.floor(diffHr / 24)}d ago`
}

// ─── Sub-component: single notification row ──────────────────────────────────

function NotificationItem({
  notification,
  onRemove,
}: {
  notification: Notification
  onRemove: (id: string) => void
}) {
  const { id, event, ts } = notification
  const color = dotColor(event)

  const summary = event.event === 'daemon.completed' || event.event === 'process.stopped' || event.event === 'process.crashed'
    ? (event as EvtDaemonCompleted | EvtProcessStopped | EvtProcessCrashed).summary
    : event.event === 'execution.failed' || event.event === 'provider.fallback_promoted'
      ? (event as EvtExecutionFailed | EvtProviderFallbackPromoted).summary
      : undefined

  const error = event.event === 'daemon.failed'
    ? (event as EvtDaemonFailed).error
    : undefined

  return (
    <div
      style={{
        display:       'flex',
        alignItems:    'flex-start',
        gap:           '10px',
        padding:       '12px 16px',
        borderBottom:  '1px solid var(--border-dim)',
        position:      'relative',
      }}
    >
      {/* Colored dot */}
      <span
        style={{
          flexShrink:   0,
          marginTop:    '4px',
          width:        '8px',
          height:       '8px',
          borderRadius: '50%',
          background:   color,
          boxShadow:    `0 0 6px ${color}80`,
        }}
      />

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Label */}
        <p
          style={{
            margin:      0,
            fontWeight:  600,
            fontSize:    '12px',
            color:       'var(--text-primary)',
            lineHeight:  '1.4',
          }}
        >
          {eventLabel(event)}
        </p>

        {/* Detail — daemon name or execution_id */}
        <p
          style={{
            margin:      '2px 0 0',
            fontFamily:  '"JetBrains Mono", monospace',
            fontSize:    '11px',
            color:       'var(--text-muted)',
            overflow:    'hidden',
            textOverflow:'ellipsis',
            whiteSpace:  'nowrap',
          }}
        >
          {eventDetail(event)}
        </p>

        {/* Summary (daemon.completed only) */}
        {summary && (
          <p
            style={{
              margin:     '4px 0 0',
              fontSize:   '11px',
              color:      'var(--text-secondary)',
              lineHeight: '1.4',
            }}
          >
            {summary}
          </p>
        )}

        {/* Error (daemon.failed only) */}
        {error && (
          <p
            style={{
              margin:     '4px 0 0',
              fontSize:   '11px',
              color:      'var(--danger)',
              lineHeight: '1.4',
            }}
          >
            {error}
          </p>
        )}

        {/* Relative time */}
        <p
          style={{
            margin:    '4px 0 0',
            fontSize:  '10px',
            color:     'var(--text-muted)',
            textAlign: 'right',
          }}
        >
          {relativeTime(ts)}
        </p>
      </div>

      {/* Remove button */}
      <button
        onClick={() => onRemove(id)}
        aria-label="Dismiss notification"
        style={{
          flexShrink:  0,
          background:  'transparent',
          border:      'none',
          cursor:      'pointer',
          color:       'var(--text-muted)',
          fontSize:    '14px',
          lineHeight:  1,
          padding:     '2px 4px',
          borderRadius:'4px',
          marginTop:   '1px',
          transition:  'color 0.15s',
        }}
        onMouseEnter={(e) => {
          ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)'
        }}
        onMouseLeave={(e) => {
          ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)'
        }}
      >
        ✕
      </button>
    </div>
  )
}

// ─── Main component ──────────────────────────────────────────────────────────

interface Props {
  open:    boolean
  onClose: () => void
}

export default function NotificationDrawer({ open, onClose }: Props) {
  const notifications = useNotificationStore((s) => s.notifications)
  const unreadCount   = useNotificationStore((s) => s.unreadCount)
  const markAllRead   = useNotificationStore((s) => s.markAllRead)
  const clear         = useNotificationStore((s) => s.clear)
  const remove        = useNotificationStore((s) => s.remove)

  // Section 85 (P-19) — second tab for the persistent running/paused state
  // view, distinct from the transient notification feed above (same SSE
  // connection under the hood via useEventStream, separate query-cache state)
  const [activeTab, setActiveTab] = useState<'notifications' | 'activity'>('notifications')

  // Mark all read when drawer opens
  useEffect(() => {
    if (open) markAllRead()
  }, [open, markAllRead])

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position:   'fixed',
          inset:      0,
          zIndex:     49,
          background: 'rgba(0, 0, 0, 0.45)',
          opacity:    open ? 1 : 0,
          pointerEvents: open ? 'auto' : 'none',
          transition: 'opacity 0.25s ease',
        }}
      />

      {/* Drawer */}
      <div
        role="dialog"
        aria-label="Notifications"
        style={{
          position:   'fixed',
          top:        0,
          right:      0,
          zIndex:     50,
          width:      activeTab === 'activity' ? '420px' : '360px',
          height:     '100vh',
          display:    'flex',
          flexDirection: 'column',
          background: 'var(--bg-surface2)',
          borderLeft: '1px solid var(--border-dim)',
          transform:  open ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.28s cubic-bezier(0.4, 0, 0.2, 1), width 0.15s ease',
          willChange: 'transform',
        }}
      >
        {/* Header */}
        <div
          style={{
            display:        'flex',
            alignItems:     'center',
            justifyContent: 'space-between',
            padding:        '16px 16px 14px',
            borderBottom:   '1px solid var(--border-dim)',
            flexShrink:     0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span
              style={{
                fontSize:   '15px',
                fontWeight: 600,
                color:      'var(--text-primary)',
              }}
            >
              {activeTab === 'activity' ? 'Activity' : 'Notifications'}
            </span>
            {activeTab === 'notifications' && unreadCount > 0 && (
              <span
                style={{
                  display:      'inline-flex',
                  alignItems:   'center',
                  justifyContent:'center',
                  minWidth:     '20px',
                  height:       '20px',
                  padding:      '0 5px',
                  borderRadius: '10px',
                  background:   'var(--eva-primary)',
                  color:        'var(--bg-base)',
                  fontSize:     '11px',
                  fontWeight:   700,
                  lineHeight:   1,
                }}
              >
                {unreadCount}
              </span>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {activeTab === 'notifications' && notifications.length > 0 && (
              <button
                onClick={clear}
                style={{
                  background:  'transparent',
                  border:      'none',
                  cursor:      'pointer',
                  fontSize:    '12px',
                  color:       'var(--text-muted)',
                  padding:     '4px 8px',
                  borderRadius:'4px',
                  transition:  'color 0.15s',
                }}
                onMouseEnter={(e) => {
                  ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)'
                }}
                onMouseLeave={(e) => {
                  ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)'
                }}
              >
                Clear all
              </button>
            )}
            <button
              onClick={onClose}
              aria-label="Close notifications"
              style={{
                background:  'transparent',
                border:      'none',
                cursor:      'pointer',
                color:       'var(--text-muted)',
                fontSize:    '16px',
                lineHeight:  1,
                padding:     '4px 6px',
                borderRadius:'4px',
                transition:  'color 0.15s',
              }}
              onMouseEnter={(e) => {
                ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)'
              }}
              onMouseLeave={(e) => {
                ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)'
              }}
            >
              ✕
            </button>
          </div>
        </div>

        {/* Tab bar */}
        <div style={{ display: 'flex', borderBottom: '1px solid var(--border-dim)', flexShrink: 0 }}>
          {(['notifications', 'activity'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                flex: 1,
                padding: '9px 0',
                background: 'transparent',
                border: 'none',
                borderBottom: `2px solid ${activeTab === tab ? 'var(--eva-primary)' : 'transparent'}`,
                color: activeTab === tab ? 'var(--text-primary)' : 'var(--text-muted)',
                fontSize: '12.5px',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'color 0.15s, border-color 0.15s',
              }}
            >
              {tab === 'notifications' ? 'Notifications' : 'Activity'}
            </button>
          ))}
        </div>

        {activeTab === 'activity' ? (
          <ActivityTab />
        ) : (
        /* Scrollable list */
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {notifications.length === 0 ? (
            // Empty state
            <div
              style={{
                display:        'flex',
                flexDirection:  'column',
                alignItems:     'center',
                justifyContent: 'center',
                height:         '100%',
                gap:            '12px',
                color:          'var(--text-muted)',
              }}
            >
              {/* Bell icon */}
              <svg
                width="40"
                height="40"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ opacity: 0.4 }}
              >
                <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                <path d="M13.73 21a2 2 0 0 1-3.46 0" />
              </svg>
              <span style={{ fontSize: '13px' }}>No notifications yet</span>
            </div>
          ) : (
            notifications.map((n) => (
              <NotificationItem key={n.id} notification={n} onRemove={remove} />
            ))
          )}
        </div>
        )}
      </div>
    </>
  )
}
