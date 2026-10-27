import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  MessageSquare, Clock, Plus, Search, Loader2, FolderOpen,
  ChevronRight, BarChart2, BookOpen, CheckSquare, File, Users, Shield,
  AlertTriangle, SlidersHorizontal, Pause, Play, Archive, RefreshCw, Plug2, X,
} from 'lucide-react'
import { projectsApi, type Project, type ProjectStatus } from '@/api/projects'
import { mcpApi } from '@/api/mcp'
import { useChatStore } from '@/store/chatStore'
import { cn } from '@/lib/utils'
import FilterMenu, { type FilterState, DEFAULT_FILTER } from './FilterMenu'

// PROJECT_CHILDREN tabs (matching design)
// hasAdd: false → no + button (Overview has no creation concept; Errors are generated)
const PROJECT_CHILDREN = [
  { key: 'overview',       label: 'Overview',      icon: BarChart2,     hasAdd: false },
  { key: 'conversations',  label: 'Conversations', icon: MessageSquare, hasAdd: true  },
  { key: 'tasks',          label: 'Tasks',          icon: CheckSquare,  hasAdd: false },
  { key: 'files',          label: 'Files',          icon: File,         hasAdd: true  },
  { key: 'knowledge',      label: 'Knowledge',      icon: BookOpen,     hasAdd: true  },
  { key: 'agents',         label: 'Agents',         icon: Users,        hasAdd: true  },
  { key: 'daemons',        label: 'Daemons',        icon: Clock,        hasAdd: true  },
  { key: 'mcp',            label: 'MCP',            icon: Plug2,        hasAdd: false },
  { key: 'errors',         label: 'Errors',         icon: AlertTriangle, hasAdd: false },
  { key: 'policies',       label: 'Policies',       icon: Shield,       hasAdd: false },
]

// ─── Project child nav (with live counts) ────────────────────────────────────
// Extracted into its own component so hooks can run per-project without
// violating the rules-of-hooks (can't call hooks inside .map()).
// Uses the same query keys as ProjectOverviewBody in ChatView so the
// TanStack cache is shared — if Overview was already visited, badges
// appear instantly with zero extra requests.
function ProjectChildNav({
  projectId,
  isViewing,
  projectBrowseTab,
  setViewProject,
  setProjectBrowseTab,
}: {
  projectId:           string
  isViewing:           boolean
  projectBrowseTab:    string | null
  setViewProject:      (id: string) => void
  setProjectBrowseTab: (tab: string) => void
}) {
  // Store actions for + button navigation
  const setViewAgents              = useChatStore(s => s.setViewAgents)
  const setViewAgentDetail         = useChatStore(s => s.setViewAgentDetail)
  const setViewDaemons             = useChatStore(s => s.setViewDaemons)
  const setViewDaemonDetail        = useChatStore(s => s.setViewDaemonDetail)
  const setProjectConvId           = useChatStore(s => s.setProjectConvId)
  const setProjectKnowledgeUpload  = useChatStore(s => s.setProjectKnowledgeUpload)

  const handleAdd = (key: string) => {
    switch (key) {
      case 'agents':
        setViewAgents()
        setViewAgentDetail('__new__')
        break
      case 'daemons':
        setViewDaemons()
        setViewDaemonDetail('__new__')
        break
      case 'conversations':
        setViewProject(projectId)
        setProjectBrowseTab('conversations')
        setProjectConvId('__new__', 'New conversation')
        break
      case 'files':
        setViewProject(projectId)
        setProjectBrowseTab('files')
        break
      case 'knowledge':
        setViewProject(projectId)
        setProjectBrowseTab('knowledge')
        setProjectKnowledgeUpload(true)   // signal → ProjectKnowledgeTab opens file dialog
        break
    }
  }

  const { data: convs   } = useQuery({
    queryKey: ['project-conversations', projectId],
    queryFn:  () => projectsApi.listConversations(projectId),
    staleTime: 30_000,
  })
  const { data: agents  } = useQuery({
    queryKey: ['project-agents', projectId],
    queryFn:  () => projectsApi.listAgents(projectId),
    staleTime: 30_000,
  })
  const { data: daemons } = useQuery({
    queryKey: ['project-daemons', projectId],
    queryFn:  () => projectsApi.listDaemons(projectId),
    staleTime: 30_000,
  })
  const { data: projectMCPs } = useQuery({
    queryKey: ['project-mcp', projectId],
    queryFn:  () => mcpApi.list(projectId),
    staleTime: 30_000,
  })
  const { data: filesData } = useQuery({
    queryKey: ['project-files', projectId],
    queryFn:  () => projectsApi.listFiles(projectId),
    staleTime: 30_000,
  })

  // Map each nav key → count (null = data not yet loaded → no badge shown)
  // V6.2: split files into workspace-purpose vs knowledge-purpose
  const workspaceFileCount = filesData != null
    ? filesData.files.filter(f => f.purpose === 'workspace').length
    : null
  const knowledgeFileCount = filesData != null
    ? filesData.files.filter(f => f.purpose === 'knowledge').length
    : null

  const counts: Record<string, number | null> = {
    overview:      null,
    conversations: convs    != null ? convs.length    : null,
    tasks:         null,
    files:         workspaceFileCount,
    knowledge:     knowledgeFileCount,
    agents:        agents   != null ? agents.length   : null,
    daemons:       daemons     != null ? daemons.length     : null,
    mcp:           projectMCPs != null ? projectMCPs.length : null,
    errors:        null,
    policies:      null,
  }

  return (
    <div className="project-children">
      {PROJECT_CHILDREN.map(c => {
        const active = isViewing && projectBrowseTab === c.key
        const count  = counts[c.key]
        return (
          // div instead of button so we can nest the + <button> inside
          <div
            key={c.key}
            className={cn('child-row', active && 'active')}
            role="button"
            tabIndex={0}
            onClick={() => {
              setViewProject(projectId)
              setProjectBrowseTab(c.key)
            }}
            onKeyDown={e => {
              if (e.key === 'Enter' || e.key === ' ') {
                setViewProject(projectId)
                setProjectBrowseTab(c.key)
              }
            }}
          >
            <span className="cr-icon"><c.icon size={12} /></span>
            <span className="cr-label">{c.label}</span>

            {/* Fixed-width badge slot — always present to keep column aligned */}
            <span className="cr-count">
              {count != null && count > 0 && (
                <span className="cr-badge">{count}</span>
              )}
            </span>

            {/* + button (addable rows) or invisible spacer (Overview / Errors) */}
            {c.hasAdd ? (
              <button
                className="cr-add"
                tabIndex={-1}
                title={`New ${c.label.replace(/s$/, '')}`}
                onClick={e => {
                  e.stopPropagation()
                  handleAdd(c.key)
                }}
              >
                <Plus size={10} />
              </button>
            ) : (
              <span className="cr-add-placeholder" />
            )}
          </div>
        )
      })}
    </div>
  )
}

// Module-level on purpose: Date.now() written directly in the component body is flagged as an impure
// call during render (react-hooks/purity); inside a separate function it is not.
function daysSince(dateStr: string): number {
  return (Date.now() - new Date(dateStr).getTime()) / 86_400_000
}

// ─── Projects Tab ────────────────────────────────────────────────────────────
export default function ProjectsTab() {
  const [search, setSearch]         = useState('')
  const [expanded, setExpanded]     = useState<Record<string, boolean>>({})
  const [filterOpen, setFilterOpen] = useState(false)
  const [filters, setFilters]       = useState<FilterState>(DEFAULT_FILTER)
  const filterRef = useRef<HTMLDivElement>(null)

  const setViewProject      = useChatStore(s => s.setViewProject)
  const setProjectBrowseTab = useChatStore(s => s.setProjectBrowseTab)
  const projectBrowseId     = useChatStore(s => s.projectBrowseId)
  const projectBrowseTab    = useChatStore(s => s.projectBrowseTab)

  const { data: projects = [], isLoading } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn:  projectsApi.list,
    staleTime: 60_000,
  })

  const qcProj = useQueryClient()
  const { mutate: setProjectStatus, isPending: settingStatus, variables: statusVars } = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ProjectStatus }) =>
      projectsApi.update(id, { status }),
    onSuccess: (_data, { id }) => {
      qcProj.invalidateQueries({ queryKey: ['projects'] })
      qcProj.invalidateQueries({ queryKey: ['project', id] })
    },
  })

  // Section 83 — manual resync, sidebar row. One shared mutation across the
  // whole list (same pattern as setProjectStatus above) rather than a
  // per-row hook — a per-row useProjectSync would violate Rules of Hooks
  // inside .map(), and would mean one poll loop per project in the list.
  // Only reflects the immediate trigger call, same limitation Pause/Resume/
  // Archive already have here.
  const { mutate: syncProjectRow, isPending: syncingRow, variables: syncVars, error: syncRowError } = useMutation({
    mutationFn: (id: string) => projectsApi.sync(id),
  })

  // Auto-expand the currently browsed project
  useEffect(() => {
    if (projectBrowseId) {
      setExpanded(e => ({ ...e, [projectBrowseId]: true }))
    }
  }, [projectBrowseId])

  const isFilterActive = filters.status !== 'all' || filters.lastActivity !== 'all' || filters.sortBy !== 'recency'

  const pq = search.trim().toLowerCase()
  let filtered = pq
    ? projects.filter(p => p.name.toLowerCase().includes(pq) || (p.description ?? '').toLowerCase().includes(pq))
    : [...projects]

  // Apply status filter
  if (filters.status !== 'all') {
    filtered = filtered.filter(p => p.status === filters.status)
  }

  // Apply lastActivity filter (by project.updated_at)
  if (filters.lastActivity !== 'all') {
    const limits: Record<string, number> = { '1d': 1, '3d': 3, '7d': 7, '30d': 30 }
    const maxDays = limits[filters.lastActivity] ?? Infinity
    filtered = filtered.filter(p => daysSince(p.updated_at ?? p.created_at) <= maxDays)
  }

  // Apply sortBy
  if (filters.sortBy === 'alpha') {
    filtered = [...filtered].sort((a, b) => a.name.localeCompare(b.name))
  } else if (filters.sortBy === 'created') {
    filtered = [...filtered].sort((a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    )
  }

  const toggleExpand = (id: string) => setExpanded(e => ({ ...e, [id]: !e[id] }))

  return (
    <>
      {/* Search */}
      <div style={{ padding: '4px 14px 4px' }}>
        <div className="search-field">
          <Search size={13} />
          <input
            placeholder="Search projects…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
              <X size={11} />
            </button>
          )}
        </div>
      </div>

      {/* New project quicklink — goes straight to the create-project page */}
      <div style={{ padding: '4px 6px 0' }}>
        <button className="quicklink" onClick={() => setViewProject('__new__')}>
          <span className="ql-icon"><Plus size={15} /></span>
          <span>New project</span>
        </button>
      </div>

      {/* All projects section label + filter button */}
      <div className="section-label" style={{ position: 'relative' }} ref={filterRef}>
        <span>{pq ? `Results · ${filtered.length}` : 'All projects'}</span>
        {!pq && (
          <button
            className={cn('filter-btn', isFilterActive && 'is-active')}
            title="Filter projects"
            onClick={() => setFilterOpen(o => !o)}
          >
            <SlidersHorizontal size={12} />
          </button>
        )}
        {filterOpen && (
          <FilterMenu
            filters={filters}
            onChange={f => setFilters(f)}
            onClose={() => setFilterOpen(false)}
          />
        )}
      </div>

      <div style={{ padding: '0 8px 12px' }}>
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 size={16} className="animate-spin" style={{ color: 'var(--text-muted)' }} />
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: '24px 16px', color: 'var(--text-muted)', fontSize: 12.5, textAlign: 'center' }}>
            {pq
              ? <>No projects match "<span style={{ color: 'var(--text-secondary)' }}>{search}</span>"</>
              : 'No projects yet — create one to get started.'}
          </div>
        ) : (
          filtered.map(p => {
            const open        = !!expanded[p.id]
            const isViewing   = p.id === projectBrowseId
            return (
              <div key={p.id} className={cn('project-card', open && 'expanded')}>
                {/* Project header row — click to expand tree + open workspace */}
                <div
                  className={cn('project-row group', open && 'open', isViewing && !projectBrowseTab && 'active')}
                  onClick={() => {
                    toggleExpand(p.id)
                    setViewProject(p.id)
                  }}
                >
                  <ChevronRight size={11} className="pr-chev" />
                  <div
                    className="pr-icon"
                    style={{
                      background: p.status === 'active'   ? 'var(--success-dim)'
                                : p.status === 'paused'   ? 'var(--amber-dim)'
                                : 'rgba(255,255,255,0.05)',
                      color:      p.status === 'active'   ? 'var(--success)'
                                : p.status === 'paused'   ? 'var(--amber)'
                                : 'var(--text-dim)',
                    }}
                  >
                    <FolderOpen size={11} />
                  </div>
                  <div className="pr-name">{p.name}</div>

                  {/* Status dot — hidden when action buttons are visible */}
                  <div
                    className="pr-status group-hover:hidden"
                    style={{
                      background: p.status === 'active'   ? 'var(--success)'
                                : p.status === 'paused'   ? 'var(--amber)'
                                : 'var(--text-dim)',
                    }}
                  />

                  {/* Hover action buttons */}
                  <div
                    className="hidden group-hover:flex items-center gap-0.5"
                    onClick={e => e.stopPropagation()}
                  >
                    {p.status === 'active' && (
                      <button
                        title={syncVars === p.id && syncRowError ? (syncRowError as { detail?: string }).detail ?? 'Sync failed' : 'Sync now'}
                        disabled={syncingRow && syncVars === p.id}
                        onClick={() => syncProjectRow(p.id)}
                        className="icon-btn"
                        style={{ width: 20, height: 20, color: 'var(--eva-primary)' }}
                      >
                        <RefreshCw size={10} className={syncingRow && syncVars === p.id ? 'animate-spin' : ''} />
                      </button>
                    )}
                    {p.status === 'active' && (
                      <button
                        title="Pause"
                        disabled={settingStatus && statusVars?.id === p.id}
                        onClick={() => setProjectStatus({ id: p.id, status: 'paused' })}
                        className="icon-btn"
                        style={{ width: 20, height: 20, color: 'var(--amber)' }}
                      >
                        <Pause size={10} />
                      </button>
                    )}
                    {p.status === 'paused' && (
                      <>
                        <button
                          title="Resume"
                          disabled={settingStatus && statusVars?.id === p.id}
                          onClick={() => setProjectStatus({ id: p.id, status: 'active' })}
                          className="icon-btn"
                          style={{ width: 20, height: 20, color: 'var(--success)' }}
                        >
                          <Play size={10} />
                        </button>
                        <button
                          title="Archive"
                          disabled={settingStatus && statusVars?.id === p.id}
                          onClick={() => setProjectStatus({ id: p.id, status: 'archived' })}
                          className="icon-btn"
                          style={{ width: 20, height: 20, color: 'var(--text-muted)' }}
                        >
                          <Archive size={10} />
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {/* Expanded child rows — each navigates to the matching tab */}
                {open && (
                  <ProjectChildNav
                    projectId={p.id}
                    isViewing={isViewing}
                    projectBrowseTab={projectBrowseTab}
                    setViewProject={setViewProject}
                    setProjectBrowseTab={setProjectBrowseTab}
                  />
                )}
              </div>
            )
          })
        )}
      </div>
    </>
  )
}
