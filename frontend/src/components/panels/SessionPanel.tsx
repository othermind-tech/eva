import { useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Settings, Loader2, CheckCircle2,
  RefreshCw, Brain, Bot, Wand2,
  ChevronDown, ChevronRight, Search,
  Shield, Lock, Info,
} from 'lucide-react'
import { toolsApi, providersApi } from '@/api/tools'
import { conversationsApi } from '@/api/conversations'
import { useSessionStore } from '@/store/sessionStore'
import { useChatStore } from '@/store/chatStore'
import ReasoningControl from '@/components/ui/ReasoningControl'
import type {
  AgentMode, ToolApprovalMode, SkillsMode, Tool,
} from '@/types/api'

// ─── Authority options ───────────────────────────────────────────────────────
const AUTHORITY_OPTIONS = [
  { value: 'read_only',   label: 'Read Only',   color: 'var(--success)', dim: 'rgba(0,212,160,0.1)',  border: 'rgba(0,212,160,0.3)'  },
  { value: 'mutating',    label: 'Mutating',    color: 'var(--amber)',   dim: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.3)' },
  { value: 'destructive', label: 'Destructive', color: 'var(--danger)',  dim: 'rgba(255,77,109,0.1)', border: 'rgba(255,77,109,0.3)' },
] as const

// ─── Main panel ──────────────────────────────────────────────────────────────
export default function SessionPanel() {
  const config    = useSessionStore(s => s.config)
  const setConfig = useSessionStore(s => s.setConfig)
  // Saving to the backend is handled centrally by useSessionAutoSave (mounted
  // once in ChatPage) — this panel only reflects the shared status.
  const autoSaveStatus = useSessionStore(s => s.saveStatus)

  // Per-model capabilities — same TanStack Query cache as WorkspacePanel + MessageInput
  const { data: modelCaps } = useQuery({
    queryKey: ['capabilities', config.provider, config.model],
    queryFn:  () => providersApi.getCapabilities(config.provider!, config.model!),
    enabled:  !!(config.provider && config.model),
    staleTime: 5 * 60_000,
  })

  // Section 66 — Agent scope auto-linkage indicator
  const activeConvId = useChatStore(s => s.activeConversationId)
  const { data: activeConv } = useQuery({
    queryKey: ['conversation', activeConvId],
    queryFn:  () => conversationsApi.get(activeConvId!),
    enabled:  !!activeConvId && (config.agent_memory_enabled ?? true),
    staleTime: 60_000,
  })
  const autoScope     = activeConv?.project_id ?? null
  const explicitScope = config.agent_scope_key || null
  const activeScope   = explicitScope || autoScope
  const isAutoScope   = !explicitScope && !!autoScope

  // All tools
  const { data: tools = [], isLoading: toolsLoading } = useQuery({
    queryKey: ['tools'],
    queryFn:  toolsApi.listAll,
  })

  // Tool groups — keyed on `source` field returned by the backend
  const defaultTools = tools.filter((t: Tool) => t.source === 'default')
  const mcpTools     = tools.filter((t: Tool) => t.source === 'mcp')
  const customTools  = tools.filter((t: Tool) => t.source === 'custom')

  const handleToolToggle = (name: string) => {
    const current = config.tools_enabled ?? []
    setConfig({ tools_enabled: current.includes(name) ? current.filter(n => n !== name) : [...current, name] })
  }

  return (
    <div
      className="flex flex-col flex-1 h-full overflow-hidden animate-slide-in-left"
      style={{ background: 'var(--bg-base)' }}
    >
      {/* ── Header ── */}
      <div className="detail-header">
        <div className="dh-icon" style={{ background: 'rgba(0,201,255,0.08)', borderColor: 'rgba(0,201,255,0.2)', color: 'var(--eva-primary)' }}>
          <Settings size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">Session Settings</h2>
          <p className="dh-sub">Behaviour · Tools</p>
        </div>
        <div className="detail-actions">
          {autoSaveStatus !== 'idle' && (
            <span
              className="flex items-center gap-1.5"
              style={{ fontSize: 11, color: autoSaveStatus === 'saved' ? 'var(--success)' : 'var(--text-muted)' }}
            >
              {autoSaveStatus === 'saving'
                ? <><Loader2 size={10} className="animate-spin" /> Saving…</>
                : <><CheckCircle2 size={10} /> Saved</>}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-5">

        {/* ━━━ BEHAVIOUR ━━━ */}
        <CollapsibleSection label="Behaviour" defaultOpen={true}>

          {/* Description */}
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '-4px 0 12px', lineHeight: 1.5 }}>
            How EVA plans, uses tools, and remembers — defaults for new conversations.
          </p>

          {/* behavior-grid: 4 equal cols; last 2 cards span 2 each */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>

            {/* Agent mode */}
            <BehaviourCard
              icon={<Bot size={13} />}
              iconBg="var(--neural-dim)"  iconColor="var(--neural)"
              title="Agent mode"  subtitle="How EVA plans + acts"
              hint={
                config.agent_mode === 'chat'         ? 'No planning. Only chat mode.' :
                config.agent_mode === 'plan_execute' ? 'EVA always uses a plan-execute loop.' :
                                                       'EVA automatically plans when needed.'
              }
            >
              <BehaviourSeg
                value={config.agent_mode ?? 'auto'}
                onChange={v => setConfig({ agent_mode: v as AgentMode })}
                options={[
                  { value: 'auto',         label: 'Auto' },
                  { value: 'chat',         label: 'Chat' },
                  { value: 'plan_execute', label: 'Plan' },
                ]}
              />
            </BehaviourCard>

            {/* Skill mode */}
            <BehaviourCard
              icon={<Wand2 size={13} />}
              iconBg="var(--eva-primary-dim)"  iconColor="var(--eva-primary)"
              title="Skill mode"  subtitle="Apply skill presets"
              hint={
                config.skills_mode === 'enabled'  ? 'Skill presets are always applied.' :
                config.skills_mode === 'disabled' ? 'No skill presets will be used.' :
                                                    'EVA applies skills when relevant.'
              }
            >
              <BehaviourSeg
                value={config.skills_mode ?? 'auto'}
                onChange={v => setConfig({ skills_mode: v as SkillsMode })}
                options={[
                  { value: 'auto',     label: 'Auto' },
                  { value: 'enabled',  label: 'On'   },
                  { value: 'disabled', label: 'Off'  },
                ]}
              />
            </BehaviourCard>

            {/* Tool approval */}
            <BehaviourCard
              icon={<Shield size={13} />}
              iconBg="var(--amber-dim)"  iconColor="var(--amber)"
              title="Tool approval"  subtitle="When EVA asks before running"
              hint={
                config.tool_approval_mode === 'always_ask'       ? 'Every tool call asks for approval.' :
                config.tool_approval_mode === 'per_conversation' ? 'Auto-approve once trusted per session.' :
                                                                   'Silent for read-only; prompts for mutating.'
              }
            >
              <BehaviourSeg
                value={config.tool_approval_mode ?? 'auto'}
                onChange={v => setConfig({ tool_approval_mode: v as ToolApprovalMode })}
                options={[
                  { value: 'auto',             label: 'Auto'  },
                  { value: 'always_ask',       label: 'Ask'   },
                  { value: 'per_conversation', label: 'Trust' },
                ]}
                activeColor="var(--amber)"
                activeDim="var(--amber-dim)"
              />
            </BehaviourCard>

            {/* Allowed authority */}
            <BehaviourCard
              icon={<Lock size={13} />}
              iconBg="var(--danger-dim)"  iconColor="var(--danger)"
              title="Allowed authority"  subtitle="Highest tool risk permitted"
              hint={
                getMaxAuthority(config.allowed_authorities) === 'destructive' ? 'All tools including destructive are permitted.' :
                getMaxAuthority(config.allowed_authorities) === 'mutating'    ? 'State-changing tools are permitted.' :
                                                                                'Only read-only tools are permitted.'
              }
            >
              <BehaviourSeg
                value={getMaxAuthority(config.allowed_authorities)}
                onChange={v => setConfig({ allowed_authorities: maxAuthorityToValue(v) })}
                options={[
                  { value: 'read_only',   label: 'Read' },
                  { value: 'mutating',    label: 'Mutating', tone: 'amber'  },
                  { value: 'destructive', label: 'All',  tone: 'danger' },
                ]}
              />
            </BehaviourCard>

            {/* Memory — spans 2 columns */}
            <BehaviourCard
              icon={<Brain size={13} />}
              iconBg="var(--neural-dim)"  iconColor="var(--neural)"
              title="Memory"  subtitle="What EVA remembers between turns and chats"
              span={2}
            >
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <BcTogRow label="Chat memory"  checked={config.user_memory_enabled  ?? true}  onChange={v => setConfig({ user_memory_enabled:  v })} />
                <BcTogRow label="Agent memory" checked={config.agent_memory_enabled ?? true}  onChange={v => setConfig({ agent_memory_enabled: v })} />
              </div>
              {(config.agent_memory_enabled ?? true) && activeScope && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '4px 8px',
                  background: 'rgba(124,108,243,0.07)',
                  border: '1px solid rgba(124,108,243,0.18)',
                  borderRadius: 6,
                }}>
                  <Brain size={9} style={{ color: 'var(--neural)', flexShrink: 0 }} />
                  <span style={{ fontSize: 10, color: 'var(--text-muted)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    scope: <code style={{ fontSize: 9, color: 'var(--neural)', fontFamily: 'JetBrains Mono, monospace' }}>{activeScope}</code>
                  </span>
                  {isAutoScope && (
                    <span style={{
                      fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
                      background: 'rgba(124,108,243,0.15)', border: '1px solid rgba(124,108,243,0.3)',
                      color: 'var(--neural)', borderRadius: 4, padding: '1px 5px', flexShrink: 0,
                    }}>AUTO</span>
                  )}
                </div>
              )}
            </BehaviourCard>

            {/* Output & safety — spans 2 columns */}
            <BehaviourCard
              icon={<Settings size={13} />}
              iconBg="rgba(255,255,255,0.05)"  iconColor="var(--text-secondary)"
              title="Output & safety"  subtitle="Reasoning and shell guard"
              span={2}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {modelCaps?.supports_reasoning !== false && (
                  <ReasoningControl
                    supports_reasoning={modelCaps?.supports_reasoning ?? true}
                    levels={modelCaps?.reasoning_levels ?? null}
                    canDisable={modelCaps?.reasoning_can_disable ?? null}
                    value={config.reasoning}
                    onChange={v => setConfig({ reasoning: v })}
                  />
                )}
                <BcTogRow label="Shell safety" checked={config.shell_readonly ?? false} onChange={v => setConfig({ shell_readonly:  v })}
                  desc="Block all write & destructive shell commands." />
                <BcTogRow label="Plan approval" checked={config.require_plan_approval ?? false} onChange={v => setConfig({ require_plan_approval: v })}
                  desc="Pause multi-step executions for you to review and edit the plan before EVA runs it." />
                <BcTogRow label="PII redaction" checked={config.pii_redaction_enabled ?? false} onChange={v => setConfig({ pii_redaction_enabled: v })}
                  desc="Scrub emails, phone numbers, SSNs, and card numbers from your messages before they're sent."
                  hint="Applies to your message text before it's stored, journaled, or sent to the model — emails, phone numbers, SSNs, and credit card / bank account / passport / driver's license numbers are replaced with placeholders like <EMAIL_ADDRESS>. Names, locations, and dates are intentionally left alone since EVA needs those for normal conversation. Off by default." />
                <BcTogRow label="Skip completed steps" checked={config.pev_lookahead_skip_enabled ?? true} onChange={v => setConfig({ pev_lookahead_skip_enabled: v })}
                  desc="If a step's work already accomplishes a later step, skip re-running it."
                  hint="EVA checks each step's own evidence against the steps planned right after it. When a later step's goal is already achieved, it's marked skipped instead of run again. It never skips a plan's final step. On by default." />
                <BcNumRow
                  label="Step extensions"
                  desc="How many times you can grant more steps when EVA hits its step limit. 0 disables the prompt."
                  value={config.max_step_extensions ?? 2}
                  onChange={v => setConfig({ max_step_extensions: v })}
                  min={0}
                  max={10}
                />
              </div>
            </BehaviourCard>

          </div>
        </CollapsibleSection>

        {/* ━━━ TOOLS ━━━ */}
        <CollapsibleSection label="Tools" defaultOpen={true}>
          <ToolsContent
            toolsLoading={toolsLoading}
            defaultTools={defaultTools}
            mcpTools={mcpTools}
            customTools={customTools}
            config={config}
            onToolToggle={handleToolToggle}
          />
        </CollapsibleSection>


      </div>
    </div>
  )
}

// Parse allowed_authorities config value → string[]  (handles comma-string or array)
function parseAuthorities(v?: string | string[]): string[] {
  if (!v) return AUTHORITY_OPTIONS.map(a => a.value)
  if (Array.isArray(v)) return v
  const parts = v.split(',').map(s => s.trim()).filter(Boolean)
  return parts.length > 0 ? parts : AUTHORITY_OPTIONS.map(a => a.value)
}

// Derive single "max authority" value from the allowed set (for the single-select card control)
function getMaxAuthority(v?: string | string[]): string {
  const arr = parseAuthorities(v)
  if (arr.includes('destructive')) return 'destructive'
  if (arr.includes('mutating'))    return 'mutating'
  return 'read_only'
}

// Expand a single max-level selection back to the cumulative allowed set
function maxAuthorityToValue(max: string): string {
  if (max === 'destructive') return 'read_only,mutating,destructive'
  if (max === 'mutating')    return 'read_only,mutating'
  return 'read_only'
}

// ─── ToolsContent — tool browser only ────────────────────────────────────────
function ToolsContent({
  toolsLoading, defaultTools, mcpTools, customTools, config, onToolToggle,
}: {
  toolsLoading:  boolean
  defaultTools:  Tool[]
  mcpTools:      Tool[]
  customTools:   Tool[]
  config:        ReturnType<typeof useSessionStore.getState>['config']
  onToolToggle:  (name: string) => void
}) {
  const [search, setSearch] = useState('')

  const filter = (list: Tool[]) =>
    !search.trim() ? list : list.filter(t =>
      t.name.toLowerCase().includes(search.toLowerCase()) ||
      (t.description ?? '').toLowerCase().includes(search.toLowerCase())
    )

  const filteredDefault = filter(defaultTools)
  const filteredCustom  = filter(customTools)
  const filteredMcp     = filter(mcpTools)
  const searchActive    = search.trim().length > 0
  const noneFound       = searchActive &&
    filteredDefault.length + filteredCustom.length + filteredMcp.length === 0

  return (
    <div className="flex flex-col gap-4">

      {/* Search */}
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg"
        style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}>
        <Search size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
        <input
          type="text"
          placeholder="Search tools…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="flex-1 text-xs outline-none bg-transparent"
          style={{ color: 'var(--text-primary)' }}
        />
      </div>

      {/* Browser groups — loading / empty / populated */}
      {toolsLoading ? (
        <div className="flex items-center gap-2 text-xs py-2" style={{ color: 'var(--text-muted)' }}>
          <Loader2 size={11} className="animate-spin" /> Loading tools…
        </div>
      ) : noneFound ? (
        <p className="text-xs text-center py-2" style={{ color: 'var(--text-muted)' }}>
          No tools match "{search}"
        </p>
      ) : (
        <>
          <ToolBrowserGroup label="Default" tools={filteredDefault} badgeColor="var(--eva-primary)"
            isDefault enabledNames={config.tools_enabled ?? []} onToggle={onToolToggle}
            allowedAuthorities={parseAuthorities(config.allowed_authorities)} />
          <ToolBrowserGroup label="Custom"  tools={filteredCustom}  badgeColor="var(--amber)"
            enabledNames={config.tools_enabled ?? []} onToggle={onToolToggle}
            allowedAuthorities={parseAuthorities(config.allowed_authorities)} />
          <ToolBrowserGroup label="MCP"     tools={filteredMcp}     badgeColor="var(--neural)"
            enabledNames={config.tools_enabled ?? []} onToggle={onToolToggle}
            allowedAuthorities={parseAuthorities(config.allowed_authorities)} />
        </>
      )}

    </div>
  )
}

// ─── ToolBrowserGroup — collapsible group, click-to-show description + toggle ─
function ToolBrowserGroup({
  label, tools, badgeColor, isDefault = false, enabledNames = [], onToggle, allowedAuthorities = [],
}: {
  label:               string
  tools:               Tool[]
  badgeColor:          string
  isDefault?:          boolean
  enabledNames?:       string[]
  onToggle?:           (name: string) => void
  allowedAuthorities?: string[]
}) {
  const [open, setOpen]         = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)

  // Normalise authority strings for comparison (backend uses "read-only", config uses "read_only")
  const norm = (s: string) => s.replace(/-/g, '_').toLowerCase()
  const normAllowed = allowedAuthorities.map(norm)

  // A tool is authority-blocked when its authority is known but NOT in the allowed set
  const isAuthBlocked = (t: Tool) =>
    !!t.authority && normAllowed.length > 0 && !normAllowed.includes(norm(t.authority))

  // Scroller kicks in once the group exceeds 5 tools
  const SCROLL_THRESHOLD = 5
  const needsScroll = tools.length > SCROLL_THRESHOLD

  return (
    <div>
      <button onClick={() => setOpen(o => !o)} className="flex items-center gap-1.5 w-full mb-1.5 px-0.5">
        {open
          ? <ChevronDown  size={10} style={{ color: badgeColor }} />
          : <ChevronRight size={10} style={{ color: badgeColor }} />}
        <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--text-secondary)' }}>
          {label}
        </span>
        <span className="text-xs px-1.5 py-0.5 rounded-full"
          style={{ background: `${badgeColor}18`, color: badgeColor, border: `1px solid ${badgeColor}30`, fontSize: 9 }}>
          {tools.length}
        </span>
      </button>

      {open && (
        /* Scroll wrapper — plain block div so maxHeight isn't fought by flex layout */
        <div style={needsScroll ? { maxHeight: 200, overflowY: 'auto', overflowX: 'hidden' } : undefined}>
          <div className="flex flex-col gap-1 pl-2">
          {tools.length === 0 && (
            <p className="text-xs py-2 px-1" style={{ color: 'var(--text-muted)' }}>
              No {label} tools exist in the system
            </p>
          )}
          {tools.map(t => {
            const showDesc  = expanded === t.name
            const blocked   = isAuthBlocked(t)
            // A tool is effectively enabled only if: not authority-blocked AND (default OR in enabledNames)
            const isEnabled = !blocked && (isDefault || enabledNames.includes(t.name))

            return (
              <div key={t.name} className="rounded-lg overflow-hidden"
                style={{
                  background: 'var(--bg-surface2)',
                  border: `1px solid ${blocked ? 'rgba(255,77,109,0.2)' : 'var(--border-dim)'}`,
                  opacity: blocked ? 0.6 : 1,
                }}>

                {/* Tool row */}
                <div className="flex items-center gap-2 px-2.5 py-2"
                  style={{ background: showDesc ? 'var(--eva-primary-dim)' : 'transparent' }}>

                  {/* Name — click expands description */}
                  <button
                    onClick={() => setExpanded(d => d === t.name ? null : t.name)}
                    className="flex items-center gap-2 flex-1 min-w-0 text-left"
                  >
                    <span className="text-xs font-mono font-medium flex-1 truncate"
                      style={{ color: showDesc ? 'var(--eva-primary)' : blocked ? 'var(--text-muted)' : 'var(--text-primary)' }}>
                      {t.name}
                    </span>
                    {t.authority && <AuthorityBadge authority={t.authority} />}
                    {/* Section 59: memory_recall Active Recall Mode indicator */}
                    {t.name === 'memory_recall' && isEnabled && (
                      <span
                        className="flex items-center gap-1 px-1.5 py-0.5 rounded-full shrink-0"
                        title="Active Recall Mode — EVA retrieves stored facts on demand rather than pre-loading them into context."
                        style={{
                          fontSize: 9, fontWeight: 600,
                          background: 'rgba(124,108,243,0.12)',
                          border: '1px solid rgba(124,108,243,0.3)',
                          color: 'var(--neural)',
                        }}
                      >
                        <RefreshCw size={8} />
                        Active recall
                      </span>
                    )}
                    <ChevronDown size={9} style={{
                      color: 'var(--text-muted)',
                      transform: showDesc ? 'rotate(180deg)' : 'none',
                      transition: 'transform 0.15s',
                      flexShrink: 0,
                    }} />
                  </button>

                  {/* Status pill */}
                  {blocked ? (
                    // Authority-blocked — shown for ALL tool types including default
                    <span className="shrink-0 text-xs px-2 py-0.5 rounded-full"
                      style={{
                        fontSize: 9, fontWeight: 600,
                        background: 'rgba(255,77,109,0.1)',
                        color: 'var(--danger)',
                        border: '1px solid rgba(255,77,109,0.25)',
                      }}>
                      Auth Blocked
                    </span>
                  ) : isDefault ? (
                    // Default, not blocked — always on
                    <span className="shrink-0 text-xs px-2 py-0.5 rounded-full"
                      style={{
                        fontSize: 9, fontWeight: 600,
                        background: 'rgba(0,212,160,0.1)',
                        color: 'var(--success)',
                        border: '1px solid rgba(0,212,160,0.25)',
                      }}>
                      Always On
                    </span>
                  ) : (
                    // Custom / MCP — toggleable
                    <button
                      onClick={e => { e.stopPropagation(); onToggle?.(t.name) }}
                      className="shrink-0 text-xs px-2 py-0.5 rounded-full transition-all duration-150"
                      style={{
                        fontSize: 9, fontWeight: 600,
                        background: isEnabled ? 'rgba(0,201,255,0.12)' : 'rgba(255,77,109,0.1)',
                        color:      isEnabled ? 'var(--eva-primary)'   : 'var(--danger)',
                        border:     `1px solid ${isEnabled ? 'rgba(0,201,255,0.3)' : 'rgba(255,77,109,0.25)'}`,
                      }}>
                      {isEnabled ? 'Allowed' : 'Disabled'}
                    </button>
                  )}
                </div>

                {/* Expandable description */}
                {showDesc && (
                  <div className="px-3 pb-2.5" style={{ borderTop: '1px solid var(--border-dim)' }}>
                    <p className="text-xs leading-relaxed pt-2" style={{ color: 'var(--text-secondary)' }}>
                      {t.description || 'No description available.'}
                    </p>
                    {/* Section 59: memory_recall active recall mode info */}
                    {t.name === 'memory_recall' && isEnabled && (
                      <div
                        className="mt-2 px-2.5 py-2 rounded-lg flex items-start gap-2"
                        style={{ background: 'rgba(124,108,243,0.08)', border: '1px solid rgba(124,108,243,0.2)' }}
                      >
                        <RefreshCw size={10} style={{ color: 'var(--neural)', marginTop: 1, flexShrink: 0 }} />
                        <p className="text-xs leading-relaxed" style={{ color: 'var(--neural)' }}>
                          <strong>Active Recall Mode</strong> — Facts retrieved on demand rather than pre-loaded.
                          EVA will call this tool when memory is relevant, instead of automatically injecting
                          your stored facts into the system prompt.
                        </p>
                      </div>
                    )}
                    {blocked && (
                      <p className="text-xs mt-1.5 px-2 py-1 rounded-md"
                        style={{ background: 'rgba(255,77,109,0.08)', color: 'var(--danger)', border: '1px solid rgba(255,77,109,0.2)' }}>
                        Blocked — authority level "{t.authority}" is not in the allowed set.
                      </p>
                    )}
                    {t.source && (
                      <p className="text-xs mt-1 font-mono" style={{ color: 'var(--text-muted)' }}>
                        source: {t.source}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )
          })}
          </div>
        </div>
      )}
    </div>
  )
}


// ─── CollapsibleSection ───────────────────────────────────────────────────────
function CollapsibleSection({
  label, children, defaultOpen = true, badge,
}: {
  label:        ReactNode
  children:     ReactNode
  defaultOpen?: boolean
  badge?:       string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="form-section">
      <button onClick={() => setOpen(o => !o)} className="flex items-center justify-between w-full text-left" style={{ marginBottom: open ? 10 : 0 }}>
        <div className="flex items-center gap-2">
          <span className="section-label" style={{ fontSize: 10, padding: 0 }}>{label}</span>
          {badge && (
            <span className="chip" style={{ fontSize: 9, fontWeight: 600, padding: '2px 7px' }}>
              {badge}
            </span>
          )}
        </div>
        {open
          ? <ChevronDown  size={12} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
          : <ChevronRight size={12} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />}
      </button>
      {open && children}
    </div>
  )
}

// ─── ToggleSwitch (inline switch only, no row) ────────────────────────────────
export function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div
      onClick={e => { e.stopPropagation(); onChange(!checked) }}
      className={`toggle${checked ? ' on' : ''}`}
    />
  )
}

// ─── BehaviourCard ────────────────────────────────────────────────────────────
// Matches the HTML reference .behavior-card anatomy.
// layout="vertical"  — icon centred on top, title, optional subtitle, control below (narrow 4-col mode cards)
// layout="horizontal" (default) — .bc-head: icon left + title/subtitle right, control below (span-2 wide cards)
// span={N} sets grid-column: span N on the card
function BehaviourCard({
  icon, iconBg, iconColor, title, subtitle, hint, children, layout = 'horizontal', span,
}: {
  icon:      ReactNode
  iconBg:    string
  iconColor: string
  title:     string
  subtitle:  string
  hint?:     string
  children:  ReactNode
  layout?:   'horizontal' | 'vertical'
  span?:     number
}) {
  return (
    <div style={{
      background: 'var(--bg-surface2)',
      border: '1px solid var(--border-dim)',
      borderRadius: 12,
      padding: '13px 14px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
      ...(span ? { gridColumn: `span ${span}` } : {}),
    }}>
      {/* .bc-head */}
      {layout === 'vertical' ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
          <div style={{
            width: 26, height: 26, borderRadius: 7,
            background: iconBg, color: iconColor,
            display: 'grid', placeItems: 'center',
          }}>
            {icon}
          </div>
          <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', margin: 0, textAlign: 'center', lineHeight: 1.2 }}>
            {title}
          </p>
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9 }}>
          <div style={{
            width: 26, height: 26, borderRadius: 7,
            background: iconBg, color: iconColor,
            display: 'grid', placeItems: 'center', flexShrink: 0,
          }}>
            {icon}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <p style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
              {title}
            </p>
            <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 0', lineHeight: 1.4 }}>
              {subtitle}
            </p>
          </div>
          {hint && (
            <p style={{
              fontSize: 9.5, color: 'var(--text-muted)', margin: 0,
              textAlign: 'right', flexShrink: 0, maxWidth: 130,
              lineHeight: 1.4, fontStyle: 'italic', opacity: 0.55,
            }}>
              {hint}
            </p>
          )}
        </div>
      )}
      {/* .bc-control */}
      <div>{children}</div>
    </div>
  )
}

// ─── BehaviourSeg — matches HTML .seg-full with optional amber/danger tone ─────
function BehaviourSeg({
  options, value, onChange,
  activeColor = 'var(--eva-primary)',
  activeDim   = 'var(--eva-primary-dim)',
}: {
  options:      { value: string; label: string; tone?: 'amber' | 'danger' }[]
  value:        string
  onChange:     (v: string) => void
  activeColor?: string
  activeDim?:   string
}) {
  return (
    <div style={{
      display: 'flex',
      background: 'var(--bg-base)',
      border: '1px solid var(--border-mid)',
      borderRadius: 8,
      padding: 2,
      gap: 1,
    }}>
      {options.map(o => {
        const isActive = value === o.value
        const bg    = !isActive ? 'transparent'
          : o.tone === 'amber'  ? 'var(--amber-dim)'
          : o.tone === 'danger' ? 'var(--danger-dim)'
          : activeDim
        const color = !isActive ? 'var(--text-muted)'
          : o.tone === 'amber'  ? 'var(--amber)'
          : o.tone === 'danger' ? 'var(--danger)'
          : activeColor
        return (
          <button
            key={o.value}
            onClick={() => onChange(o.value)}
            style={{
              flex: 1, minWidth: 0,
              padding: '5px 6px',
              borderRadius: 6,
              fontSize: 11.5,
              fontWeight: 500,
              background: bg,
              color,
              cursor: 'pointer',
              transition: 'all 120ms ease',
              overflow: 'hidden',
              whiteSpace: 'nowrap',
              textOverflow: 'clip',
            }}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

// ─── BcTogRow — matches HTML .bc-toggle-row style ─────────────────────────────
function BcTogRow({
  label, checked, onChange, desc, hint,
}: {
  label:    string
  checked:  boolean
  onChange: (v: boolean) => void
  desc?:    string
  hint?:    string   // longer explanation shown via an (i) tooltip next to the label
}) {
  return (
    <button
      onClick={() => onChange(!checked)}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        background: 'var(--bg-base)',
        border: '1px solid var(--border-dim)',
        borderRadius: 8,
        padding: '8px 10px',
        cursor: 'pointer',
        width: '100%',
        textAlign: 'left',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>
            {label}
          </span>
          {hint && (
            <span title={hint} style={{ display: 'inline-flex', cursor: 'help', flexShrink: 0 }}>
              <Info size={11} style={{ color: 'var(--text-muted)' }} />
            </span>
          )}
        </span>
        {desc && (
          <p style={{ fontSize: 10.5, color: 'var(--text-muted)', margin: '3px 0 0', lineHeight: 1.4 }}>
            {desc}
          </p>
        )}
      </div>
      <ToggleSwitch checked={checked} onChange={onChange} />
    </button>
  )
}

// ─── BcNumRow — small numeric stepper, same visual language as BcTogRow ──────
function BcNumRow({
  label, desc, value, onChange, min, max,
}: {
  label:    string
  desc?:    string
  value:    number
  onChange: (v: number) => void
  min:      number
  max:      number
}) {
  const clamp = (v: number) => Math.min(max, Math.max(min, v))

  return (
    <div
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        background: 'var(--bg-base)',
        border: '1px solid var(--border-dim)',
        borderRadius: 8,
        padding: '8px 10px',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>
          {label}
        </span>
        {desc && (
          <p style={{ fontSize: 10.5, color: 'var(--text-muted)', margin: '3px 0 0', lineHeight: 1.4 }}>
            {desc}
          </p>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
        <button
          onClick={() => onChange(clamp(value - 1))}
          disabled={value <= min}
          style={{
            width: 22, height: 22, borderRadius: 6,
            background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)',
            color: value <= min ? 'var(--text-dim)' : 'var(--text-secondary)',
            cursor: value <= min ? 'default' : 'pointer', fontSize: 13, lineHeight: 1,
          }}
        >−</button>
        <span style={{
          minWidth: 20, textAlign: 'center', fontSize: 12.5, fontWeight: 600,
          color: 'var(--text-primary)', fontFamily: 'JetBrains Mono, monospace',
        }}>
          {value}
        </span>
        <button
          onClick={() => onChange(clamp(value + 1))}
          disabled={value >= max}
          style={{
            width: 22, height: 22, borderRadius: 6,
            background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)',
            color: value >= max ? 'var(--text-dim)' : 'var(--text-secondary)',
            cursor: value >= max ? 'default' : 'pointer', fontSize: 13, lineHeight: 1,
          }}
        >+</button>
      </div>
    </div>
  )
}

// ─── AuthorityBadge ───────────────────────────────────────────────────────────
function AuthorityBadge({ authority }: { authority: string }) {
  const styles: Record<string, { bg: string; color: string; border: string }> = {
    read_only:   { bg: 'rgba(0,212,160,0.08)',  color: 'var(--success)', border: 'rgba(0,212,160,0.25)'  },
    mutating:    { bg: 'rgba(245,158,11,0.08)', color: 'var(--amber)',   border: 'rgba(245,158,11,0.25)' },
    destructive: { bg: 'rgba(255,77,109,0.08)', color: 'var(--danger)',  border: 'rgba(255,77,109,0.25)' },
  }
  const s = styles[authority] ?? { bg: 'var(--bg-surface3)', color: 'var(--text-muted)', border: 'var(--border-dim)' }
  return (
    <span className="text-xs px-1.5 py-0.5 rounded-full shrink-0"
      style={{ background: s.bg, color: s.color, border: `1px solid ${s.border}`, fontSize: 9 }}>
      {authority.replace(/_/g, ' ')}
    </span>
  )
}
