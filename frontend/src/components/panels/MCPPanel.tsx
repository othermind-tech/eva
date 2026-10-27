// MCPPanel — full-width MCP server management view
import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plug2, Plus, RefreshCw, X, Trash2, Edit3, CheckCircle2,
  Loader2, ChevronRight, Terminal,
  Globe, Activity, ToggleLeft, ToggleRight, FileText,
  BookOpen, Cpu, FolderOpen, Wrench,
} from 'lucide-react'
import { mcpApi } from '@/api/mcp'
import { projectsApi } from '@/api/projects'
import type { Project } from '@/api/projects'
import type { MCPConfig, MCPHealth, MCPResource, MCPPrompt, MCPTool, SeedDefaultMCPsResponse } from '@/types/api'
import { useChatStore } from '@/store/chatStore'

// ─── Form shape ──────────────────────────────────────────────────────────────
type Transport = 'stdio' | 'sse' | 'http'

interface FormState {
  name:       string
  transport:  Transport
  command:    string
  args:       string   // comma-separated string; split on save
  url:        string
  enabled:    boolean
  project_id: string   // empty string = null (global)
}

const BLANK: FormState = {
  name: '', transport: 'stdio', command: '', args: '', url: '', enabled: true, project_id: '',
}

// ─── Transport badge ─────────────────────────────────────────────────────────
function TransportBadge({ t }: { t: Transport }) {
  const cfg = {
    stdio: { icon: Terminal, label: 'STDIO', color: 'var(--neural)',   bg: 'rgba(124,108,243,0.12)', border: 'rgba(124,108,243,0.28)' },
    sse:   { icon: Activity, label: 'SSE',   color: 'var(--amber)',    bg: 'rgba(245,158,11,0.10)', border: 'rgba(245,158,11,0.28)' },
    http:  { icon: Globe,    label: 'HTTP',  color: 'var(--eva-primary)', bg: 'rgba(0,201,255,0.08)', border: 'rgba(0,201,255,0.25)' },
  }[t]
  const Icon = cfg.icon
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 3,
        fontSize: 9, fontWeight: 600, padding: '2px 6px', borderRadius: 999,
        color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.border}`,
        flexShrink: 0, whiteSpace: 'nowrap',
      }}
    >
      <Icon size={9} />
      {cfg.label}
    </span>
  )
}

// ─── Health dot ──────────────────────────────────────────────────────────────
function HealthDot({ health }: { health?: MCPHealth }) {
  if (!health) return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11.5, color: 'var(--text-dim)' }}>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: 'var(--text-dim)', display: 'inline-block', flexShrink: 0 }} />
      Unknown
    </span>
  )
  const { status, latency_ms, error } = health
  const color = status === 'healthy'        ? 'var(--success)'
              : status === 'unhealthy'       ? 'var(--danger)'
              : status === 'project_paused'  ? 'var(--amber)'
              : 'var(--text-dim)'
  const glow  = status === 'healthy'   ? '0 0 5px rgba(0,212,160,0.5)'
              : status === 'unhealthy' ? '0 0 5px rgba(255,77,109,0.5)'
              : 'none'
  const label = status === 'healthy'
    ? `${latency_ms != null ? `${latency_ms} ms` : 'Healthy'}${health.tool_count != null ? ` · ${health.tool_count} tools` : ''}`
    : status === 'unhealthy'      ? 'Error'
    : status === 'disabled'       ? 'Disabled'
    : status === 'project_paused' ? 'Project paused'
    : 'Unknown'
  const tip = status === 'project_paused'
    ? 'This MCP is paused because its project is paused. Resume the project to re-enable.'
    : (error ?? undefined)
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11.5, color }} title={tip}>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: color, boxShadow: glow, display: 'inline-block', flexShrink: 0 }} />
      {label}
    </span>
  )
}

// ─── Resources sub-panel ─────────────────────────────────────────────────────
function ServerResources({ configId }: { configId: string }) {
  const { data: resources = [], isLoading } = useQuery<MCPResource[]>({
    queryKey: ['mcp-resources', configId],
    queryFn:  () => mcpApi.getResources(configId),
  })
  if (isLoading) return (
    <div className="flex items-center gap-2 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={12} className="animate-spin" /> Loading resources…
    </div>
  )
  if (resources.length === 0) return (
    <p className="text-xs py-2" style={{ color: 'var(--text-dim)' }}>No resources exposed by this server.</p>
  )
  return (
    <div className="flex flex-col gap-1.5">
      {resources.map(r => (
        <div
          key={r.uri}
          className="flex items-start gap-2 px-3 py-2 rounded-lg"
          style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}
        >
          <FileText size={13} style={{ color: 'var(--text-muted)', marginTop: 1 }} />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>{r.name ?? r.uri}</p>
            <p className="text-xs truncate" style={{ color: 'var(--text-muted)', fontFamily: 'JetBrains Mono, monospace' }}>{r.uri}</p>
            {r.description && <p className="text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>{r.description}</p>}
          </div>
          {r.mime_type && (
            <span className="text-xs shrink-0 px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-surface3)', color: 'var(--text-dim)' }}>
              {r.mime_type}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

// ─── Prompts sub-panel ───────────────────────────────────────────────────────
function ServerPrompts({ configId }: { configId: string }) {
  const { data: prompts = [], isLoading } = useQuery<MCPPrompt[]>({
    queryKey: ['mcp-prompts', configId],
    queryFn:  () => mcpApi.getPrompts(configId),
  })
  if (isLoading) return (
    <div className="flex items-center gap-2 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={12} className="animate-spin" /> Loading prompts…
    </div>
  )
  if (prompts.length === 0) return (
    <p className="text-xs py-2" style={{ color: 'var(--text-dim)' }}>No prompts exposed by this server.</p>
  )
  return (
    <div className="flex flex-col gap-1.5">
      {prompts.map(p => (
        <div
          key={p.name}
          className="flex items-start gap-2 px-3 py-2 rounded-lg"
          style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}
        >
          <BookOpen size={13} style={{ color: 'var(--text-muted)', marginTop: 1 }} />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium" style={{ color: 'var(--text-primary)', fontFamily: 'JetBrains Mono, monospace' }}>{p.name}</p>
            {p.description && <p className="text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>{p.description}</p>}
          </div>
        </div>
      ))}
    </div>
  )
}

// ─── Tools sub-panel ─────────────────────────────────────────────────────────
function ServerTools({ configId }: { configId: string }) {
  const { data: tools = [], isLoading } = useQuery<MCPTool[]>({
    queryKey: ['mcp-tools', configId],
    queryFn:  () => mcpApi.getTools(configId),
  })
  if (isLoading) return (
    <div className="flex items-center gap-2 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={12} className="animate-spin" /> Loading tools…
    </div>
  )
  if (tools.length === 0) return (
    <p className="text-xs py-2" style={{ color: 'var(--text-dim)' }}>No tools exposed by this server. Try syncing first.</p>
  )
  return (
    <div className="flex flex-col gap-1.5" style={{ maxHeight: 168, overflowY: 'auto' }}>
      {tools.map(t => (
        <div
          key={t.name}
          className="flex items-start gap-2 px-3 py-2 rounded-lg"
          style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}
        >
          <Wrench size={13} style={{ color: 'var(--eva-primary)', marginTop: 1, flexShrink: 0 }} />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium" style={{ color: 'var(--text-primary)', fontFamily: 'JetBrains Mono, monospace' }}>{t.name}</p>
            {t.description && <p className="text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>{t.description}</p>}
          </div>
        </div>
      ))}
    </div>
  )
}

// ─── Expanded server detail ───────────────────────────────────────────────────
type DetailTab = 'tools' | 'resources' | 'prompts'

export function ServerDetail({ server }: { server: MCPConfig }) {
  const [tab, setTab] = useState<DetailTab>('tools')

  return (
    <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--border-dim)' }}>
      {/* Config details row */}
      <div className="flex flex-wrap gap-4 mb-3 text-xs" style={{ color: 'var(--text-muted)' }}>
        {server.command && (
          <span className="flex items-center gap-1">
            <Terminal size={11} style={{ color: 'var(--text-dim)' }} />
            <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-secondary)' }}>{server.command}</span>
          </span>
        )}
        {server.args && server.args.length > 0 && (
          <span className="flex items-center gap-1">
            Args:
            <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-secondary)' }}>
              {server.args.join(' ')}
            </span>
          </span>
        )}
        {server.url && (
          <span className="flex items-center gap-1">
            <Globe size={11} style={{ color: 'var(--text-dim)' }} />
            <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-secondary)' }}>{server.url}</span>
          </span>
        )}
        <span style={{ color: 'var(--text-dim)' }}>
          Added {new Date(server.created_at).toLocaleDateString()}
        </span>
      </div>

      {/* Tabs */}
      <div className="flex gap-0.5 mb-3 p-0.5 rounded-lg w-fit" style={{ background: 'var(--bg-surface2)' }}>
        {([['tools', 'Tools', Wrench], ['resources', 'Resources', FileText], ['prompts', 'Prompts', BookOpen]] as const).map(([id, label, Icon]) => (
          <button
            key={id}
            onClick={() => setTab(id as DetailTab)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors"
            style={tab === id
              ? { background: 'var(--bg-surface3)', color: 'var(--text-primary)', border: '1px solid var(--border-mid)' }
              : { color: 'var(--text-muted)', border: '1px solid transparent' }
            }
          >
            <Icon size={11} />
            {label}
          </button>
        ))}
      </div>

      {tab === 'tools'     && <ServerTools     configId={server.id} />}
      {tab === 'resources' && <ServerResources configId={server.id} />}
      {tab === 'prompts'   && <ServerPrompts   configId={server.id} />}
    </div>
  )
}

// ─── Server row ───────────────────────────────────────────────────────────────
interface ServerRowProps {
  server:           MCPConfig
  health?:          MCPHealth
  expanded:         boolean
  onToggleExpand:   () => void
  onEdit:           () => void
  onDelete:         () => void
  onToggleEnabled:  (v: boolean) => void
  onSync:           () => void
  deleting:         boolean
  toggling:         boolean          // true while this row's enable/disable is in-flight
  syncing:          boolean          // true while this row's sync is in-flight
  projectName?:     string | null   // resolved from project_id (V5.7)
  syncError?:       string | null   // Section 91 — from the last enable/update call, not the health poll
}

function ServerRow({
  server, health, expanded, onToggleExpand,
  onEdit, onDelete, onToggleEnabled, onSync, deleting, toggling, syncing, projectName, syncError,
}: ServerRowProps) {
  const isPaused = server.project_paused

  return (
    <div
      className="kn-card"
      style={{
        opacity: server.enabled && !isPaused ? 1 : 0.65,
        borderColor: isPaused ? 'rgba(245,158,11,0.2)' : undefined,
        background: isPaused ? 'rgba(245,158,11,0.03)' : undefined,
      }}
    >
      {/* Main row */}
      <div className="kn-head">

        {/* Icon */}
        <div
          className="row-icon"
          style={{
            width: 32, height: 32, borderRadius: 9, flexShrink: 0,
            background: isPaused ? 'rgba(245,158,11,0.1)' : 'rgba(124,108,243,0.12)',
            color: isPaused ? 'var(--amber)' : 'var(--neural)',
          }}
        >
          <Cpu size={14} />
        </div>

        {/* Name + badges + health */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 2 }}>
            <span style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text-primary)' }}>
              {server.name}
            </span>
            <TransportBadge t={server.transport} />
            {isPaused && (
              <span
                title="This MCP is paused because its project is paused. Resume the project to re-enable."
                style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 4, background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)', color: 'var(--amber)' }}
              >
                ⊘ PROJECT PAUSED
              </span>
            )}
            {!isPaused && server.project_id && (
              <span
                title={`Scoped to project: ${projectName ?? server.project_id}`}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 4, background: 'rgba(0,212,160,0.1)', border: '1px solid rgba(0,212,160,0.25)', color: 'var(--success)' }}
              >
                <FolderOpen size={9} />
                {projectName ?? 'PROJECT'}
              </span>
            )}
          </div>
          <HealthDot health={health} />
          {server.name === 'playwright' && (
            <p style={{ margin: '3px 0 0', fontSize: 10.5, color: 'var(--text-dim)' }}>
              Browser automation — lets the agent open and test the app it builds.
            </p>
          )}
          {syncError && (
            <p style={{ margin: '3px 0 0', fontSize: 10.5, color: 'var(--danger)' }}>
              {syncError} — use Sync to retry.
            </p>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center" style={{ gap: 1 }} onClick={e => e.stopPropagation()}>

          {/* Sync — force reconnect + re-discover tools; disabled when server is off or project paused */}
          <button
            className="icon-btn"
            style={{
              width: 26, height: 26, padding: 0,
              color: syncing ? 'var(--eva-primary)' : 'var(--text-muted)',
              opacity: (!server.enabled || isPaused) ? 0.35 : 1,
              cursor: (!server.enabled || isPaused || syncing) ? 'default' : 'pointer',
            }}
            title={isPaused ? 'Cannot sync — project paused' : !server.enabled ? 'Cannot sync — server disabled' : 'Sync — reconnect and re-discover tools'}
            disabled={!server.enabled || isPaused || syncing}
            onClick={onSync}
          >
            <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} />
          </button>

          {/* Enable toggle — paused / loading / normal */}
          {isPaused ? (
            <span
              title="This MCP is paused because its project is paused. Resume the project to re-enable."
              style={{ width: 26, height: 26, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--amber)', opacity: 0.5, cursor: 'not-allowed' }}
            >
              <ToggleLeft size={18} />
            </span>
          ) : toggling ? (
            <span
              title={server.enabled ? 'Disabling…' : 'Connecting…'}
              style={{ width: 26, height: 26, display: 'flex', alignItems: 'center', justifyContent: 'center', color: server.enabled ? 'var(--text-dim)' : 'var(--success)' }}
            >
              <Loader2 size={13} className="animate-spin" />
            </span>
          ) : (
            <button
              className="icon-btn"
              style={{ width: 26, height: 26, padding: 0, color: server.enabled ? 'var(--success)' : 'var(--text-muted)' }}
              title={server.enabled ? 'Disable server' : 'Enable server'}
              onClick={() => onToggleEnabled(!server.enabled)}
            >
              {server.enabled ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
            </button>
          )}

          {/* Edit */}
          <button
            className="icon-btn"
            style={{ width: 26, height: 26, padding: 0 }}
            title="Edit"
            onClick={onEdit}
          >
            <Edit3 size={13} />
          </button>

          {/* Delete */}
          <button
            className="icon-btn"
            style={{ width: 26, height: 26, padding: 0 }}
            title="Delete"
            disabled={deleting}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
            onMouseLeave={e => (e.currentTarget.style.color = '')}
            onClick={onDelete}
          >
            {deleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
          </button>
        </div>

        {/* Expand chevron */}
        <button
          className="icon-btn"
          style={{ width: 26, height: 26, padding: 0, flexShrink: 0 }}
          title={expanded ? 'Collapse' : 'Browse resources & prompts'}
          onClick={onToggleExpand}
        >
          <ChevronRight size={14} style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }} />
        </button>
      </div>

      {/* Expanded detail */}
      {expanded && <ServerDetail server={server} />}
    </div>
  )
}

// ─── Add / Edit form ─────────────────────────────────────────────────────────
interface ServerFormProps {
  form:        FormState
  setForm:     (f: FormState) => void
  onSave:      () => void
  onCancel:    () => void
  isNew:       boolean
  isPending:   boolean
  canSave:     boolean
  projects:    Project[]   // V5.7 — non-archived projects for project dropdown
}

function ServerForm({ form, setForm, onSave, onCancel, isNew, isPending, canSave, projects }: ServerFormProps) {
  const f = (key: keyof FormState, val: string | boolean) =>
    setForm({ ...form, [key]: val })
  const nameHasDelimiter = form.name.includes('__')

  return (
    <div
      className="m-6 rounded-xl"
      style={{ background: 'var(--bg-surface1)', border: '1px solid var(--border-mid)' }}
    >
      {/* Form header */}
      <div
        className="flex items-center justify-between px-5 py-4"
        style={{ borderBottom: '1px solid var(--border-dim)' }}
      >
        <div className="flex items-center gap-2">
          <Plug2 size={15} style={{ color: 'var(--neural)' }} />
          <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
            {isNew ? 'Add MCP Server' : 'Edit MCP Server'}
          </span>
        </div>
        <button onClick={onCancel} style={{ color: 'var(--text-muted)' }}>
          <X size={15} />
        </button>
      </div>

      {/* Fields */}
      <div className="p-5 flex flex-col gap-4">

        {/* Name */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
            Server Name <span style={{ color: 'var(--danger)' }}>*</span>
          </label>
          <input
            className="w-full px-3 py-2 rounded-lg text-sm"
            style={{
              background: 'var(--bg-surface2)',
              border: `1px solid ${nameHasDelimiter ? 'rgba(255,77,109,0.4)' : 'var(--border-mid)'}`,
              color: 'var(--text-primary)',
              outline: 'none',
            }}
            placeholder="e.g. my-filesystem-server"
            value={form.name}
            onChange={e => f('name', e.target.value)}
          />
          {nameHasDelimiter && (
            <p style={{ fontSize: 11.5, color: 'var(--danger)', margin: 0 }}>
              "__" is reserved and can't be used in a server name
            </p>
          )}
        </div>

        {/* Transport */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Transport</label>
          <div className="flex gap-2">
            {(['stdio', 'sse', 'http'] as const).map(t => (
              <button
                key={t}
                onClick={() => f('transport', t)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
                style={form.transport === t
                  ? { background: 'rgba(124,108,243,0.18)', border: '1px solid rgba(124,108,243,0.4)', color: 'var(--neural)' }
                  : { background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-muted)' }
                }
              >
                {t === 'stdio' ? <Terminal size={11} /> : t === 'sse' ? <Activity size={11} /> : <Globe size={11} />}
                {t.toUpperCase()}
              </button>
            ))}
          </div>
          <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
            {form.transport === 'stdio'
              ? 'Launches a local process via standard I/O. Requires a command.'
              : form.transport === 'sse'
              ? 'Connects to a remote server via Server-Sent Events. Requires a URL.'
              : 'Connects to a remote server via HTTP streaming. Requires a URL.'}
          </p>
        </div>

        {/* Project scope — V5.7 */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
            Project <span className="font-normal" style={{ color: 'var(--text-dim)' }}>(optional — leave blank for global)</span>
          </label>
          <select
            value={form.project_id}
            onChange={e => f('project_id', e.target.value)}
            className="w-full px-3 py-2 rounded-lg text-sm"
            style={{
              background: 'var(--bg-surface2)',
              border: '1px solid var(--border-mid)',
              color: form.project_id ? 'var(--text-primary)' : 'var(--text-dim)',
              outline: 'none',
            }}
          >
            <option value="">(none — global)</option>
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
            Global MCPs are available to all conversations. Project MCPs are paused automatically when the project is paused or archived.
          </p>
        </div>

        {/* STDIO fields */}
        {form.transport === 'stdio' && (
          <>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
                Command <span style={{ color: 'var(--danger)' }}>*</span>
              </label>
              <input
                className="w-full px-3 py-2 rounded-lg text-sm"
                style={{
                  background: 'var(--bg-surface2)',
                  border: '1px solid var(--border-mid)',
                  color: 'var(--text-primary)',
                  fontFamily: 'JetBrains Mono, monospace',
                  outline: 'none',
                }}
                placeholder="e.g. npx or /usr/local/bin/mcp-server"
                value={form.command}
                onChange={e => f('command', e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
                Arguments <span className="font-normal" style={{ color: 'var(--text-dim)' }}>(comma-separated)</span>
              </label>
              <input
                className="w-full px-3 py-2 rounded-lg text-sm"
                style={{
                  background: 'var(--bg-surface2)',
                  border: '1px solid var(--border-mid)',
                  color: 'var(--text-primary)',
                  fontFamily: 'JetBrains Mono, monospace',
                  outline: 'none',
                }}
                placeholder="e.g. @modelcontextprotocol/server-filesystem, /home/user"
                value={form.args}
                onChange={e => f('args', e.target.value)}
              />
            </div>
          </>
        )}

        {/* SSE / HTTP URL */}
        {form.transport !== 'stdio' && (
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
              URL <span style={{ color: 'var(--danger)' }}>*</span>
            </label>
            <input
              className="w-full px-3 py-2 rounded-lg text-sm"
              style={{
                background: 'var(--bg-surface2)',
                border: '1px solid var(--border-mid)',
                color: 'var(--text-primary)',
                fontFamily: 'JetBrains Mono, monospace',
                outline: 'none',
              }}
              placeholder={form.transport === 'sse'
                ? 'e.g. http://localhost:3001/sse'
                : 'e.g. http://localhost:3001/mcp'}
              value={form.url}
              onChange={e => f('url', e.target.value)}
            />
          </div>
        )}

        {/* Enabled */}
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Enable on save</p>
            <p className="text-xs" style={{ color: 'var(--text-dim)' }}>Disabled servers are not connected or available to agents</p>
          </div>
          <button
            onClick={() => f('enabled', !form.enabled)}
            style={{ color: form.enabled ? 'var(--success)' : 'var(--text-dim)' }}
          >
            {form.enabled ? <ToggleRight size={26} /> : <ToggleLeft size={26} />}
          </button>
        </div>
      </div>

      {/* Footer */}
      <div
        className="flex items-center justify-end gap-2 px-5 py-4"
        style={{ borderTop: '1px solid var(--border-dim)' }}
      >
        <button
          onClick={onCancel}
          className="px-4 py-2 rounded-lg text-xs font-medium transition-colors"
          style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', color: 'var(--text-muted)' }}
        >
          Cancel
        </button>
        <button
          onClick={onSave}
          disabled={!canSave || isPending}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium transition-all"
          style={{
            background: canSave && !isPending ? 'rgba(124,108,243,0.2)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${canSave && !isPending ? 'rgba(124,108,243,0.4)' : 'var(--border-dim)'}`,
            color: canSave && !isPending ? 'var(--neural)' : 'var(--text-dim)',
            cursor: canSave && !isPending ? 'pointer' : 'not-allowed',
          }}
        >
          {isPending ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
          {isPending ? 'Saving…' : isNew ? 'Add Server' : 'Save Changes'}
        </button>
      </div>
    </div>
  )
}

// ─── Empty state ──────────────────────────────────────────────────────────────
function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 py-24">
      <div
        className="flex items-center justify-center rounded-2xl"
        style={{
          width: 64, height: 64,
          background: 'linear-gradient(135deg, rgba(124,108,243,0.14), rgba(0,201,255,0.08))',
          border: '1px solid rgba(124,108,243,0.22)',
        }}
      >
        <Plug2 size={28} style={{ color: 'var(--neural)', opacity: 0.7 }} />
      </div>
      <div className="text-center max-w-xs">
        <p className="text-sm font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>No MCP servers configured</p>
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
          Connect external tools, databases, and services via the Model Context Protocol. Servers expose resources and prompts that agents can use.
        </p>
      </div>
      <button
        onClick={onAdd}
        className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all"
        style={{
          background: 'rgba(124,108,243,0.16)',
          border: '1px solid rgba(124,108,243,0.32)',
          color: 'var(--neural)',
        }}
      >
        <Plus size={15} />
        Add your first server
      </button>
    </div>
  )
}

// ─── Main panel ───────────────────────────────────────────────────────────────
export default function MCPPanel() {
  const qc = useQueryClient()

  const [editingId, setEditingId] = useState<string | '__new__' | null>(null)
  const [form,      setForm]      = useState<FormState>(BLANK)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  // Section 91 — sync_error from the enable/update call, keyed by config id.
  // Distinct from the periodic health poll (['mcp-health']): this is the
  // immediate reason a just-enabled server has 0 tools, not a stale/eventual
  // connectivity check. Cleared when the row is re-synced via its own button.
  const [syncErrors, setSyncErrors] = useState<Record<string, string | null>>({})

  // Server list — global panel only shows servers not scoped to a project
  const { data: allServers = [], isLoading, refetch } = useQuery<MCPConfig[]>({
    queryKey: ['mcp'],
    queryFn:  () => mcpApi.list(),
  })
  const servers = allServers.filter(s => !s.project_id)

  // Projects list — for scope badge resolution + form dropdown (V5.7)
  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn:  projectsApi.list,
    staleTime: 60_000,
  })
  const nonArchivedProjects = projects.filter(p => p.status !== 'archived')
  const projectMap = Object.fromEntries(projects.map(p => [p.id, p.name]))

  // Health for all servers
  const { data: healthList = [], refetch: refetchHealth, isLoading: healthLoading } = useQuery<MCPHealth[]>({
    queryKey: ['mcp-health'],
    queryFn:  mcpApi.healthAll,
    refetchInterval: 30_000,
    retry: false,
  })
  const healthMap = Object.fromEntries(healthList.map(h => [h.config_id, h]))

  // Create
  const { mutate: createServer, isPending: creating } = useMutation({
    mutationFn: (body: Parameters<typeof mcpApi.create>[0]) => mcpApi.create(body),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['mcp'] }); closeForm() },
  })

  // Update
  const { mutate: updateServer, isPending: updating } = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<MCPConfig> }) => mcpApi.update(id, body),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['mcp'] }); closeForm() },
  })

  // Delete
  const {
    mutate: deleteServer, isPending: deleting, variables: deleteVars,
  } = useMutation({
    mutationFn: (id: string) => mcpApi.delete(id),
    onSuccess:  () => {
      qc.invalidateQueries({ queryKey: ['mcp'] })
      qc.invalidateQueries({ queryKey: ['mcp-health'] })
    },
  })

  // Toggle enabled — track variables so each row knows if it's the one loading
  const {
    mutate: toggleEnabled, isPending: toggling, variables: toggleVars,
  } = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => mcpApi.update(id, { enabled }),
    onSuccess:  (data, variables) => {
      qc.invalidateQueries({ queryKey: ['mcp'] })
      qc.invalidateQueries({ queryKey: ['mcp-health'] })
      setSyncErrors(prev => ({ ...prev, [variables.id]: data.sync_error ?? null }))
    },
    // A cold Playwright-style install can outlast even the 120s override above.
    // The row would otherwise keep showing its pre-toggle state forever —
    // refetch so it reflects whatever actually happened server-side.
    onError: () => {
      qc.invalidateQueries({ queryKey: ['mcp'] })
      qc.invalidateQueries({ queryKey: ['mcp-health'] })
    },
  })

  // Sync — force reconnect and re-discover tools/resources for a single server
  const {
    mutate: syncServer, isPending: syncing, variables: syncVars,
  } = useMutation({
    mutationFn: (id: string) => mcpApi.sync(id),
    onSuccess:  (_data, id) => {
      qc.invalidateQueries({ queryKey: ['mcp-health'] })
      refetchHealth()
      setSyncErrors(prev => ({ ...prev, [id]: null }))
    },
  })

  // Section 91 — (re)seed default MCP configs (e.g. Playwright). Insert-only
  // and idempotent, so this is safe to click repeatedly — a config that
  // already exists is reported in mcps_skipped, never duplicated/overwritten.
  const [seedResult, setSeedResult] = useState<SeedDefaultMCPsResponse | null>(null)
  const { mutate: seedDefaults, isPending: seeding } = useMutation({
    mutationFn: () => mcpApi.seedDefaults(),
    onSuccess:  data => {
      qc.invalidateQueries({ queryKey: ['mcp'] })
      setSeedResult(data)
      setTimeout(() => setSeedResult(null), 6000)
    },
  })

  // Pre-open edit form when navigated here from the project MCP tab
  const mcpBrowseId         = useChatStore(s => s.mcpBrowseId)
  const clearMCPBrowse      = useChatStore(s => s.clearMCPBrowse)
  const mcpReturnProjectId  = useChatStore(s => s.mcpReturnProjectId)
  const setViewProject      = useChatStore(s => s.setViewProject)
  const setProjectBrowseTab = useChatStore(s => s.setProjectBrowseTab)

  const closeForm = () => {
    setEditingId(null)
    setForm(BLANK)
    if (mcpReturnProjectId) {
      setViewProject(mcpReturnProjectId)
      setProjectBrowseTab('mcp')
    }
  }

  const openCreate = () => { setForm(BLANK); setEditingId('__new__') }

  const openEdit = (s: MCPConfig) => {
    setForm({
      name:       s.name,
      transport:  s.transport,
      command:    s.command ?? '',
      args:       (s.args ?? []).join(', '),
      url:        s.url ?? '',
      enabled:    s.enabled,
      project_id: s.project_id ?? '',
    })
    setEditingId(s.id)
  }

  // Auto-open edit form when arriving from the project MCP tab (mcpBrowseId set)
  useEffect(() => {
    if (!mcpBrowseId || servers.length === 0) return
    const target = servers.find(s => s.id === mcpBrowseId)
    if (target) {
      openEdit(target)
      clearMCPBrowse()   // clear so re-opening the panel later shows the list
    }
  // openEdit is stable within the render — safe to omit from deps
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcpBrowseId, servers])

  const handleSave = () => {
    const body = {
      name:       form.name.trim(),
      transport:  form.transport,
      command:    form.transport === 'stdio' ? form.command.trim() || undefined : undefined,
      args:       form.transport === 'stdio' ? form.args.split(',').map(a => a.trim()).filter(Boolean) : [],
      url:        form.transport !== 'stdio' ? form.url.trim()    || undefined : undefined,
      enabled:    form.enabled,
      project_id: form.project_id || null,   // V5.7 — empty string → null (global)
    }
    if (editingId === '__new__') createServer(body)
    else if (editingId)          updateServer({ id: editingId, body })
  }

  const isSubmitting = creating || updating
  // V6.39 (Section 88) — '__' is reserved as the internal tool-name delimiter
  // ("<config_name>__<tool_name>"); the backend 422s on create/update if the
  // name contains it. Validate inline rather than waiting for that round-trip.
  const nameHasDelimiter = form.name.includes('__')
  const canSave = !!form.name.trim() && !nameHasDelimiter && (
    (form.transport === 'stdio' && !!form.command.trim()) ||
    (form.transport !== 'stdio' && !!form.url.trim())
  )

  const handleRefresh = () => { refetch(); refetchHealth() }

  // Health summary counts
  const healthyCount   = healthList.filter(h => h.status === 'healthy').length
  const unhealthyCount = healthList.filter(h => h.status === 'unhealthy').length

  return (
    <div className="detail animate-slide-in-left">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="detail-header">
        <div
          className="dh-icon"
          style={{
            background: 'linear-gradient(135deg, rgba(124,108,243,0.18), rgba(0,201,255,0.1))',
            borderColor: 'rgba(124,108,243,0.3)',
            color: 'var(--neural)',
          }}
        >
          <Plug2 size={20} />
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">MCP Servers</h2>
          <p className="dh-sub">
            {isLoading ? 'Loading…' : `${servers.length} server${servers.length !== 1 ? 's' : ''} configured`}
            {!healthLoading && healthyCount > 0   && ` · ${healthyCount} healthy`}
            {!healthLoading && unhealthyCount > 0 && ` · ${unhealthyCount} error`}
          </p>
        </div>

        <div className="detail-actions" style={{ position: 'relative' }}>
          {seedResult && (
            <div
              style={{
                position: 'absolute', bottom: '100%', right: 0, marginBottom: 8,
                background: 'var(--bg-surface3)', border: '1px solid var(--border-mid)',
                borderRadius: 8, padding: '8px 12px', fontSize: 11.5, color: 'var(--text-secondary)',
                whiteSpace: 'nowrap', boxShadow: '0 4px 20px rgba(0,0,0,0.5)', zIndex: 10,
              }}
            >
              {seedResult.mcps_seeded} added, {seedResult.mcps_skipped} already present
              {seedResult.mcps_pending_install.length > 0 && (
                <div style={{ marginTop: 4, color: 'var(--amber)' }}>
                  {seedResult.mcps_pending_install.map(p => `${p.name} needs ${p.missing.join(', ')}`).join(' · ')}
                </div>
              )}
            </div>
          )}
          <button
            className="btn ghost"
            style={{ fontSize: 12, gap: 5 }}
            title="Add any default MCPs (e.g. Playwright) this account is missing — safe to click again, existing ones are skipped"
            onClick={() => seedDefaults()}
            disabled={seeding}
          >
            <RefreshCw size={12} className={seeding ? 'animate-spin' : ''} /> Restore default MCPs
          </button>
          <button
            className="icon-btn"
            style={{ width: 30, height: 30 }}
            title="Refresh health"
            onClick={handleRefresh}
          >
            <RefreshCw size={14} />
          </button>
          <button className="btn primary" onClick={openCreate}>
            <Plus size={12} /> Add Server
          </button>
        </div>
      </div>

      {/* ── Body ───────────────────────────────────────────────────────────── */}
      <div className="detail-body" style={{ maxWidth: 'none' }}>

        {/* Form (add / edit) */}
        {editingId && (
          <ServerForm
            form={form}
            setForm={setForm}
            onSave={handleSave}
            onCancel={closeForm}
            isNew={editingId === '__new__'}
            isPending={isSubmitting}
            canSave={canSave}
            projects={nonArchivedProjects}
          />
        )}

        {/* Server list */}
        {!editingId && (
          <>
            {isLoading && (
              <div className="flex items-center justify-center gap-2 py-10" style={{ color: 'var(--text-muted)', fontSize: 13 }}>
                <Loader2 size={16} className="animate-spin" /> Loading servers…
              </div>
            )}

            {!isLoading && servers.length === 0 && <EmptyState onAdd={openCreate} />}

            {!isLoading && servers.length > 0 && (
              <>
                {servers.map(s => (
                  <ServerRow
                    key={s.id}
                    server={s}
                    health={healthMap[s.id]}
                    expanded={expandedId === s.id}
                    onToggleExpand={() => setExpandedId(expandedId === s.id ? null : s.id)}
                    onEdit={() => openEdit(s)}
                    onDelete={() => deleteServer(s.id)}
                    onToggleEnabled={enabled => toggleEnabled({ id: s.id, enabled })}
                    onSync={() => syncServer(s.id)}
                    deleting={deleting && deleteVars === s.id}
                    toggling={toggling && toggleVars?.id === s.id}
                    syncing={syncing && syncVars === s.id}
                    projectName={s.project_id ? (projectMap[s.project_id] ?? null) : null}
                    syncError={syncErrors[s.id]}
                  />
                ))}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
