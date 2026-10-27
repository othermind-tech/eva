import { useEffect, useRef, useState } from 'react'
import { Loader2, ChevronRight, Bot, Monitor, Copy, RotateCcw, Trash2, Check, X, AlertTriangle, Edit3, Search, Plus, Cpu, Lock, Database } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { agentsApi } from '@/api/agents'
import { providersApi, toolsApi } from '@/api/tools'
import { mcpApi } from '@/api/mcp'
import { projectsApi } from '@/api/projects'
import SearchableSelect from '@/components/ui/SearchableSelect'
import { chatReadyProviderOptions, modelOptions } from '@/lib/providers'
import { parseProviderError } from '@/lib/errors'
import ProviderFieldError from '@/components/ui/ProviderFieldError'
import SystemPromptField from '@/components/ui/SystemPromptField'
import type {
  SubagentConfigRequest,
  ProviderListItem, LiveModelOut, Tool, MCPConfig,
} from '@/types/api'
import { useChatStore } from '@/store/chatStore'
import { useSessionStore } from '@/store/sessionStore'

// ── Agent detail / edit (Level 2) ─────────────────────────────────────────────
export default function AgentEditView({
  agentName, onBack, onSaved,
}: {
  agentName: string | null   // null = create mode
  onBack:    () => void
  onSaved:   () => void
}) {
  const qc     = useQueryClient()
  const isEdit = agentName !== null
  const activeProjectId = useSessionStore(s => s.activeProjectId)
  const setViewAgent    = useChatStore(s => s.setViewAgent)
  const toolPickerRef   = useRef<HTMLDivElement>(null)

  const { data: existing, isLoading: loadingAgent } = useQuery({
    queryKey: ['agent', agentName],
    queryFn:  () => agentsApi.getOne(agentName!),
    enabled:  isEdit,
    staleTime: 0,
  })

  const [name,          setName]         = useState('')
  const [description,   setDesc]         = useState('')
  const [systemPrompt,  setSysPrompt]    = useState('')
  const [editingPrompt, setEditingPrompt] = useState(false)
  const [promptSnapshot, setPromptSnapshot] = useState('')
  const [provider,      setProvider]     = useState('')
  const [model,         setModel]        = useState('')
  const [maxSteps,      setMaxSteps]     = useState(5)
  const [projectId,     setProjectId]    = useState(activeProjectId ?? '')
  const [selectedTools,   setSelectedTools]   = useState<string[]>([])
  const [selectedMcp,     setSelectedMcp]     = useState<string[]>([])
  const [toolPickerOpen,  setToolPickerOpen]  = useState(false)
  const [toolPickerSearch, setToolPickerSearch] = useState('')
  const [toolPickerSel,   setToolPickerSel]   = useState<string[]>([])
  const [fieldErrors,     setFieldErrors]     = useState<Record<string, string>>({})
  // Section 101 - why the server refused the provider (drives the "Add a key" shortcut)
  const [providerReason,  setProviderReason]  = useState<string | null>(null)
  const [serverError,   setServerError]  = useState<string | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [resetConfirm,  setResetConfirm]  = useState(false)
  const [cloneMode,     setCloneMode]     = useState(false)
  const [cloneName,     setCloneName]     = useState('')
  const [cloneError,    setCloneError]    = useState<string | null>(null)
  const [initialised,   setInitialised]  = useState(!isEdit)

  // 64-B — default-agent project assignment warning
  const [showDefaultProjectWarning, setShowDefaultProjectWarning] = useState(false)
  const [pendingProjectId,          setPendingProjectId]          = useState<string | null>(null)

  // True when any field differs from the saved value (only relevant in edit mode)
  const isDirty = !isEdit || !existing ? false : (
    name        !== (existing.name            ?? '')  ||
    description !== (existing.description     ?? '')  ||
    systemPrompt !== (existing.system_prompt  ?? '')  ||
    provider    !== (existing.provider        ?? '')  ||
    model       !== (existing.model           ?? '')  ||
    maxSteps    !== (existing.max_steps       ?? 5)   ||
    JSON.stringify([...selectedTools].sort()) !== JSON.stringify([...(existing.tools ?? [])].sort()) ||
    JSON.stringify([...selectedMcp].sort())   !== JSON.stringify([...(existing.allowed_mcp_servers ?? [])].sort())
  )

  useEffect(() => {
    if (!existing || initialised) return
    setName(existing.name)
    setDesc(existing.description ?? '')
    setSysPrompt(existing.system_prompt ?? '')
    setProvider(existing.provider ?? '')
    setModel(existing.model ?? '')
    setMaxSteps(existing.max_steps ?? 5)
    setSelectedTools(existing.tools ?? [])
    setSelectedMcp(existing.allowed_mcp_servers ?? [])
    setInitialised(true)
  }, [existing, initialised])

  // Close tool picker on outside click
  useEffect(() => {
    if (!toolPickerOpen) return
    const handler = (e: MouseEvent) => {
      if (toolPickerRef.current && !toolPickerRef.current.contains(e.target as Node))
        setToolPickerOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [toolPickerOpen])

  // All available tools (for the tool picker)
  const { data: allTools = [] } = useQuery<Tool[]>({
    queryKey: ['tools-all'],
    queryFn:  toolsApi.listAll,
    staleTime: 60_000,
  })

  // Providers + live models
  const { data: providers = [], isLoading: provsLoading } = useQuery<ProviderListItem[]>({
    queryKey: ['providers'],
    queryFn:  providersApi.list,
    staleTime: 30_000,
  })
  const [liveModels,    setLiveModels]    = useState<LiveModelOut[]>([])
  const [modelsLoading, setModelsLoading] = useState(false)
  useEffect(() => {
    if (!provider) { setLiveModels([]); setModel(''); return }
    let cancelled = false
    setModelsLoading(true)
    providersApi.getModels(provider)
      .then(d  => { if (!cancelled) { setLiveModels(d.models); setModelsLoading(false) } })
      .catch(() => { if (!cancelled) { setLiveModels([]);       setModelsLoading(false) } })
    return () => { cancelled = true }
  }, [provider])

  const { data: mcpConfigs = [] } = useQuery({ queryKey: ['mcp-configs'], queryFn: () => mcpApi.list(), staleTime: 30_000 })
  const { data: projects   = [] } = useQuery({ queryKey: ['projects'],    queryFn: projectsApi.list,  staleTime: 60_000 })

  const { mutate: submit, isPending: saving } = useMutation({
    mutationFn: () => {
      const payload: SubagentConfigRequest = {
        name:                name.trim(),
        description:         description.trim()  || undefined,
        system_prompt:       systemPrompt.trim() || undefined,
        provider:            provider            || undefined,
        model:               model.trim()        || undefined,
        tools:               selectedTools.length > 0 ? selectedTools : undefined,
        max_steps:           maxSteps,
        allowed_mcp_servers: selectedMcp.length  > 0 ? selectedMcp   : null,
        project_id:          projectId           || undefined,
      }
      return isEdit ? agentsApi.update(agentName!, payload) : agentsApi.create(payload)
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['agents'] }); onSaved() },
    onError: (err: unknown) => {
      const e = err as { status?: number; detail?: unknown }
      // Section 101 - a provider/model choice the server won't accept: show its own
      // sentence on the control it names, not a generic "Save failed".
      const problem = parseProviderError(err)
      if (problem) {
        setFieldErrors({ [problem.field === 'model' ? 'model' : 'provider']: problem.message })
        setProviderReason(problem.reason)
        return
      }
      if (e.status === 422 && Array.isArray(e.detail)) {
        const errs: Record<string, string> = {}
        for (const d of e.detail as Array<{ loc: string[]; msg: string }>)
          errs[d.loc[d.loc.length - 1] ?? 'form'] = d.msg
        setFieldErrors(errs)
      } else {
        setServerError(e.status === 404 ? 'Agent no longer exists.' : 'Save failed. Please try again.')
      }
    },
  })

  const { mutate: remove, isPending: removing } = useMutation({
    mutationFn: () => agentsApi.remove(agentName!),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['agents'] }); onBack() },
    onError:    () => setServerError('Delete failed.'),
  })

  const { mutate: doReset, isPending: resetting } = useMutation({
    mutationFn: () => agentsApi.resetAgent(agentName!),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['agents', agentName] }); setInitialised(false); setResetConfirm(false) },
    onError:    () => setServerError('Reset failed — definition not found.'),
  })

  const { mutate: doClone, isPending: cloning } = useMutation({
    mutationFn: () => agentsApi.cloneAgent(agentName!, cloneName.trim()),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['agents'] }); setCloneMode(false); setCloneName('') },
    onError:    (err: unknown) => {
      const e = err as { status?: number }
      setCloneError(e.status === 409 ? 'That name is already taken.' : 'Clone failed.')
    },
  })

  // 64-B — clone default agent then assign project
  const { mutate: doCloneAndAssign, isPending: cloneAssigning } = useMutation({
    mutationFn: async () => {
      const proj    = (projects as { id: string; name: string }[]).find(p => p.id === pendingProjectId)
      const slug    = proj?.name.toLowerCase().replace(/[^a-z0-9]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '') ?? 'project'
      const newName = `${agentName}-${slug}`
      await agentsApi.cloneAgent(agentName!, newName)
      await agentsApi.update(newName, { project_id: pendingProjectId! })
      return newName
    },
    onSuccess: (newName) => {
      qc.invalidateQueries({ queryKey: ['agents'] })
      setShowDefaultProjectWarning(false)
      setPendingProjectId(null)
      setViewAgent(newName)   // navigate to the new clone
    },
    onError: () => setServerError('Clone and assign failed. The name may already be taken.'),
  })

  const handleProjectChange = (pid: string) => {
    if (existing?.is_default && pid) {
      setPendingProjectId(pid)
      setShowDefaultProjectWarning(true)
      return
    }
    setProjectId(pid)
  }

  const handleSubmit = () => {
    const errs: Record<string, string> = {}
    if (!name.trim()) errs.name = 'Name is required'
    else if (!/^[a-z0-9_-]+$/.test(name.trim())) errs.name = 'Lowercase letters, numbers, hyphens, underscores only'
    if (maxSteps < 1 || maxSteps > 20) errs.max_steps = 'Must be 1–20'
    // Section 101 - the server rejects a provider override with no model (the run would
    // pair it with the session's model, which belongs to another provider).
    if (provider && !model.trim()) errs.model = 'A provider override needs a model too. Pick a model, or clear the provider to inherit from the session.'
    if (Object.keys(errs).length > 0) { setFieldErrors(errs); return }
    setFieldErrors({})
    setProviderReason(null)
    setServerError(null)
    submit()
  }

  const openToolPicker = () => {
    setToolPickerSel([])
    setToolPickerSearch('')
    setToolPickerOpen(true)
  }

  const confirmToolPicker = () => {
    setSelectedTools(prev => {
      const next = [...prev]
      for (const t of toolPickerSel) if (!next.includes(t)) next.push(t)
      return next
    })
    setToolPickerOpen(false)
  }

  const providerOptions = [
    { value: '', label: '— inherit from session —' },
    ...chatReadyProviderOptions(providers as ProviderListItem[], provider),
  ]

  if (isEdit && loadingAgent && !initialised) {
    return (
      <div className="detail">
        <div className="detail-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Loader2 size={20} className="animate-spin" style={{ color: 'var(--text-muted)' }} />
        </div>
      </div>
    )
  }

  // 64-B — default agent project warning dialog (modal overlay)
  if (showDefaultProjectWarning && pendingProjectId) {
    const projName = (projects as { id: string; name: string }[]).find(p => p.id === pendingProjectId)?.name ?? 'the project'
    return (
      <div className="detail">
        <div className="detail-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 280 }}>
          <div style={{
            background: 'var(--bg-surface2)',
            border: '1px solid rgba(245,158,11,0.35)',
            borderRadius: 16,
            padding: 28,
            maxWidth: 440,
            width: '100%',
          }}>
            <div className="flex items-start gap-3 mb-4">
              <div style={{ width: 36, height: 36, borderRadius: 10, background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                <AlertTriangle size={18} style={{ color: 'var(--amber)' }} />
              </div>
              <div>
                <p style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>
                  Default agents cannot be assigned to a project
                </p>
                <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  <strong style={{ color: 'var(--text-primary)' }}>{agentName}</strong> is a built-in EVA agent. To assign it to <strong style={{ color: 'var(--eva-primary)' }}>{projName}</strong>, a clone will be created and assigned automatically.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 justify-end">
              <button
                className="btn ghost"
                onClick={() => { setShowDefaultProjectWarning(false); setPendingProjectId(null) }}
                disabled={cloneAssigning}
              >
                Cancel
              </button>
              <button
                className="btn primary"
                onClick={() => doCloneAndAssign()}
                disabled={cloneAssigning}
              >
                {cloneAssigning
                  ? <><Loader2 size={11} className="animate-spin" /> Cloning…</>
                  : <><Copy size={11} /> Create Clone &amp; Assign</>
                }
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="detail">
      {/* Header */}
      <div className="detail-header">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-xs shrink-0"
          style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '2px 6px 2px 0', marginRight: 4 }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-primary)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
        >
          <ChevronRight size={11} style={{ transform: 'rotate(180deg)' }} /> Subagents
        </button>
        <div className="dh-icon" style={{ background: 'rgba(124,108,243,0.12)', borderColor: 'rgba(124,108,243,0.25)', color: 'var(--neural)' }}>
          <Bot size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">{isEdit ? name || agentName! : 'New Subagent'}</h2>
          <p className="dh-sub">
            {isEdit
              ? `${selectedTools.length} tools · max ${maxSteps} steps`
              : 'Configure a new sub-agent'}
          </p>
        </div>
        <div className="detail-actions">
          {isEdit && (
            <button
              className="btn ghost"
              onClick={() => setViewAgent(agentName!, {
                systemPrompt: existing?.system_prompt ?? undefined,
                model:        existing?.model         ?? undefined,
                provider:     existing?.provider      ?? undefined,
              })}
              title="Open in chat"
            >
              <Monitor size={12} /> Run now
            </button>
          )}
          {isEdit && (
            <button
              className="btn ghost"
              onClick={() => { setCloneMode(m => !m); setCloneName(''); setCloneError(null) }}
              title="Clone this agent"
              style={{ color: cloneMode ? 'var(--eva-primary)' : undefined }}
            >
              <Copy size={12} /> Clone
            </button>
          )}
          {(!isEdit || isDirty) && (
            <button className="btn primary" onClick={handleSubmit} disabled={saving}>
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
              {saving ? 'Saving…' : isEdit ? 'Save' : 'Create'}
            </button>
          )}
          {/* Default agents: Reset — only shown when dirty */}
          {isEdit && isDirty && existing?.is_default && !resetConfirm && (
            <button
              className="btn ghost"
              onClick={() => setResetConfirm(true)}
              style={{ border: '1px solid rgba(245,158,11,0.4)', color: 'var(--amber)' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(245,158,11,0.08)'; e.currentTarget.style.borderColor = 'var(--amber)' }}
              onMouseLeave={e => { e.currentTarget.style.background = ''; e.currentTarget.style.borderColor = 'rgba(245,158,11,0.4)' }}
            >
              <RotateCcw size={12} /> Reset
            </button>
          )}
          {isEdit && existing?.is_default && resetConfirm && (
            <div className="flex items-center gap-2">
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Reset to built-in?</span>
              <button className="btn ghost" style={{ color: 'var(--amber)', borderColor: 'rgba(245,158,11,0.4)' }} onClick={() => doReset()} disabled={resetting}>
                {resetting ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} Yes, reset
              </button>
              <button className="btn ghost" onClick={() => setResetConfirm(false)}>Cancel</button>
            </div>
          )}
          {isEdit && !existing?.is_default && !deleteConfirm && (
            <button
              className="btn ghost"
              onClick={() => setDeleteConfirm(true)}
              style={{ border: '1px solid rgba(255,77,109,0.5)', color: 'var(--danger)' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,77,109,0.08)'; e.currentTarget.style.borderColor = 'var(--danger)' }}
              onMouseLeave={e => { e.currentTarget.style.background = ''; e.currentTarget.style.borderColor = 'rgba(255,77,109,0.5)' }}
            >
              <Trash2 size={12} /> Delete
            </button>
          )}
          {isEdit && !existing?.is_default && deleteConfirm && (
            <div className="flex items-center gap-2">
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Confirm delete?</span>
              <button className="btn danger" onClick={() => remove()} disabled={removing}>
                {removing ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} Yes, delete
              </button>
              <button className="btn ghost" onClick={() => setDeleteConfirm(false)}>Cancel</button>
            </div>
          )}
        </div>
      </div>

      {/* Clone inline panel */}
      {cloneMode && (
        <div style={{ padding: '10px 20px 12px', borderBottom: '1px solid var(--border-dim)', background: 'rgba(0,201,255,0.03)', flexShrink: 0 }}>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>Clone <strong style={{ fontFamily: 'JetBrains Mono, monospace' }}>{agentName}</strong> as a new custom agent:</p>
          <div className="flex items-center gap-2">
            <input
              autoFocus
              className="form-input"
              style={{ width: 260, fontSize: 12.5, fontFamily: 'JetBrains Mono, monospace' }}
              value={cloneName}
              onChange={e => { setCloneName(e.target.value.replace(/\s+/g, '_').toLowerCase()); setCloneError(null) }}
              onKeyDown={e => {
                if (e.key === 'Enter') {
                  const t = cloneName.trim()
                  if (!t) return
                  if (!/^[a-z0-9_-]+$/.test(t)) { setCloneError('Lowercase, numbers, hyphens, underscores only'); return }
                  doClone()
                }
                if (e.key === 'Escape') { setCloneMode(false); setCloneName('') }
              }}
              placeholder={`${agentName}_custom`}
            />
            <button
              className="btn ghost"
              onClick={() => {
                const t = cloneName.trim()
                if (!t) return
                if (!/^[a-z0-9_-]+$/.test(t)) { setCloneError('Lowercase, numbers, hyphens, underscores only'); return }
                doClone()
              }}
              disabled={!cloneName.trim() || cloning}
            >
              {cloning ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} Clone
            </button>
            <button className="btn ghost" onClick={() => { setCloneMode(false); setCloneName('') }}>Cancel</button>
          </div>
          {cloneError && <p style={{ fontSize: 11, color: 'var(--danger)', marginTop: 4 }}>{cloneError}</p>}
        </div>
      )}

      {/* Body */}
      <div className="detail-body" style={{ maxWidth: 'none' }}>
        {serverError && (
          <div
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs mb-4"
            style={{ background: 'rgba(255,77,109,0.08)', border: '1px solid rgba(255,77,109,0.2)', color: 'var(--danger)' }}
          >
            <AlertTriangle size={11} /> {serverError}
          </div>
        )}

        {/* ── Section 1: Identity ── */}
        <div className="form-section">
          {!isEdit && (
            <div className="form-row">
              <label>Name *</label>
              <input
                className={`form-input font-mono${fieldErrors.name ? ' border-[var(--danger)]' : ''}`}
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="researcher"
              />
              {fieldErrors.name && <p className="text-xs mt-1" style={{ color: 'var(--danger)' }}>{fieldErrors.name}</p>}
            </div>
          )}
          <div className="form-row">
            <label>Role / Description</label>
            <textarea
              className="form-input"
              rows={4}
              value={description}
              onChange={e => setDesc(e.target.value)}
              placeholder="Brief summary shown to the orchestrator when choosing this agent…"
              style={{ resize: 'vertical', fontFamily: 'inherit' }}
            />
          </div>
          <div className="form-row" style={{ marginBottom: 0 }}>
            {/* Label row with inline edit controls */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', fontWeight: 600 }}>
                System Prompt
              </span>
              {!editingPrompt ? (
                <button
                  className="icon-btn"
                  style={{ width: 22, height: 22, padding: 0 }}
                  title="Edit system prompt"
                  onClick={() => { setPromptSnapshot(systemPrompt); setEditingPrompt(true) }}
                >
                  <Edit3 size={11} />
                </button>
              ) : (
                <div className="flex items-center gap-1.5">
                  <button
                    className="btn ghost"
                    style={{ fontSize: 11, padding: '3px 9px' }}
                    onClick={() => { setSysPrompt(promptSnapshot); setEditingPrompt(false) }}
                  >
                    Cancel
                  </button>
                  <button
                    className="btn primary"
                    style={{ fontSize: 11, padding: '3px 9px' }}
                    onClick={() => setEditingPrompt(false)}
                  >
                    <Check size={10} /> Save
                  </button>
                </div>
              )}
            </div>
            <SystemPromptField
              value={systemPrompt}
              onChange={setSysPrompt}
              context="agent"
              rows={20}
              showEnhance
              readOnly={!editingPrompt}
              placeholder="You are a specialist. Your job is to…"
            />
          </div>
        </div>

        {/* ── Section 2: Model ── */}
        <div className="form-section">
          <h3>Model</h3>
          <div className="form-row">
            <label>Provider</label>
            <SearchableSelect
              value={provider}
              onChange={v => { setProvider(v); setModel(''); setFieldErrors(f => ({ ...f, provider: '', model: '' })); setProviderReason(null) }}
              options={providerOptions}
              placeholder={provsLoading ? '— loading…' : '— inherit from session —'}
              loading={provsLoading}
            />
            {fieldErrors.provider && <ProviderFieldError message={fieldErrors.provider} reason={providerReason} />}
          </div>
          <div className="form-row">
            <label>Model</label>
            {provider ? (
              <SearchableSelect
                value={model}
                onChange={v => { setModel(v); setFieldErrors(f => ({ ...f, model: '' })) }}
                options={modelOptions(liveModels as LiveModelOut[], model, !modelsLoading)}
                placeholder={modelsLoading ? '— loading models…' : '— select model —'}
                loading={modelsLoading}
              />
            ) : (
              <SearchableSelect
                value=""
                onChange={() => {}}
                options={[{ value: '', label: '— inherit from session —' }]}
                placeholder="— inherit from session —"
                disabled
              />
            )}
            {fieldErrors.model && <ProviderFieldError message={fieldErrors.model} />}
          </div>
          <div className="form-row" style={{ marginBottom: 0 }}>
            <label>Max Steps: {maxSteps}</label>
            <input
              type="range" min={1} max={20}
              value={maxSteps}
              onChange={e => setMaxSteps(Number(e.target.value))}
              className="w-full accent-[var(--neural)]"
            />
            <div className="flex justify-between" style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
              <span>1</span><span>20</span>
            </div>
          </div>
        </div>

        {/* ── Section 3: Tools ── */}
        <div className="form-section">
          {existing?.is_default ? (
            /* Read-only tools notice for built-in agents */
            <div>
              <h3>Tools <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: 12 }}>· {selectedTools.length === 0 ? 'all available' : selectedTools.length}</span></h3>
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg" style={{ background: 'rgba(0,201,255,0.05)', border: '1px solid rgba(0,201,255,0.15)' }}>
                <Lock size={12} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
                <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: 0 }}>
                  Tool scope is managed by EVA. <span style={{ color: 'var(--eva-primary)', cursor: 'pointer' }} onClick={() => { setCloneMode(true); setCloneName(''); setCloneError(null) }}>Clone this agent</span> to customise tools.
                </p>
              </div>
              {selectedTools.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  {selectedTools.map(t => (
                    <div key={t} className="tool-row" style={{ opacity: 0.65 }}>
                      <Cpu size={13} style={{ color: 'var(--neural)', flexShrink: 0 }} />
                      <span className="tr-name">{t}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            /* Editable tools for custom agents */
            <div>
          <div ref={toolPickerRef} className="flex items-center mb-3" style={{ position: 'relative' }}>
            <h3 style={{ margin: 0 }}>Tools <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: 12 }}>· {selectedTools.length === 0 ? 'all available' : selectedTools.length}</span></h3>
            <button
              className="btn ghost"
              style={{ marginLeft: 'auto' }}
              onClick={openToolPicker}
            >
              <Plus size={12} /> Add tool
            </button>

            {/* Tool picker popover */}
            {toolPickerOpen && (() => {
              const filtered = (allTools as Tool[]).filter(t =>
                !selectedTools.includes(t.name) &&
                (t.name.toLowerCase().includes(toolPickerSearch.toLowerCase()) ||
                 t.description.toLowerCase().includes(toolPickerSearch.toLowerCase()))
              )
              return (
                <div
                  style={{
                    position: 'absolute', top: 'calc(100% + 6px)', right: 0,
                    width: 360, zIndex: 50,
                    background: 'var(--bg-surface2)',
                    border: '1px solid var(--border-mid)',
                    borderRadius: 12,
                    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
                    display: 'flex', flexDirection: 'column',
                    maxHeight: 380,
                  }}
                >
                  {/* Search */}
                  <div style={{ padding: '10px 12px 8px', borderBottom: '1px solid var(--border-dim)', flexShrink: 0 }}>
                    <div className="search-field">
                      <Search size={13} />
                      <input
                        autoFocus
                        placeholder="Search tools…"
                        value={toolPickerSearch}
                        onChange={e => setToolPickerSearch(e.target.value)}
                      />
                      {toolPickerSearch && (
                        <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setToolPickerSearch('')}>
                          <X size={11} />
                        </button>
                      )}
                    </div>
                  </div>

                  {/* List */}
                  <div style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>
                    {filtered.length === 0 && (
                      <p style={{ fontSize: 12, color: 'var(--text-muted)', textAlign: 'center', padding: '16px 0' }}>
                        {toolPickerSearch ? 'No tools match' : 'All tools already added'}
                      </p>
                    )}
                    {filtered.map(t => {
                      const checked = toolPickerSel.includes(t.name)
                      return (
                        <div
                          key={t.name}
                          onClick={() => setToolPickerSel(prev =>
                            checked ? prev.filter(x => x !== t.name) : [...prev, t.name]
                          )}
                          style={{
                            display: 'flex', alignItems: 'center', gap: 10,
                            padding: '7px 14px', cursor: 'pointer',
                            background: checked ? 'rgba(124,108,243,0.08)' : 'transparent',
                          }}
                          onMouseEnter={e => { if (!checked) e.currentTarget.style.background = 'var(--bg-surface3)' }}
                          onMouseLeave={e => { e.currentTarget.style.background = checked ? 'rgba(124,108,243,0.08)' : 'transparent' }}
                        >
                          <div style={{
                            width: 16, height: 16, borderRadius: 4, flexShrink: 0,
                            border: `1.5px solid ${checked ? 'var(--neural)' : 'var(--border-mid)'}`,
                            background: checked ? 'var(--neural)' : 'transparent',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                          }}>
                            {checked && <Check size={10} style={{ color: '#fff' }} />}
                          </div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: 12.5, fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-primary)', fontWeight: 500 }}>{t.name}</div>
                            <div style={{ fontSize: 11, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.description}</div>
                          </div>
                          <span className="chip chip-sm" style={{ fontSize: 10, flexShrink: 0, background: t.type === 'mcp' ? 'rgba(0,201,255,0.1)' : 'rgba(124,108,243,0.1)', color: t.type === 'mcp' ? 'var(--eva-primary)' : 'var(--neural)', border: 'none' }}>{t.type}</span>
                        </div>
                      )
                    })}
                  </div>

                  {/* Footer */}
                  <div style={{ padding: '8px 12px', borderTop: '1px solid var(--border-dim)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
                    <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      {toolPickerSel.length > 0 ? `${toolPickerSel.length} selected` : 'Select tools'}
                    </span>
                    <div className="flex gap-2">
                      <button className="btn ghost" onClick={() => setToolPickerOpen(false)}>Cancel</button>
                      <button className="btn primary" onClick={confirmToolPicker} disabled={toolPickerSel.length === 0}>
                        <Check size={11} /> Add {toolPickerSel.length > 0 ? toolPickerSel.length : ''}
                      </button>
                    </div>
                  </div>
                </div>
              )
            })()}
          </div>

          {selectedTools.length === 0 && (
            <p className="desc">No restriction — agent has access to all tools.</p>
          )}
          {selectedTools.map(t => (
            <div key={t} className="tool-row">
              <Cpu size={13} style={{ color: 'var(--neural)', flexShrink: 0 }} />
              <span className="tr-name">{t}</span>
              <button
                className="icon-btn"
                onClick={() => setSelectedTools(prev => prev.filter(x => x !== t))}
                title="Remove"
              >
                <X size={12} />
              </button>
            </div>
          ))}
            </div>
          )}
        </div>

        {/* ── Section 4: MCP Servers ── */}
        <div className="form-section">
          <h3>MCP Servers <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: 12 }}>· {selectedMcp.length === 0 ? 'all' : selectedMcp.length + ' scoped'}</span></h3>
          <p className="desc">Leave empty to allow access to all your enabled MCP servers.</p>
          {selectedMcp.map(s => (
            <div key={s} className="tool-row">
              <Database size={13} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
              <span className="tr-name">{s}</span>
              <button
                className="icon-btn"
                onClick={() => setSelectedMcp(prev => prev.filter(x => x !== s))}
                title="Remove"
              >
                <X size={12} />
              </button>
            </div>
          ))}
          {(mcpConfigs as MCPConfig[]).filter(c => !selectedMcp.includes(c.name)).length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {(mcpConfigs as MCPConfig[]).filter(c => !selectedMcp.includes(c.name)).map(c => (
                <button
                  key={c.name}
                  className="btn ghost"
                  style={{ fontSize: 11, padding: '3px 10px' }}
                  onClick={() => setSelectedMcp(prev => [...prev, c.name])}
                >
                  <Plus size={10} /> {c.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* ── Section 5: Project scope ── */}
        <div className="form-section">
          <h3>Project Scope</h3>
          <div className="form-row" style={{ marginBottom: 0 }}>
            <label>Project (optional)</label>
            <select
              className="form-input"
              value={projectId}
              onChange={e => handleProjectChange(e.target.value)}
            >
              <option value="">— no project —</option>
              {(projects as { id: string; name: string }[]).map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
        </div>

      </div>
    </div>
  )
}
