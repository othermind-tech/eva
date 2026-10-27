import { useState, useRef, useEffect } from 'react'
import { SlidersHorizontal, RotateCcw, Info, Settings } from 'lucide-react'
import type { AgentMode, ToolApprovalMode, SkillsMode } from '@/types/api'
import { useChatStore } from '@/store/chatStore'

// ─── Session patch shape (subset of SessionConfig) ───────────────────────────
export interface ModesPatch {
  agent_mode?:         AgentMode
  tool_approval_mode?: ToolApprovalMode
  skills_mode?:        SkillsMode
}

export interface ModesButtonProps {
  agentMode:    AgentMode
  toolApproval: ToolApprovalMode
  skillMode:    SkillsMode
  /** Called with a partial patch — wired to sessionStore.setConfig */
  onChange: (patch: ModesPatch) => void
}

// ─── Option tables ────────────────────────────────────────────────────────────
const AGENT_OPTS: { value: AgentMode; label: string; desc: string }[] = [
  { value: 'auto',         label: 'Auto',    desc: 'EVA decides plan vs. converse per turn.' },
  { value: 'chat',         label: 'Converse', desc: 'Just chat — no autonomous plans or multi-step actions.' },
  { value: 'plan_execute', label: 'Plan',    desc: 'Always draft a plan, then execute step-by-step.' },
]

const APPROVAL_OPTS: { value: ToolApprovalMode; label: string; desc: string }[] = [
  { value: 'auto',             label: 'Auto',  desc: 'Read-only tools run silently · mutating tools prompt for approval.' },
  { value: 'always_ask',       label: 'All',   desc: 'Every tool call asks before running.' },
  { value: 'per_conversation', label: 'Trust', desc: 'Auto-approve everything except destructive tools.' },
]

const SKILL_OPTS: { value: SkillsMode; label: string; desc: string }[] = [
  { value: 'auto',     label: 'Auto',    desc: 'EVA picks a skill if relevant, otherwise none.' },
  { value: 'enabled',  label: 'Enabled', desc: 'Always apply the active skill preset.' },
  { value: 'disabled', label: 'Disabled', desc: 'Never use a skill preset — pure model behaviour.' },
]

const DEFAULTS: { agentMode: AgentMode; toolApproval: ToolApprovalMode; skillMode: SkillsMode } = {
  agentMode:    'auto',
  toolApproval: 'auto',
  skillMode:    'auto',
}

// ─── Segmented control ────────────────────────────────────────────────────────
function Segmented<T extends string>({
  value, options, onChange, ariaLabel,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
  ariaLabel: string
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={ariaLabel}>
      {options.map(o => (
        <button
          key={o.value}
          type="button"
          className={o.value === value ? 'active' : ''}
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────
export function ModesButton({ agentMode, toolApproval, skillMode, onChange }: ModesButtonProps) {
  const [open, setOpen] = useState(false)
  const containerRef    = useRef<HTMLDivElement>(null)
  const triggerRef      = useRef<HTMLButtonElement>(null)
  const setViewSettings  = useChatStore(s => s.setViewSettings)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node))
        setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); triggerRef.current?.focus() }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const changeCount = [
    agentMode    !== DEFAULTS.agentMode,
    toolApproval !== DEFAULTS.toolApproval,
    skillMode    !== DEFAULTS.skillMode,
  ].filter(Boolean).length
  const hasChanges = changeCount > 0

  const reset = () => onChange({
    agent_mode:         'auto',
    tool_approval_mode: 'auto',
    skills_mode:        'auto',
  })

  const descOf = <T extends string>(opts: { value: T; desc: string }[], v: T) =>
    opts.find(o => o.value === v)?.desc

  return (
    <div ref={containerRef} className="group" style={{ position: 'relative' }}>
      <button
        ref={triggerRef}
        type="button"
        className={'modes-trigger' + (open ? ' open' : '') + (hasChanges ? ' has-changes' : '')}
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Mode settings"
        title="Mode settings"
      >
        <SlidersHorizontal size={12} />
        <span>Modes</span>
        {hasChanges && <span className="mt-dot" title={`${changeCount} non-default`} />}
      </button>

      {/* Always rendered — hover peeks (opacity only), click pins (pointer-events-auto) */}
      <div
        className={`modes-panel ${
          open
            ? 'opacity-100 pointer-events-auto'
            : 'opacity-0 pointer-events-none group-hover:opacity-100'
        }`}
        role="menu"
        aria-label="Mode settings"
      >
          {/* Header — matches Active Model popup style */}
          <p className="mp-head-title">Chat Modes</p>

          {/* Agent row */}
          <div className="seg-row">
            <div className="seg-row-top">
              <div className="seg-label">
                Agent
                <span className="seg-info">
                  <Info size={10} />
                  <span className="seg-tooltip">{descOf(AGENT_OPTS, agentMode)}</span>
                </span>
              </div>
              <Segmented
                value={agentMode}
                options={AGENT_OPTS}
                onChange={v => onChange({ agent_mode: v })}
                ariaLabel="Agent mode"
              />
            </div>
          </div>

          {/* Approval row */}
          <div className="seg-row">
            <div className="seg-row-top">
              <div className="seg-label">
                Approval
                <span className="seg-info">
                  <Info size={10} />
                  <span className="seg-tooltip">{descOf(APPROVAL_OPTS, toolApproval)}</span>
                </span>
              </div>
              <Segmented
                value={toolApproval}
                options={APPROVAL_OPTS}
                onChange={v => onChange({ tool_approval_mode: v })}
                ariaLabel="Tool approval mode"
              />
            </div>
          </div>

          {/* Skill row */}
          <div className="seg-row">
            <div className="seg-row-top">
              <div className="seg-label">
                Skill
                <span className="seg-info">
                  <Info size={10} />
                  <span className="seg-tooltip">{descOf(SKILL_OPTS, skillMode)}</span>
                </span>
              </div>
              <Segmented
                value={skillMode}
                options={SKILL_OPTS}
                onChange={v => onChange({ skills_mode: v })}
                ariaLabel="Skill mode"
              />
            </div>
          </div>

          {/* Footer */}
          <div className="mp-foot" style={{ justifyContent: 'space-between' }}>
            {/* Left — gear + Change Settings */}
            <button
              type="button"
              className="mp-reset"
              style={{ display: 'flex', alignItems: 'center', gap: 5 }}
              onClick={() => { setOpen(false); setViewSettings() }}
            >
              <Settings size={11} />
              Change Settings
            </button>

            {/* Right — Reset to defaults (only when dirty) */}
            {hasChanges && (
              <button type="button" className="mp-reset" onClick={reset}>
                <RotateCcw size={11} style={{ marginRight: 4, verticalAlign: '-1px' }} />
                Reset to defaults
              </button>
            )}
          </div>
        </div>
    </div>
  )
}
