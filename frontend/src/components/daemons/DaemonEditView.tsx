import { useEffect, useState } from 'react'
import { Loader2, ChevronDown, ChevronRight, Clock, Check, Edit3, Trash2, Play, RefreshCw, AlertTriangle, Pause, History, Zap, RotateCcw, X } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { daemonsApi } from '@/api/daemons'
import { providersApi, toolsApi } from '@/api/tools'
import SearchableSelect from '@/components/ui/SearchableSelect'
import { chatReadyProviderOptions, modelOptions } from '@/lib/providers'
import { parseProviderError } from '@/lib/errors'
import ProviderFieldError from '@/components/ui/ProviderFieldError'
import SystemPromptField from '@/components/ui/SystemPromptField'
import type {
  ProviderListItem, LiveModelOut, Tool,
  DaemonConfig, DaemonConfigRequest,
} from '@/types/api'
import { projectsApi } from '@/api/projects'
import { formatDate } from '@/lib/utils'
import { useSessionStore } from '@/store/sessionStore'
import { formatInterval, formatRelativeTime, type DaemonTriggerState } from './daemonFormat'
import DaemonLifecycleBadge from './DaemonLifecycleBadge'
import DaemonRunHistoryTab from './DaemonRunHistoryTab'
import DaemonWebhookSection from './DaemonWebhookSection'

function DaemonToggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div
      onClick={() => onChange(!value)}
      style={{ width: 32, height: 16, borderRadius: 999, background: value ? 'var(--eva-primary)' : 'var(--bg-surface3)', position: 'relative', cursor: 'pointer', flexShrink: 0, transition: 'background 0.2s' }}
    >
      <div style={{ position: 'absolute', top: 2, width: 12, height: 12, borderRadius: '50%', background: '#fff', left: value ? 18 : 2, transition: 'left 0.2s' }} />
    </div>
  )
}

// ── Daemon detail / edit (Level 2) ────────────────────────────────────────────
export default function DaemonEditView({
  daemonName, onBack, onSaved,
}: {
  daemonName: string | null
  onBack:     () => void
  onSaved:    () => void
}) {
  const qc    = useQueryClient()
  const isEdit = daemonName !== null
  const activeProjectId = useSessionStore(s => s.activeProjectId)

  const { data: existing, isLoading: loadingDaemon } = useQuery<DaemonConfig>({
    queryKey:  ['daemon', daemonName],
    queryFn:   () => daemonsApi.getOne(daemonName!),
    enabled:   isEdit,
    staleTime: 0,
  })

  // Form fields
  const [name,            setName]         = useState('')
  const [description,     setDesc]         = useState('')
  const [systemPrompt,    setSysPrompt]    = useState('')
  const [triggerMessage,  setTriggerMsg]   = useState('')
  const [provider,        setProvider]     = useState('')
  const [model,           setModel]        = useState('')
  const [projectId,       setProjectId]    = useState(activeProjectId ?? '')
  const [kind,            setKind]         = useState<'standard' | 'supervisor'>('standard')
  const [maxSteps,        setMaxSteps]     = useState(5)
  const [intervalMode,    setIntervalMode] = useState<'manual' | 'scheduled'>('manual')
  const [intervalSeconds, setIntervalSecs] = useState<number>(3600)
  const [enabled,         setEnabled]      = useState(true)
  const [selectedTools,   setSelectedTools] = useState<string[]>([])
  const [selectedEvents,  setSelectedEvents] = useState<string[]>([])
  const [retryOnFailure,  setRetryOnFailure] = useState(true)
  const [maxRetries,      setMaxRetries]    = useState(3)
  const [retryDelaySecs,  setRetryDelaySecs] = useState(300)
  const [toolsOpen,       setToolsOpen]    = useState(false)
  const [eventsOpen,      setEventsOpen]   = useState(false)
  const [retryOpen,       setRetryOpen]    = useState(false)
  const [webhookOpen,     setWebhookOpen]  = useState(false)
  const [activeTab,       setActiveTab]    = useState<'config' | 'history'>('config')
  const [fieldErrors,     setFieldErrors]  = useState<Record<string, string>>({})
  // Section 101 - why the server refused the provider (drives the "Add a key" shortcut)
  const [providerReason,  setProviderReason] = useState<string | null>(null)
  const [serverError,     setServerError]  = useState<string | null>(null)
  const [deleteConfirm,   setDeleteConfirm] = useState(false)
  const [triggerState,    setTriggerState]  = useState<DaemonTriggerState>('idle')
  const [initialised,     setInitialised]   = useState(!isEdit)
  const [editingDesc,          setEditingDesc]         = useState(false)
  const [descSnapshot,         setDescSnapshot]         = useState('')
  const [editingDaemonPrompt,  setEditingDaemonPrompt]  = useState(false)
  const [daemonPromptSnapshot, setDaemonPromptSnapshot] = useState('')

  // True when any field differs from the saved daemon config
  const isDirty = !isEdit || !existing ? false : (
    name            !== (existing.name                  ?? '')   ||
    description     !== (existing.description           ?? '')   ||
    systemPrompt    !== (existing.system_prompt         ?? '')   ||
    triggerMessage  !== (existing.trigger_message       ?? '')   ||
    provider        !== (existing.provider              ?? '')   ||
    model           !== (existing.model                 ?? '')   ||
    maxSteps        !== (existing.max_steps             ?? 5)    ||
    kind            !== (existing.kind === 'supervisor' ? 'supervisor' : 'standard') ||
    enabled         !== (existing.enabled               ?? true) ||
    retryOnFailure  !== (existing.retry_on_failure      ?? true) ||
    maxRetries      !== (existing.max_retries           ?? 3)    ||
    retryDelaySecs  !== (existing.retry_delay_seconds   ?? 300)  ||
    (intervalMode === 'scheduled' ? intervalSeconds : null) !== (existing.run_interval_seconds ?? null) ||
    JSON.stringify([...selectedTools].sort())  !== JSON.stringify([...(existing.tools               ?? [])].sort()) ||
    JSON.stringify([...selectedEvents].sort()) !== JSON.stringify([...(existing.event_subscriptions ?? [])].sort())
  )

  useEffect(() => {
    if (!existing || initialised) return
    setName(existing.name)
    setDesc(existing.description ?? '')
    setSysPrompt(existing.system_prompt ?? '')
    setTriggerMsg(existing.trigger_message ?? '')
    setProvider(existing.provider ?? '')
    setModel(existing.model ?? '')
    setProjectId(existing.project_id ?? activeProjectId ?? '')
    setKind(existing.kind === 'supervisor' ? 'supervisor' : 'standard')
    setMaxSteps(existing.max_steps ?? 5)
    setIntervalMode(existing.run_interval_seconds ? 'scheduled' : 'manual')
    setIntervalSecs(existing.run_interval_seconds ?? 3600)
    setEnabled(existing.enabled ?? true)
    setSelectedTools(existing.tools ?? [])
    setSelectedEvents(existing.event_subscriptions ?? [])
    setRetryOnFailure(existing.retry_on_failure ?? true)
    setMaxRetries(existing.max_retries ?? 3)
    setRetryDelaySecs(existing.retry_delay_seconds ?? 300)
    setInitialised(true)
  }, [existing, initialised, activeProjectId])

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
      .catch(() => { if (!cancelled) { setLiveModels([]);      setModelsLoading(false) } })
    return () => { cancelled = true }
  }, [provider])

  const { data: allTools = [] } = useQuery<Tool[]>({
    queryKey: ['tools-all'],
    queryFn:  toolsApi.listAll,
    staleTime: 60_000,
  })

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn:  projectsApi.list,
    staleTime: 60_000,
  })

  // Derive project pause state from the linked project's actual status.
  // The daemon's lifecycle_status may still be 'permanently_failed' even when its
  // project is paused — so we cannot rely on lifecycle_status alone.
  const linkedProject   = (projects as import('@/api/projects').Project[]).find(p => p.id === projectId)
  const projectIsPaused = linkedProject?.status === 'paused' || linkedProject?.status === 'archived'

  const { mutate: submit, isPending: saving } = useMutation({
    mutationFn: () => {
      const payload: DaemonConfigRequest = {
        name:                 name.trim(),
        description:          description.trim()     || undefined,
        system_prompt:        systemPrompt.trim(),
        trigger_message:      triggerMessage.trim()  || undefined,
        provider:             provider               || undefined,
        model:                model.trim()           || undefined,
        tools:                selectedTools.length > 0 ? selectedTools : undefined,
        // 'system' is server-reserved — never send kind at all when editing
        // one, rather than relying solely on the backend's own strip-on-save
        // guard for it.
        kind:                 existing?.kind === 'system' ? undefined : kind,
        max_steps:            maxSteps,
        run_interval_seconds: intervalMode === 'scheduled' ? intervalSeconds : null,
        enabled,
        retry_on_failure:     retryOnFailure,
        max_retries:          retryOnFailure ? maxRetries      : undefined,
        retry_delay_seconds:  retryOnFailure ? retryDelaySecs  : undefined,
        event_subscriptions:  selectedEvents.length > 0 ? selectedEvents : null,
        project_id:           projectId || undefined,
      }
      return isEdit ? daemonsApi.update(daemonName!, payload) : daemonsApi.create(payload)
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['daemons'] }); onSaved() },
    onError: (err: unknown) => {
      const e = err as { status?: number; detail?: unknown }
      // Section 101 - show the server's own sentence on the control it names
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
      } else if (e.status === 403) {
        setServerError('Project is paused — resume the project to enable or disable this daemon.')
      } else {
        setServerError(e.status === 404 ? 'Daemon no longer exists.' : 'Save failed. Please try again.')
      }
    },
  })

  const { mutate: remove, isPending: removing } = useMutation({
    mutationFn: () => daemonsApi.remove(daemonName!),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['daemons'] }); onBack() },
    onError:    () => setServerError('Delete failed.'),
  })

  const { mutate: pauseDaemon } = useMutation({
    mutationFn: () => daemonsApi.pause(daemonName!),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['daemon', daemonName] }),
  })
  const { mutate: resumeDaemon } = useMutation({
    mutationFn: () => daemonsApi.resume(daemonName!),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['daemon', daemonName] }),
  })
  const { mutate: resetFailures } = useMutation({
    mutationFn: () => daemonsApi.resetFailures(daemonName!),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['daemon', daemonName] }),
  })

  const handleTrigger = async () => {
    if (triggerState === 'loading' || !daemonName) return
    setTriggerState('loading')
    try {
      await daemonsApi.trigger(daemonName)
      setTriggerState('ok')
      setTimeout(() => setTriggerState('idle'), 3000)
    } catch (err: unknown) {
      const e = err as { status?: number }
      setTriggerState('error')
      setTimeout(() => setTriggerState('idle'), 3000)
      if (e.status === 403) {
        setServerError('Cannot trigger — project is paused. Resume the project first.')
      }
    }
  }

  const handleSubmit = () => {
    const errs: Record<string, string> = {}
    if (!name.trim()) errs.name = 'Name is required'
    else if (!/^[a-z0-9_-]+$/.test(name.trim())) errs.name = 'Lowercase letters, numbers, hyphens, underscores only'
    if (!systemPrompt.trim()) errs.system_prompt = 'System prompt is required'
    if (maxSteps < 1 || maxSteps > 50) errs.max_steps = 'Must be 1–50'
    if (intervalMode === 'scheduled' && intervalSeconds < 60) errs.run_interval_seconds = 'Minimum interval is 60 seconds'
    if (retryOnFailure && retryDelaySecs < 60) errs.retry_delay_seconds = 'Minimum retry delay is 60 seconds'
    // Section 101 - a provider override needs a model (see AgentEditView)
    if (provider && !model.trim()) errs.model = 'A provider override needs a model too. Pick a model, or clear the provider to inherit from the session.'
    if (Object.keys(errs).length > 0) {
      setFieldErrors(errs)
      // The system prompt field is collapsed to read-only behind an edit
      // icon by default — force it open so the required-field error is
      // actually visible next to an editable input, not just a stray line
      // of red text under a locked field.
      if (errs.system_prompt) { setDaemonPromptSnapshot(systemPrompt); setEditingDaemonPrompt(true) }
      return
    }
    setFieldErrors({})
    setProviderReason(null)
    setServerError(null)
    submit()
  }

  const toggleTool  = (t: string) => setSelectedTools(p => p.includes(t) ? p.filter(x => x !== t) : [...p, t])
  const toggleEvent = (ev: string) => setSelectedEvents(p => p.includes(ev) ? p.filter(x => x !== ev) : [...p, ev])

  const PRESETS = [
    { label: '1h',  secs: 3_600   },
    { label: '6h',  secs: 21_600  },
    { label: '12h', secs: 43_200  },
    { label: '24h', secs: 86_400  },
    { label: '7d',  secs: 604_800 },
  ]

  const AVAILABLE_EVENTS = [
    { key: 'execution.completed', label: 'execution.completed — trigger when any execution completes' },
    { key: 'execution.failed',    label: 'execution.failed — trigger when any execution fails' },
  ]

  const ls = existing?.lifecycle_status

  if (isEdit && loadingDaemon && !initialised) {
    return (
      <div className="detail">
        <div className="detail-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Loader2 size={20} className="animate-spin" style={{ color: 'var(--text-muted)' }} />
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
          <ChevronRight size={11} style={{ transform: 'rotate(180deg)' }} /> Daemons
        </button>
        <div className="dh-icon" style={{ background: 'rgba(0,201,255,0.1)', borderColor: 'rgba(0,201,255,0.25)', color: 'var(--eva-primary)' }}>
          <Clock size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <h2 className="dh-title">{isEdit ? name || daemonName! : 'New Daemon'}</h2>
            {isEdit && existing?.lifecycle_status && (
              <DaemonLifecycleBadge status={existing.lifecycle_status} />
            )}
          </div>
          <p className="dh-sub">
            {isEdit
              ? `${formatInterval(existing?.run_interval_seconds)} · max ${maxSteps} steps`
              : 'Configure a new daemon agent'}
          </p>
        </div>
        <div className="detail-actions">
          {isEdit && (
            (projectIsPaused || ls === 'project_paused') ? (
              <span
                title="Cannot trigger — project is paused. Resume the project first."
                style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--amber)', opacity: 0.6, cursor: 'not-allowed', padding: '5px 10px', borderRadius: 8, border: '1px solid rgba(245,158,11,0.25)', background: 'rgba(245,158,11,0.06)' }}
              >
                <Play size={12} /> Run now
              </span>
            ) : (
              <button
                className="btn ghost"
                onClick={handleTrigger}
                disabled={triggerState === 'loading'}
                title="Trigger now"
                style={{ color: triggerState === 'ok' ? 'var(--success)' : triggerState === 'error' ? 'var(--danger)' : undefined }}
              >
                {triggerState === 'loading' ? <Loader2 size={12} className="animate-spin" />
                 : triggerState === 'ok'    ? <Check size={12} />
                 : triggerState === 'error' ? <AlertTriangle size={12} />
                 : <Play size={12} />}
                {triggerState === 'loading' ? 'Triggering…' : triggerState === 'ok' ? 'Triggered!' : triggerState === 'error' ? 'Failed' : 'Run now'}
              </button>
            )
          )}
          {(!isEdit || isDirty) && (
            <button className="btn primary" onClick={handleSubmit} disabled={saving}>
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
              {saving ? 'Saving…' : isEdit ? 'Save' : 'Create Daemon'}
            </button>
          )}
          {isEdit && !deleteConfirm && (
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
          {isEdit && deleteConfirm && (
            <>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Delete daemon?</span>
              <button className="btn danger" style={{ fontSize: 12 }} onClick={() => remove()} disabled={removing}>
                {removing ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} Confirm
              </button>
              <button className="btn ghost" style={{ fontSize: 12 }} onClick={() => setDeleteConfirm(false)}>Cancel</button>
            </>
          )}
        </div>
      </div>

      {/* Server error — rendered OUTSIDE the scrollable body so it's always visible */}
      {serverError && (
        <div className="flex items-center gap-2 px-4 py-2.5 text-xs"
          style={{ background: 'rgba(245,158,11,0.09)', borderBottom: '1px solid rgba(245,158,11,0.25)', color: 'var(--amber)', flexShrink: 0 }}>
          <AlertTriangle size={12} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{serverError}</span>
          <button onClick={() => setServerError(null)} style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', padding: 0 }}>
            <X size={12} />
          </button>
        </div>
      )}

      <div className="detail-body" style={{ maxWidth: 'none' }}>
        {/* Tabs (edit only) */}
        {isEdit && (
          <div style={{ display: 'flex', borderBottom: '1px solid var(--border-dim)', marginBottom: 20 }}>
            {(['config', 'history'] as const).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  display:        'flex',
                  alignItems:     'center',
                  gap:            6,
                  padding:        '10px 16px',
                  fontSize:       12,
                  fontWeight:     500,
                  color:          activeTab === tab ? 'var(--eva-primary)' : 'var(--text-muted)',
                  background:     'none',
                  border:         'none',
                  borderBottom:   activeTab === tab ? '2px solid var(--eva-primary)' : '2px solid transparent',
                  marginBottom:   -1,
                  cursor:         'pointer',
                  transition:     'color 0.15s',
                  whiteSpace:     'nowrap',
                }}
              >
                {tab === 'config' ? <Zap size={11} /> : <History size={11} />}
                {tab === 'config' ? 'Config' : 'Run History'}
              </button>
            ))}
          </div>
        )}

        {/* Run history tab */}
        {isEdit && activeTab === 'history' && daemonName && (
          <DaemonRunHistoryTab daemonName={daemonName} lifecycleStatus={ls} />
        )}

        {/* Config tab (or create form) */}
        {(activeTab === 'config' || !isEdit) && (
          <>

            {/* Lifecycle status section (edit only) */}
            {isEdit && ls && (
              <div className="form-section">
                <h3>Lifecycle Status</h3>

                {/* Action buttons */}
                <div className="flex flex-wrap gap-2">
                  {ls === 'paused' && (
                    <button
                      onClick={() => resumeDaemon()}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium"
                      style={{ background: 'rgba(0,212,160,0.1)', border: '1px solid rgba(0,212,160,0.25)', color: 'var(--success)' }}
                    ><Pause size={10} /> Resume</button>
                  )}
                  {(ls === 'idle' || ls === 'retrying') && existing?.run_interval_seconds && (
                    <button
                      onClick={() => pauseDaemon()}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium"
                      style={{ background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.25)', color: 'var(--amber)' }}
                    ><Pause size={10} /> Pause</button>
                  )}
                  {ls === 'permanently_failed' && (
                    <button
                      onClick={() => resetFailures()}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium"
                      style={{ background: 'rgba(255,77,109,0.1)', border: '1px solid rgba(255,77,109,0.25)', color: 'var(--danger)' }}
                    ><RotateCcw size={10} /> Reset Failures</button>
                  )}
                </div>

                {/* Permanently failed info */}
                {ls === 'permanently_failed' && (
                  <div className="flex flex-col gap-1 px-3 py-2 rounded-lg text-xs"
                    style={{ background: 'rgba(255,77,109,0.07)', border: '1px solid rgba(255,77,109,0.2)' }}>
                    <div className="flex items-center gap-1.5" style={{ color: 'var(--danger)' }}>
                      <AlertTriangle size={10} />
                      <span className="font-semibold">Permanently failed after {existing?.failure_count ?? 0} attempts</span>
                    </div>
                    {existing?.last_error && (
                      <p style={{ color: 'var(--text-secondary)', fontFamily: 'JetBrains Mono, monospace' }}>{existing.last_error}</p>
                    )}
                    {existing?.last_failure_at && (
                      <p style={{ color: 'var(--text-muted)' }}>Failed at: {formatDate(existing.last_failure_at)}</p>
                    )}
                  </div>
                )}

                {/* Retrying info */}
                {ls === 'retrying' && (
                  <div className="flex flex-col gap-1 px-3 py-2 rounded-lg text-xs"
                    style={{ background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.2)' }}>
                    <div className="flex items-center gap-1.5" style={{ color: 'var(--amber)' }}>
                      <RefreshCw size={10} />
                      <span className="font-semibold">Retrying ({existing?.failure_count ?? 0} of {existing?.max_retries ?? 3} attempts)</span>
                    </div>
                    <p style={{ color: 'var(--text-muted)' }}>Next retry: {formatRelativeTime(existing?.next_run_at)}</p>
                  </div>
                )}

                {/* Running info */}
                {ls === 'running' && existing?.current_execution_id && (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs"
                    style={{ background: 'rgba(0,201,255,0.07)', border: '1px solid rgba(0,201,255,0.2)' }}>
                    <Loader2 size={10} className="animate-spin" style={{ color: 'var(--eva-primary)' }} />
                    <span style={{ color: 'var(--text-secondary)' }}>Currently running —</span>
                    <span className="font-mono" style={{ color: 'var(--eva-primary)' }}>{existing.current_execution_id}</span>
                  </div>
                )}

                {/* Timing */}
                <div className="flex flex-col gap-1">
                  <div className="flex items-center justify-between text-xs">
                    <span style={{ color: 'var(--text-muted)' }}>Last run</span>
                    <span style={{ color: 'var(--text-secondary)' }}>{existing?.last_run_at ? formatDate(existing.last_run_at) : 'Never'}</span>
                  </div>
                  {existing?.run_interval_seconds && (
                    <div className="flex items-center justify-between text-xs">
                      <span style={{ color: 'var(--text-muted)' }}>Next run</span>
                      <span style={{ color: 'var(--eva-primary)' }}>{formatRelativeTime(existing?.next_run_at)}</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Name */}
            <div className="form-section">
              <h3>Identity</h3>
              <div className="form-row">
                <label className="form-label">Name *</label>
                <input
                  type="text"
                  className="form-input"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  disabled={isEdit}
                  placeholder="daily-reporter"
                  style={{
                    fontFamily: 'JetBrains Mono, monospace',
                    background: isEdit ? 'var(--bg-surface3)' : undefined,
                    color:      isEdit ? 'var(--text-muted)' : undefined,
                    borderColor: fieldErrors.name ? 'rgba(255,77,109,0.4)' : undefined,
                  }}
                />
                {isEdit && <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 2 }}>Name cannot be changed after creation.</p>}
                {fieldErrors.name && <p style={{ fontSize: 11.5, color: 'var(--danger)', marginTop: 2 }}>{fieldErrors.name}</p>}
              </div>
              <div className="form-row">
                <label className="form-label">Kind</label>
                {existing?.kind === 'system' ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{
                      fontSize: 11, fontWeight: 600, padding: '3px 9px', borderRadius: 999,
                      background: 'rgba(255,255,255,0.06)', color: 'var(--text-muted)', border: '1px solid var(--border-dim)',
                    }}>
                      system (reserved)
                    </span>
                    <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>Auto-created and managed by EVA — kind cannot be changed.</span>
                  </div>
                ) : (
                  <div style={{ display: 'flex', gap: 0, background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 8, padding: 2, width: 'fit-content' }}>
                    {(['standard', 'supervisor'] as const).map(k => (
                      <button
                        key={k}
                        onClick={() => setKind(k)}
                        className="py-1.5 px-3 rounded-md text-xs font-medium transition-all duration-150"
                        style={{
                          background: kind === k ? 'var(--eva-primary-dim)' : 'transparent',
                          border:     kind === k ? '1px solid var(--border-mid)' : '1px solid transparent',
                          color:      kind === k ? 'var(--eva-primary)' : 'var(--text-muted)',
                        }}
                      >
                        {k === 'standard' ? 'Standard' : 'Supervisor (manages background processes)'}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="form-row">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span className="form-label" style={{ marginBottom: 0 }}>Description</span>
                  {!editingDesc ? (
                    <button className="icon-btn" style={{ width: 22, height: 22, padding: 0 }} title="Edit description"
                      onClick={() => { setDescSnapshot(description); setEditingDesc(true) }}>
                      <Edit3 size={11} />
                    </button>
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <button className="btn ghost" style={{ fontSize: 11, padding: '3px 9px' }}
                        onClick={() => { setDesc(descSnapshot); setEditingDesc(false) }}>Cancel</button>
                      <button className="btn primary" style={{ fontSize: 11, padding: '3px 9px' }}
                        onClick={() => setEditingDesc(false)}>
                        <Check size={10} /> Save
                      </button>
                    </div>
                  )}
                </div>
                <textarea
                  className="form-input"
                  rows={2}
                  value={description}
                  onChange={e => setDesc(e.target.value)}
                  readOnly={!editingDesc}
                  placeholder="Brief description of what this daemon does…"
                  style={{
                    resize: editingDesc ? 'vertical' : 'none',
                    fontFamily: 'inherit',
                    borderColor: fieldErrors.description ? 'rgba(255,77,109,0.4)' : undefined,
                    opacity: editingDesc ? 1 : 0.85,
                    cursor: editingDesc ? 'text' : 'default',
                  }}
                />
              </div>
              <div className="form-row" style={{ marginBottom: 0 }}>
                <label className="form-label">Project (optional)</label>
                <select
                  className="form-input"
                  value={projectId}
                  onChange={e => setProjectId(e.target.value)}
                  style={{ color: projectId ? 'var(--text-primary)' : 'var(--text-muted)' }}
                >
                  <option value="">— no project —</option>
                  {(projects as Array<{ id: string; name: string }>).map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* System Prompt */}
            <div className="form-section">
              <h3>Behaviour</h3>
              <div className="form-row">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span className="form-label" style={{ marginBottom: 0 }}>System Prompt *</span>
                  {!editingDaemonPrompt ? (
                    <button className="icon-btn" style={{ width: 22, height: 22, padding: 0 }} title="Edit system prompt"
                      onClick={() => { setDaemonPromptSnapshot(systemPrompt); setEditingDaemonPrompt(true) }}>
                      <Edit3 size={11} />
                    </button>
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <button className="btn ghost" style={{ fontSize: 11, padding: '3px 9px' }}
                        onClick={() => { setSysPrompt(daemonPromptSnapshot); setEditingDaemonPrompt(false) }}>Cancel</button>
                      <button className="btn primary" style={{ fontSize: 11, padding: '3px 9px' }}
                        onClick={() => setEditingDaemonPrompt(false)}>
                        <Check size={10} /> Save
                      </button>
                    </div>
                  )}
                </div>
                <SystemPromptField
                  value={systemPrompt}
                  onChange={setSysPrompt}
                  context="agent"
                  rows={14}
                  showEnhance
                  readOnly={!editingDaemonPrompt}
                  placeholder="You are a background monitoring agent. When activated, check…"
                />
                {fieldErrors.system_prompt && <p style={{ fontSize: 11.5, color: 'var(--danger)', marginTop: 2 }}>{fieldErrors.system_prompt}</p>}
              </div>
              <div className="form-row" style={{ marginBottom: 0 }}>
                <label className="form-label">Trigger Message</label>
                <input
                  type="text"
                  className="form-input"
                  value={triggerMessage}
                  onChange={e => setTriggerMsg(e.target.value)}
                  placeholder="You have been activated. Begin your task."
                  style={{ borderColor: fieldErrors.trigger_message ? 'rgba(255,77,109,0.4)' : undefined }}
                />
                <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 2 }}>Message sent at the start of each run. Leave blank for default.</p>
              </div>
            </div>

            {/* Schedule */}
            <div className="form-section">
              <h3>Schedule</h3>
              <div className="form-row" style={{ marginBottom: 0 }}>
                <label className="form-label">Run mode</label>
                <div style={{ display: 'flex', gap: 0, background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 8, padding: 2 }}>
                  {(['manual', 'scheduled'] as const).map(mode => (
                    <button
                      key={mode}
                      onClick={() => setIntervalMode(mode)}
                      className="flex-1 py-1.5 rounded-md text-xs font-medium transition-all duration-150 capitalize"
                      style={{
                        background: intervalMode === mode ? 'var(--eva-primary-dim)' : 'transparent',
                        border:     intervalMode === mode ? '1px solid var(--border-mid)' : '1px solid transparent',
                        color:      intervalMode === mode ? 'var(--eva-primary)' : 'var(--text-muted)',
                      }}
                    >
                      {mode === 'manual' ? 'Manual only' : 'Scheduled'}
                    </button>
                  ))}
                </div>

                {intervalMode === 'scheduled' && (
                  <div className="flex flex-col gap-2 mt-2">
                    <div className="flex flex-wrap gap-1.5">
                      {PRESETS.map(p => (
                        <button
                          key={p.secs}
                          onClick={() => setIntervalSecs(p.secs)}
                          className="text-xs px-2.5 py-1 rounded-lg transition-colors"
                          style={{
                            background: intervalSeconds === p.secs ? 'var(--eva-primary-dim)' : 'var(--bg-surface2)',
                            border:     `1px solid ${intervalSeconds === p.secs ? 'var(--border-mid)' : 'var(--border-dim)'}`,
                            color:      intervalSeconds === p.secs ? 'var(--eva-primary)' : 'var(--text-muted)',
                          }}
                        >{p.label}</button>
                      ))}
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min={60}
                        value={intervalSeconds}
                        onChange={e => setIntervalSecs(Math.max(60, Number(e.target.value)))}
                        className="form-input"
                        style={{ width: 100, fontFamily: 'JetBrains Mono, monospace', borderColor: fieldErrors.run_interval_seconds ? 'rgba(255,77,109,0.4)' : undefined }}
                      />
                      <span className="text-xs" style={{ color: 'var(--text-muted)' }}>seconds (min 60) — currently: {formatInterval(intervalSeconds)}</span>
                    </div>
                    {fieldErrors.run_interval_seconds && <p style={{ fontSize: 11.5, color: 'var(--danger)' }}>{fieldErrors.run_interval_seconds}</p>}
                  </div>
                )}
              </div>
            </div>

            {/* Provider + Model */}
            <div className="form-section">
              <h3>Model</h3>
              <div className="form-row">
                <label className="form-label">Provider</label>
                <SearchableSelect
                  value={provider}
                  onChange={v => { setProvider(v); setModel(''); setFieldErrors(f => ({ ...f, provider: '', model: '' })); setProviderReason(null) }}
                  options={[
                    { value: '', label: '— inherit from session —' },
                    ...chatReadyProviderOptions(providers as ProviderListItem[], provider),
                  ]}
                  placeholder={provsLoading ? '— loading…' : '— inherit from session —'}
                  loading={provsLoading}
                />
                {fieldErrors.provider && <ProviderFieldError message={fieldErrors.provider} reason={providerReason} />}
              </div>
              <div className="form-row">
                <label className="form-label">Model</label>
                {provider ? (
                  <SearchableSelect
                    value={model}
                    onChange={v => { setModel(v); setFieldErrors(f => ({ ...f, model: '' })) }}
                    options={modelOptions(liveModels, model, !modelsLoading)}
                    placeholder={modelsLoading ? '— loading models…' : '— select model —'}
                    loading={modelsLoading}
                  />
                ) : (
                  <SearchableSelect value="" onChange={() => {}} options={[{ value: '', label: '— inherit from session —' }]} placeholder="— inherit from session —" disabled />
                )}
                {fieldErrors.model && <ProviderFieldError message={fieldErrors.model} />}
              </div>
              <div className="form-row" style={{ marginBottom: 0 }}>
                <label className="form-label">Max Steps: {maxSteps}</label>
                <input
                  type="range" min={1} max={50} value={maxSteps}
                  onChange={e => setMaxSteps(Number(e.target.value))}
                  className="w-full accent-[var(--eva-primary)]"
                />
                <div className="flex justify-between text-xs" style={{ color: 'var(--text-muted)' }}>
                  <span>1</span><span>50</span>
                </div>
                {fieldErrors.max_steps && <p style={{ fontSize: 11.5, color: 'var(--danger)' }}>{fieldErrors.max_steps}</p>}
              </div>
            </div>

            {/* Event Subscriptions */}
            <div className="form-section">
              <button
                className="flex items-center gap-1.5 w-full text-left"
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-muted)' }}
                onClick={() => setEventsOpen(o => !o)}
              >
                {eventsOpen ? <ChevronDown size={10} style={{ color: 'var(--text-muted)' }} /> : <ChevronRight size={10} style={{ color: 'var(--text-muted)' }} />}
                Event Triggers {selectedEvents.length > 0 && <span style={{ fontSize: 10, color: 'var(--eva-primary)', background: 'rgba(0,201,255,0.1)', padding: '0 5px', borderRadius: 99 }}>{selectedEvents.length}</span>}
              </button>
              {eventsOpen && (
                <div className="rounded-lg p-2" style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}>
                  {AVAILABLE_EVENTS.map(ev => (
                    <label
                      key={ev.key}
                      className="flex items-start gap-2 px-2 py-1.5 rounded-md cursor-pointer text-xs"
                      style={{ background: selectedEvents.includes(ev.key) ? 'rgba(124,108,243,0.07)' : 'transparent' }}
                    >
                      <input
                        type="checkbox"
                        checked={selectedEvents.includes(ev.key)}
                        onChange={() => toggleEvent(ev.key)}
                        className="mt-0.5 accent-[var(--neural)]"
                      />
                      <span style={{ color: 'var(--text-primary)', lineHeight: '1.4' }}>{ev.label}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            {/* Tool Whitelist */}
            <div className="form-section">
              <button
                className="flex items-center gap-1.5 w-full text-left"
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-muted)' }}
                onClick={() => setToolsOpen(o => !o)}
              >
                {toolsOpen ? <ChevronDown size={10} style={{ color: 'var(--text-muted)' }} /> : <ChevronRight size={10} style={{ color: 'var(--text-muted)' }} />}
                Tool Whitelist {selectedTools.length > 0 && <span style={{ fontSize: 10, color: 'var(--eva-primary)', background: 'rgba(0,201,255,0.1)', padding: '0 5px', borderRadius: 99 }}>{selectedTools.length} selected</span>}
              </button>
              {!toolsOpen && (
                <p style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                  {selectedTools.length === 0 ? 'All tools available (no restriction)' : selectedTools.join(', ')}
                </p>
              )}
              {toolsOpen && (
                <div className="flex flex-col gap-0.5 max-h-48 overflow-y-auto rounded-lg p-2"
                  style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}>
                  {(allTools as Tool[]).length === 0 && (
                    <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No tools available</p>
                  )}
                  {(allTools as Tool[]).map(t => (
                    <label
                      key={t.name}
                      className="flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer"
                      style={{ background: selectedTools.includes(t.name) ? 'rgba(0,201,255,0.06)' : 'transparent' }}
                    >
                      <input type="checkbox" checked={selectedTools.includes(t.name)} onChange={() => toggleTool(t.name)} className="accent-[var(--eva-primary)]" />
                      <span className="text-xs font-mono flex-1 truncate" style={{ color: 'var(--text-primary)' }}>{t.name}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            {/* Retry Settings */}
            <div className="form-section">
              <button
                className="flex items-center gap-1.5 w-full text-left"
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-muted)' }}
                onClick={() => setRetryOpen(o => !o)}
              >
                {retryOpen ? <ChevronDown size={10} style={{ color: 'var(--text-muted)' }} /> : <ChevronRight size={10} style={{ color: 'var(--text-muted)' }} />}
                Retry Settings
              </button>
              {retryOpen && (
                <div className="flex flex-col gap-3">
                  <div className="flex items-center justify-between px-3 py-2 rounded-lg"
                    style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}>
                    <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>Retry on failure</span>
                    <DaemonToggle value={retryOnFailure} onChange={setRetryOnFailure} />
                  </div>
                  {retryOnFailure && (
                    <>
                      <div className="form-row">
                        <label className="form-label">Max Retries: {maxRetries}</label>
                        <input type="range" min={1} max={10} value={maxRetries} onChange={e => setMaxRetries(Number(e.target.value))} className="w-full accent-[var(--eva-primary)]" />
                        <div className="flex justify-between text-xs" style={{ color: 'var(--text-muted)' }}><span>1</span><span>10</span></div>
                      </div>
                      <div className="form-row" style={{ marginBottom: 0 }}>
                        <label className="form-label">Base Retry Delay (seconds): doubles each attempt</label>
                        <input
                          type="number" min={60} value={retryDelaySecs}
                          onChange={e => setRetryDelaySecs(Math.max(60, Number(e.target.value)))}
                          className="form-input" style={{ width: 110, fontFamily: 'JetBrains Mono, monospace', borderColor: fieldErrors.retry_delay_seconds ? 'rgba(255,77,109,0.4)' : undefined }}
                        />
                        {fieldErrors.retry_delay_seconds && <p style={{ fontSize: 11.5, color: 'var(--danger)' }}>{fieldErrors.retry_delay_seconds}</p>}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>

            {/* Enabled toggle */}
            <div className="form-section">
              <div className="flex items-center justify-between px-4 py-3 rounded-lg"
                style={{
                  background: projectIsPaused ? 'rgba(245,158,11,0.04)' : 'var(--bg-surface2)',
                  border: `1px solid ${projectIsPaused ? 'rgba(245,158,11,0.25)' : 'var(--border-dim)'}`,
                }}>
                <div>
                  <div className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>Enabled</div>
                  <div className="text-xs mt-0.5" style={{ color: projectIsPaused ? 'var(--amber)' : 'var(--text-muted)' }}>
                    {projectIsPaused
                      ? `Project is ${linkedProject?.status} — resume the project to enable or disable this daemon.`
                      : enabled ? 'Daemon will run on schedule / when triggered' : 'Daemon is paused — will not run automatically'}
                  </div>
                </div>
                <div style={{ opacity: projectIsPaused ? 0.4 : 1, pointerEvents: projectIsPaused ? 'none' : 'auto' }}>
                  <DaemonToggle value={enabled} onChange={setEnabled} />
                </div>
              </div>
            </div>

            {/* Webhook section (edit only) */}
            {isEdit && daemonName && (
              <div className="form-section">
                <button
                  className="flex items-center gap-1.5 w-full text-left"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-muted)' }}
                  onClick={() => setWebhookOpen(o => !o)}
                >
                  {webhookOpen ? <ChevronDown size={10} style={{ color: 'var(--text-muted)' }} /> : <ChevronRight size={10} style={{ color: 'var(--text-muted)' }} />}
                  Webhook
                </button>
                {webhookOpen && <DaemonWebhookSection daemonName={daemonName} expanded={webhookOpen} />}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
