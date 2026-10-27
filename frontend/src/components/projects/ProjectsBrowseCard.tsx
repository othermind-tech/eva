import { useState } from 'react'
import { FolderOpen, Plus, Loader2, RefreshCw, Pause, Play, Archive, MessageSquare, X } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi } from '@/api/projects'
import type { ProjectStatus, ArtifactType } from '@/api/projects'
import { conversationsApi } from '@/api/conversations'
import { useChatStore, apiMessageToUI } from '@/store/chatStore'
import { useSessionStore } from '@/store/sessionStore'
import { useProjectSync } from '@/hooks/useProjectSync'
import { extractErrorDetail, isUnreachableError } from '@/lib/errors'
import ProjectCreateView from './ProjectCreateView'
import { ProjectOverviewBody } from './ProjectOverviewBody'
import ProjectConvList from './ProjectConvList'
import ProjectAgentsTab from './ProjectAgentsTab'
import ProjectDaemonsTab from './ProjectDaemonsTab'
import ProjectMCPTab from './ProjectMCPTab'
import ProjectFilesTab from './ProjectFilesTab'
import ProjectKnowledgeTab from './ProjectKnowledgeTab'
import ProjectArtifactTab from './ProjectArtifactTab'
import ProjectConvView from '@/components/chat/ProjectConvView'

// ── Projects ──────────────────────────────────────────────────────────────────
export default function ProjectsBrowseCard() {
  const projectBrowseId      = useChatStore(s => s.projectBrowseId)
  const projectBrowseTab     = useChatStore(s => s.projectBrowseTab)
  const projectConvId        = useChatStore(s => s.projectConvId)
  const setProjectConvId     = useChatStore(s => s.setProjectConvId)
  const clearMessages        = useChatStore(s => s.clearMessages)
  const loadMessages         = useChatStore(s => s.loadMessages)
  const setMessagesLoadError = useChatStore(s => s.setMessagesLoadError)
  const setWorkerDown        = useChatStore(s => s.setWorkerDown)
  const setActiveConvInPlace = useChatStore(s => s.setActiveConvInPlace)
  const setViewProject       = useChatStore(s => s.setViewProject)
  const setViewProjects      = useChatStore(s => s.setViewProjects)
  const [loadingConv, setLoadingConv] = useState(false)
  // Section 66 — agent memory status chip
  const agentMemEnabled = useSessionStore(s => s.config.agent_memory_enabled ?? true)

  const handleConvSelect = async (id: string, title: string | null) => {
    setLoadingConv(true)
    clearMessages()
    setActiveConvInPlace(id)
    try {
      const { messages: msgs } = await conversationsApi.getMessages(id, { limit: 100 })
      loadMessages(msgs.map(apiMessageToUI))
    } catch (err) {
      // Section 95 — same gap as ChatTab.tsx's handleSelect, project-scoped
      // path. ProjectConvView.tsx renders messagesLoadError instead of its
      // generic "No messages in this conversation" text when set.
      if (isUnreachableError(err)) {
        setWorkerDown(true)
        setMessagesLoadError("Couldn't reach the server. Check your connection and try again.")
      } else {
        setMessagesLoadError(extractErrorDetail(err, "Couldn't load this conversation."))
      }
    }
    setProjectConvId(id, title)
    setLoadingConv(false)
  }

  const handleConvBack = () => {
    setProjectConvId(null)
    clearMessages()
  }

  const { data: project, isLoading, isError } = useQuery({
    queryKey: ['project', projectBrowseId],
    queryFn:  () => projectsApi.get(projectBrowseId!),
    enabled:  !!projectBrowseId && projectBrowseId !== '__new__',
  })

  const qcProj = useQueryClient()
  const { mutate: setProjectStatus, isPending: settingStatus } = useMutation({
    mutationFn: (status: ProjectStatus) => projectsApi.update(project!.id, { status }),
    onSuccess: () => {
      qcProj.invalidateQueries({ queryKey: ['project', projectBrowseId] })
      qcProj.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  // Section 83 — manual resync. Hook must be called unconditionally (before
  // the early returns below), so it's keyed off projectBrowseId directly
  // rather than the not-yet-loaded `project` object.
  const { sync: syncProject, syncing: projectSyncing, triggerError: syncError } = useProjectSync(projectBrowseId ?? '')

  // Create form — full page, matches the Daemon/Agent create pattern
  if (projectBrowseId === '__new__') {
    return (
      <ProjectCreateView
        onBack={setViewProjects}
        onCreated={id => setViewProject(id)}
      />
    )
  }

  // No project selected — show info card
  if (!projectBrowseId) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 px-6 select-none">
        <div
          className="w-16 h-16 rounded-2xl flex items-center justify-center mb-2"
          style={{ background: 'rgba(0,212,160,0.1)', border: '1px solid rgba(0,212,160,0.25)', boxShadow: '0 0 32px rgba(0,212,160,0.1)' }}
        >
          <FolderOpen size={30} style={{ color: 'var(--success)' }} />
        </div>
        <div className="text-center">
          <p className="font-semibold tracking-tight" style={{ fontSize: '1.75rem', color: 'var(--text-primary)', lineHeight: 1.15 }}>
            Projects
          </p>
          <p className="mt-2" style={{ fontSize: '0.9rem', color: 'var(--text-muted)', letterSpacing: '0.01em' }}>
            Organise work into projects
          </p>
        </div>
        <p className="text-center max-w-xs" style={{ fontSize: '0.75rem', color: 'var(--text-dim)', lineHeight: 1.65, marginTop: '0.5rem' }}>
          Click a project in the sidebar to view its workspace — tasks, knowledge, policies, files, agents, and linked conversations.
        </p>
        <button
          onClick={() => setViewProject('__new__')}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold transition-all"
          style={{ background: 'var(--eva-primary-dim)', border: '1px solid var(--border-mid)', color: 'var(--eva-primary)' }}
        >
          <Plus size={13} /> New Project
        </button>
      </div>
    )
  }

  if (isLoading) return (
    <div className="flex items-center justify-center h-full gap-2" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={16} className="animate-spin" /> Loading project…
    </div>
  )
  if (isError || !project) return (
    <div className="flex items-center justify-center h-full gap-2" style={{ color: 'var(--danger)' }}>
      Failed to load project
    </div>
  )


  return (
    <div className="workspace">
      {/* ── Project overview header — hidden when a conversation is drilled into ── */}
      {!projectConvId && <div style={{
        background: 'var(--bg-base)',
        padding: '32px 36px',
        display: 'flex', flexDirection: 'column', gap: 22,
      }}>

        {/* Row 1: icon + name/description + actions */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>

          {/* Folder icon */}
          <div style={{
            width: 44, height: 44, borderRadius: 12, flexShrink: 0,
            background: 'rgba(0,212,160,0.1)', border: '1px solid rgba(0,212,160,0.2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <FolderOpen size={20} style={{ color: 'var(--success)' }} />
          </div>

          {/* Name + status badge + description */}
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 5 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', margin: 0, lineHeight: 1.2 }}>
                {project.name}
              </h1>
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                fontSize: 11, fontWeight: 600, padding: '3px 9px', borderRadius: 999,
                background: project.status === 'active' ? 'rgba(0,212,160,0.12)'
                          : project.status === 'paused' ? 'rgba(245,158,11,0.12)'
                          : 'rgba(255,255,255,0.06)',
                color:      project.status === 'active' ? 'var(--success)'
                          : project.status === 'paused' ? 'var(--amber)'
                          : 'var(--text-muted)',
                border: `1px solid ${
                          project.status === 'active' ? 'rgba(0,212,160,0.25)'
                          : project.status === 'paused' ? 'rgba(245,158,11,0.25)'
                          : 'var(--border-dim)'}`,
              }}>
                {project.status === 'active' ? '● Active' : project.status === 'paused' ? '⏸ Paused' : '◻ Archived'}
              </span>
            </div>
            {project.description && (
              <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>
                {project.description}
              </p>
            )}
          </div>

          {/* Right: action buttons + memory chip */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0, marginTop: 2 }}>
            {project.status === 'active' && (
              <button className="btn ghost" style={{ fontSize: 11, padding: '3px 10px' }}
                disabled={projectSyncing} title={syncError ?? 'Re-run this project\'s sync daemon now'}
                onClick={() => syncProject()}>
                <RefreshCw size={10} className={projectSyncing ? 'animate-spin' : ''} /> {projectSyncing ? 'Syncing…' : 'Sync'}
              </button>
            )}
            {project.status === 'active' && (
              <button className="btn ghost" style={{ fontSize: 11, padding: '3px 10px' }}
                disabled={settingStatus} onClick={() => setProjectStatus('paused')}>
                <Pause size={10} /> Pause
              </button>
            )}
            {project.status === 'paused' && (
              <>
                <button className="btn primary" style={{ fontSize: 11, padding: '3px 10px' }}
                  disabled={settingStatus} onClick={() => setProjectStatus('active')}>
                  <Play size={10} /> Resume
                </button>
                <button className="btn ghost" style={{ fontSize: 11, padding: '3px 10px', color: 'var(--text-muted)' }}
                  disabled={settingStatus} onClick={() => setProjectStatus('archived')}>
                  <Archive size={10} /> Archive
                </button>
              </>
            )}
            {/* Memory chip */}
            {(() => {
              const memOn   = agentMemEnabled && project.status === 'active'
              const tipText = !agentMemEnabled
                ? 'Agent memory is disabled in Session settings'
                : project.status !== 'active'
                  ? `Project is ${project.status} — memory will resume when active (scope: ${project.id})`
                  : `Agent memory accumulating for this project (scope: ${project.id})`
              return (
                <span title={tipText} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  fontSize: 9, fontWeight: 600, padding: '2px 7px', borderRadius: 4,
                  background: memOn ? 'rgba(124,108,243,0.1)' : 'rgba(255,255,255,0.04)',
                  border: `1px solid ${memOn ? 'rgba(124,108,243,0.25)' : 'var(--border-dim)'}`,
                  color: memOn ? 'var(--neural)' : 'var(--text-muted)', cursor: 'default',
                }}>
                  Memory: {memOn ? 'Accumulating' : agentMemEnabled ? 'Paused' : 'Disabled'}
                </span>
              )
            })()}
            {/* Back-to-conversations chip */}
            {projectConvId && (
              <button onClick={handleConvBack} className="chip"
                style={{ fontSize: 11, cursor: 'pointer' }} title="Back to conversations list">
                <MessageSquare size={9} /> Conversations <X size={9} />
              </button>
            )}
          </div>
        </div>

      </div>}{/* ── end header ── */}

      {/* Tab content — shown when no conversation is drilled into */}
      {!projectConvId && (
        <div className="ws-body">
          {projectBrowseTab === 'overview' || !projectBrowseTab ? (
            <ProjectOverviewBody
              projectId={project.id}
              synopsis={project.synopsis || null}
              workspacePath={project.workspace_path}
              onOpenConv={handleConvSelect}
            />
          ) : projectBrowseTab === 'conversations' ? (
            <ProjectConvList
              projectId={project.id}
              projectStatus={project.status}
              onSelect={handleConvSelect}
            />
          ) : projectBrowseTab === 'agents' ? (
            <ProjectAgentsTab projectId={project.id} />
          ) : projectBrowseTab === 'daemons' ? (
            <ProjectDaemonsTab projectId={project.id} />
          ) : projectBrowseTab === 'mcp' ? (
            <ProjectMCPTab projectId={project.id} />
          ) : projectBrowseTab === 'files' ? (
            <ProjectFilesTab projectId={project.id} />
          ) : projectBrowseTab === 'knowledge' ? (
            <ProjectKnowledgeTab projectId={project.id} />
          ) : (
            <ProjectArtifactTab projectId={project.id} type={projectBrowseTab as ArtifactType} />
          )}
        </div>
      )}

      {/* Conversation drill-in */}
      {projectConvId && (
        loadingConv ? (
          <div className="flex items-center justify-center flex-1 gap-2" style={{ color: 'var(--text-muted)' }}>
            <Loader2 size={14} className="animate-spin" /> Loading conversation…
          </div>
        ) : (
          <ProjectConvView onBack={handleConvBack} />
        )
      )}
    </div>
  )
}
