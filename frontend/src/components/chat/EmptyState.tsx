import { Zap, Brain, Code2, FileSearch, Wrench, Lightbulb, BookOpen, Rocket } from 'lucide-react'
import { useAuthStore } from '@/store/authStore'
import { useSessionStore } from '@/store/sessionStore'

interface Props {
  onSelect: (text: string) => void
}

const SUGGESTIONS = [
  {
    icon: Brain,
    label: 'Think through a problem',
    sub:   'Step-by-step reasoning & analysis',
    prompt: 'Help me think through this step by step: ',
    color: 'var(--neural)',
    dim:   'rgba(124,108,243,0.08)',
    glow:  'rgba(124,108,243,0.15)',
  },
  {
    icon: Code2,
    label: 'Write or review code',
    sub:   'Debug, refactor, explain code',
    prompt: 'Review this code and suggest improvements:\n\n```\n\n```',
    color: 'var(--eva-primary)',
    dim:   'rgba(0,201,255,0.06)',
    glow:  'rgba(0,201,255,0.14)',
  },
  {
    icon: FileSearch,
    label: 'Search knowledge base',
    sub:   'Find answers from your documents',
    prompt: 'Search my knowledge base and summarize what you find about: ',
    color: 'var(--success)',
    dim:   'rgba(0,212,160,0.06)',
    glow:  'rgba(0,212,160,0.14)',
  },
  {
    icon: Wrench,
    label: 'Use tools to get things done',
    sub:   'Web search, file ops, and more',
    prompt: 'Use your available tools to help me: ',
    color: 'var(--amber)',
    dim:   'rgba(245,158,11,0.06)',
    glow:  'rgba(245,158,11,0.14)',
  },
  {
    icon: Rocket,
    label: 'Execute a multi-step plan',
    sub:   'Break down & complete complex tasks',
    prompt: 'Create and execute a step-by-step plan to: ',
    color: 'var(--neural)',
    dim:   'rgba(124,108,243,0.06)',
    glow:  'rgba(124,108,243,0.14)',
  },
  {
    icon: Lightbulb,
    label: 'Brainstorm ideas',
    sub:   'Creative exploration & suggestions',
    prompt: 'Brainstorm creative ideas for: ',
    color: 'var(--amber)',
    dim:   'rgba(245,158,11,0.06)',
    glow:  'rgba(245,158,11,0.14)',
  },
  {
    icon: BookOpen,
    label: 'Explain a concept',
    sub:   'Clear, detailed explanations',
    prompt: 'Explain this concept in depth, with examples: ',
    color: 'var(--eva-primary)',
    dim:   'rgba(0,201,255,0.06)',
    glow:  'rgba(0,201,255,0.14)',
  },
  {
    icon: Zap,
    label: 'Quick answer',
    sub:   'Fast, direct response',
    prompt: '',
    color: 'var(--success)',
    dim:   'rgba(0,212,160,0.06)',
    glow:  'rgba(0,212,160,0.14)',
  },
]

export default function EmptyState({ onSelect }: Props) {
  const username = useAuthStore(s => s.username)
  const config   = useSessionStore(s => s.config)

  const greeting = username ? getGreeting(username) : 'What can I help with?'

  return (
    <div className="empty">
      {/* Logo mark */}
      <div className="empty-mark">
        <img src="/eva-icon.svg" alt="EVA" style={{ width: 62, height: 62 }} />
      </div>

      {/* Greeting */}
      <h1>{greeting}</h1>
      <p>
        {config.model
          ? <>Running on <span style={{ color: 'var(--eva-primary)', fontFamily: 'JetBrains Mono, monospace', fontSize: 13 }}>{config.model}</span></>
          : 'Configure a model in Session Settings ⚙ to get started'}
      </p>

      {/* Suggestion cards */}
      <div className="suggest-grid">
        {SUGGESTIONS.map(({ icon: Icon, label, sub, prompt, color, dim, glow }) => (
          <button
            key={label}
            className="suggest-card"
            onClick={() => onSelect(prompt)}
            onMouseEnter={e => {
              const t = e.currentTarget
              t.style.borderColor = color
              t.style.background  = glow
              t.style.boxShadow   = `0 4px 20px ${color}20`
            }}
            onMouseLeave={e => {
              const t = e.currentTarget
              t.style.borderColor = ''
              t.style.background  = ''
              t.style.boxShadow   = ''
            }}
            style={{ background: dim }}
          >
            <div
              className="sc-icon"
              style={{ background: `${color}18`, border: `1px solid ${color}30`, color }}
            >
              <Icon size={14} />
            </div>
            <div className="sc-body">
              <p className="sc-label">{label}</p>
              <p className="sc-sub">{sub}</p>
            </div>
          </button>
        ))}
      </div>

      <p style={{ color: 'var(--text-muted)', fontSize: 12 }}>
        Click a suggestion to get started, or type your own message below
      </p>
    </div>
  )
}

function getGreeting(username: string): string {
  const hour = new Date().getHours()
  const name = username.charAt(0).toUpperCase() + username.slice(1)
  if (hour < 12) return `Good morning, ${name}`
  if (hour < 17) return `Good afternoon, ${name}`
  return `Good evening, ${name}`
}
