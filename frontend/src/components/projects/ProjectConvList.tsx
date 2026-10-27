import { useState, useRef } from 'react'
import { Loader2, MessageSquare, Plus, Lock, Edit3, Trash2, Check, X } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi } from '@/api/projects'
import { conversationsApi } from '@/api/conversations'
import { formatDate } from '@/lib/utils'
import { useChatStore } from '@/store/chatStore'

export default function ProjectConvList({ projectId, projectStatus, onSelect }: { projectId: string; projectStatus?: string; onSelect: (id: string, title: string | null) => void }) {
  const qc                   = useQueryClient()
  const activeConversationId = useChatStore(s => s.activeConversationId)
  const setProjectConvId     = useChatStore(s => s.setProjectConvId)
  const renameRef            = useRef<HTMLInputElement>(null)

  const goToNewConv = () => setProjectConvId('__new__', 'New conversation')

  const { data: convs = [], isLoading } = useQuery({
    queryKey: ['project-conversations', projectId],
    queryFn:  () => projectsApi.listConversations(projectId),
    staleTime: 30_000,
  })

  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [renameId,  setRenameId]  = useState<string | null>(null)
  const [renameVal, setRenameVal] = useState('')

  const deleteMut = useMutation({
    mutationFn: (id: string) => conversationsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project-conversations', projectId] })
      qc.invalidateQueries({ queryKey: ['conversations'] })
      setConfirmId(null)
    },
  })

  const renameMut = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      conversationsApi.rename(id, title),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project-conversations', projectId] })
      qc.invalidateQueries({ queryKey: ['conversations'] })
      setRenameId(null)
    },
  })

  const startRename = (c: { id: string; title?: string }, e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirmId(null)
    setRenameId(c.id)
    setRenameVal(c.title ?? '')
    setTimeout(() => renameRef.current?.select(), 60)
  }

  const commitRename = (id: string) => {
    const t = renameVal.trim()
    if (t) renameMut.mutate({ id, title: t })
    else setRenameId(null)
  }

  const isPaused   = projectStatus === 'paused'
  const isArchived = projectStatus === 'archived'

  if (isLoading) return (
    <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={12} className="animate-spin" /> Loading…
    </div>
  )
  if (convs.length === 0) return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <MessageSquare size={24} style={{ color: 'var(--text-muted)' }} />
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No conversations linked yet.</p>
      {!isPaused && !isArchived && (
        <button className="btn ghost" onClick={goToNewConv}>
          <Plus size={12} /> Start a conversation
        </button>
      )}
    </div>
  )

  return (
    <div>
      {/* New conversation button — hidden when project is locked */}
      {!isPaused && !isArchived && (
        <div className="flex justify-end mb-3">
          <button className="btn primary" onClick={goToNewConv}>
            <Plus size={12} /> New Conversation
          </button>
        </div>
      )}

      {/* Project status banner — shown when paused or archived */}
      {(isPaused || isArchived) && (
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs mb-3"
          style={{
            background: isPaused ? 'rgba(245,158,11,0.08)' : 'rgba(255,77,109,0.08)',
            border:     `1px solid ${isPaused ? 'rgba(245,158,11,0.25)' : 'rgba(255,77,109,0.25)'}`,
            color:      isPaused ? 'var(--amber)' : 'var(--danger)',
          }}
        >
          <Lock size={11} style={{ flexShrink: 0 }} />
          {isPaused
            ? 'Project is paused — resume the project to start new chats.'
            : 'Project is archived — conversations are read-only.'}
        </div>
      )}

      {convs.map(c => {
        const isActive   = c.id === activeConversationId
        const isConfirm  = confirmId === c.id
        const isRenaming = renameId === c.id
        const locked     = isPaused || isArchived

        return (
          <div
            key={c.id}
            className="kn-card"
            style={{
              borderColor: isActive ? 'rgba(0,201,255,0.3)' : undefined,
              opacity:     isArchived ? 0.65 : 1,
            }}
          >
            {/* ── Card header — click to open conversation ── */}
            <div
              className="kn-head"
              onClick={() => !locked && onSelect(c.id, c.title ?? null)}
              style={{ cursor: locked ? 'not-allowed' : 'pointer', marginBottom: isRenaming ? 10 : 0 }}
            >
              {/* Icon box */}
              <div
                className="row-icon"
                style={{
                  background: locked
                    ? isPaused ? 'rgba(245,158,11,0.1)' : 'rgba(255,77,109,0.1)'
                    : 'rgba(0,201,255,0.08)',
                  color: locked
                    ? isPaused ? 'var(--amber)' : 'var(--danger)'
                    : 'var(--eva-primary)',
                  width: 32, height: 32, borderRadius: 9, flexShrink: 0,
                }}
              >
                {locked ? <Lock size={14} /> : <MessageSquare size={14} />}
              </div>

              {/* Title + date */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 500, color: isActive ? 'var(--eva-primary)' : 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {c.title || 'Untitled conversation'}
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                  {formatDate(c.created_at)}
                </div>
              </div>

              {/* Action buttons — stopPropagation so they don't open the conversation */}
              {!locked && (
                <div className="flex items-center" style={{ gap: 1 }} onClick={e => e.stopPropagation()}>
                  {isConfirm ? (
                    <>
                      <span style={{ fontSize: 11, color: 'var(--danger)', marginRight: 4 }}>Delete?</span>
                      <button
                        onClick={() => deleteMut.mutate(c.id)}
                        disabled={deleteMut.isPending}
                        className="icon-btn"
                        style={{ width: 26, height: 26, padding: 0, color: 'var(--danger)' }}
                        title="Confirm"
                      >
                        {deleteMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                      </button>
                      <button
                        onClick={() => setConfirmId(null)}
                        className="icon-btn"
                        style={{ width: 26, height: 26, padding: 0 }}
                        title="Cancel"
                      >
                        <X size={12} />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={e => startRename(c, e)}
                        className="icon-btn"
                        style={{ width: 26, height: 26, padding: 0, color: isRenaming ? 'var(--eva-primary)' : undefined }}
                        title="Rename"
                      >
                        <Edit3 size={13} />
                      </button>
                      <button
                        onClick={e => { e.stopPropagation(); setConfirmId(c.id) }}
                        className="icon-btn"
                        style={{ width: 26, height: 26, padding: 0 }}
                        title="Delete"
                      >
                        <Trash2 size={13} />
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>

            {/* ── Inline rename input — expands below card header ── */}
            {isRenaming && (
              <div className="flex items-center gap-2">
                <input
                  ref={renameRef}
                  value={renameVal}
                  onChange={e => setRenameVal(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') commitRename(c.id)
                    if (e.key === 'Escape') setRenameId(null)
                  }}
                  className="flex-1 text-xs outline-none rounded-lg px-2.5 py-1.5"
                  style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-mid)', color: 'var(--text-primary)' }}
                  autoFocus
                />
                <button
                  onClick={() => commitRename(c.id)}
                  disabled={renameMut.isPending}
                  className="icon-btn"
                  style={{ width: 26, height: 26, padding: 0, color: 'var(--eva-primary)', flexShrink: 0 }}
                >
                  {renameMut.isPending ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                </button>
                <button
                  onClick={() => setRenameId(null)}
                  className="icon-btn"
                  style={{ width: 26, height: 26, padding: 0, flexShrink: 0 }}
                >
                  <X size={11} />
                </button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
