import { useState, useEffect, useRef } from 'react'
import { X, Globe, Layers, Shield, FolderOpen, BookOpen, ArrowRight, Sparkles } from 'lucide-react'
import { useChatStore } from '@/store/chatStore'
import { BRAND } from '@/lib/brand'
import { useOpenWorkspace } from '@/components/layout/AppShell'

const FEATURES = [
  {
    icon: Globe,
    color: 'var(--eva-primary)',
    dim: 'rgba(0,201,255,0.07)',
    border: 'rgba(0,201,255,0.18)',
    title: 'Provider Agnostic',
    body: 'EVA is not tied to any single AI company. It works with many providers — including Anthropic, OpenAI, Groq, Gemini, Mistral AI, DeepSeek, xAI, Together AI, Ollama, and Ollama Cloud. Switch providers at any time. Your memory, projects, and tools work identically across all of them.',
  },
  {
    icon: Layers,
    color: 'var(--neural)',
    dim: 'rgba(124,108,243,0.07)',
    border: 'rgba(124,108,243,0.18)',
    title: 'Multi-Provider Fallback',
    body: 'Configure an ordered chain of backup providers. If your primary hits a rate limit or outage, EVA switches automatically and continues your task without interruption — no failed requests, no manual retry, no lost work.',
  },
  {
    icon: Shield,
    color: 'var(--success)',
    dim: 'rgba(0,212,160,0.07)',
    border: 'rgba(0,212,160,0.18)',
    title: 'Your Machine. Your Data.',
    body: 'Conversations, memory, projects, documents, and agent configurations live in a local workspace on your machine. Nothing is locked in a cloud account you do not control. Run fully offline with Ollama — zero data leaves your device.',
  },
  {
    icon: FolderOpen,
    color: 'var(--amber)',
    dim: 'rgba(245,158,11,0.07)',
    border: 'rgba(245,158,11,0.18)',
    title: 'Projects & Project View',
    body: 'Projects are persistent workspaces for ongoing work. EVA maintains a living record of each project — decisions made, tasks tracked, knowledge discovered — and carries it into every conversation. Start a new session already in full context, every time.',
  },
]

export default function WelcomePanel() {
  const setViewConversation = useChatStore(s => s.setViewConversation)
  const setViewDocs         = useChatStore(s => s.setViewDocs)
  const setShowWhatsNew     = useChatStore(s => s.setShowWhatsNew)
  const openWorkspace       = useOpenWorkspace()

  const [appVersion, setAppVersion] = useState<string | null>(null)

  useEffect(() => {
    window.electronAPI?.getVersion?.()
      .then((v: string) => setAppVersion(v))
      .catch(() => {})
  }, [])

  // Version pill comet-trace border — measured live rather than hand-calculated
  // (see the comment beside the animated pill JSX for why)
  const wpPillRef = useRef<SVGRectElement>(null)
  const [wpPerimeter, setWpPerimeter] = useState(423)

  useEffect(() => {
    if (wpPillRef.current) setWpPerimeter(wpPillRef.current.getTotalLength())
  }, [appVersion])

  const closeWelcome = () => { setViewConversation(); openWorkspace() }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%', background: 'var(--bg-base)', overflow: 'hidden' }}>

      {/* Sticky top bar — close button */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
        padding: '12px 20px',
        borderBottom: '1px solid var(--border-dim)',
        flexShrink: 0,
      }}>
        <button
          onClick={closeWelcome}
          title="Close"
          style={{
            width: 32, height: 32,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            borderRadius: 8,
            border: '1px solid var(--border-dim)',
            background: 'var(--bg-surface1)',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            transition: 'color 150ms, background 150ms',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)'; (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-surface2)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-muted)';   (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-surface1)' }}
        >
          <X size={16} />
        </button>
      </div>

      {/* Scrollable page content */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
      <div style={{ maxWidth: 780, margin: '0 auto', padding: '48px 40px 80px' }}>

        {/* ── Hero ── */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: 56 }}>
          <img
            src="/eva-main.svg"
            alt={BRAND.logoAlt}
            style={{ width: 260, height: 'auto', objectFit: 'contain', display: 'block' }}
          />
          <div style={{ marginTop: 18, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
            {appVersion ? (
              /* Animated clickable pill — comet traces border once version is known */
              <div
                onClick={() => setShowWhatsNew(appVersion)}
                title="Version Summary"
                style={{
                  position: 'relative',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  width: 200,
                  height: 28,
                }}
              >
                {/* Comet-trace dash values must exactly match this pill's rendered
                    path length or the loop shows a seam/overshoot where it restarts
                    (most visible on the curved end-caps). Browsers don't compute a
                    rounded rect's path length as the plain geometric formula — measure
                    it live via getTotalLength() on the reference rect below instead of
                    hand-calculating, so this can never drift out of sync with the pill's
                    actual size again. wpPerimeter starts at a rough estimate so the
                    animation isn't static for the one frame before the measurement effect runs. */}
                <style>{`
                  @keyframes wp-ch{0%{stroke-dashoffset:0}100%{stroke-dashoffset:-${wpPerimeter}}}
                  @keyframes wp-cb{0%{stroke-dashoffset:3}100%{stroke-dashoffset:-${wpPerimeter - 3}}}
                  @keyframes wp-ct{0%{stroke-dashoffset:20}100%{stroke-dashoffset:-${wpPerimeter - 20}}}
                  @keyframes wp-cg{0%{stroke-dashoffset:28}100%{stroke-dashoffset:-${wpPerimeter - 28}}}
                `}</style>
                <svg width="200" height="28" aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
                  <defs>
                    <filter id="wp-fb" x="-40%" y="-150%" width="180%" height="400%">
                      <feGaussianBlur stdDeviation="1"/>
                    </filter>
                    <filter id="wp-ft" x="-40%" y="-150%" width="180%" height="400%">
                      <feGaussianBlur stdDeviation="2"/>
                    </filter>
                    <filter id="wp-fg" x="-40%" y="-150%" width="180%" height="400%">
                      <feGaussianBlur stdDeviation="3.5"/>
                    </filter>
                  </defs>
                  <rect ref={wpPillRef} x="1.5" y="1.5" width="197" height="25" rx="12.5"
                    fill="none" stroke="#7C6CF3" strokeWidth={7}
                    strokeLinecap="round" strokeOpacity={0.07}
                    strokeDasharray={`68 ${wpPerimeter - 68}`}
                    filter="url(#wp-fg)"
                    style={{ animation: 'wp-cg 2.2s linear infinite' }}
                  />
                  <rect x="1.5" y="1.5" width="197" height="25" rx="12.5"
                    fill="none" stroke="#7C6CF3" strokeWidth={2}
                    strokeLinecap="round" strokeOpacity={0.2}
                    strokeDasharray={`50 ${wpPerimeter - 50}`}
                    filter="url(#wp-ft)"
                    style={{ animation: 'wp-ct 2.2s linear infinite' }}
                  />
                  <rect x="1.5" y="1.5" width="197" height="25" rx="12.5"
                    fill="none" stroke="#7C6CF3" strokeWidth={2}
                    strokeLinecap="round" strokeOpacity={0.6}
                    strokeDasharray={`20 ${wpPerimeter - 20}`}
                    filter="url(#wp-fb)"
                    style={{ animation: 'wp-cb 2.2s linear infinite' }}
                  />
                  <rect x="1.5" y="1.5" width="197" height="25" rx="12.5"
                    fill="none" stroke="#9D95F5" strokeWidth={2.5}
                    strokeLinecap="round" strokeOpacity={1}
                    strokeDasharray={`3 ${wpPerimeter - 3}`}
                    style={{ animation: 'wp-ch 2.2s linear infinite' }}
                  />
                </svg>
                <span style={{
                  position: 'relative',
                  zIndex: 1,
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '4px 12px',
                  borderRadius: 999,
                  background: 'rgba(124,108,243,0.1)',
                  border: '1px solid rgba(124,108,243,0.22)',
                  color: 'var(--neural)',
                  fontSize: 11,
                  fontFamily: 'JetBrains Mono, monospace',
                  letterSpacing: '0.08em',
                  userSelect: 'none',
                  whiteSpace: 'nowrap',
                }}>
                  {BRAND.editionLabel} · V{appVersion}
                </span>
              </div>
            ) : (
              /* Static pill — no animation until Electron resolves the version */
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '4px 12px',
                borderRadius: 999,
                background: 'rgba(124,108,243,0.1)',
                border: '1px solid rgba(124,108,243,0.25)',
                color: 'var(--neural)',
                fontSize: 11,
                fontFamily: 'JetBrains Mono, monospace',
                letterSpacing: '0.08em',
              }}>
                {BRAND.editionLabel}
              </span>
            )}
          </div>
        </div>

        {/* ── What is EVA ── */}
        <section style={{ marginBottom: 52 }}>
          <h2 style={{
            fontSize: 20, fontWeight: 600, color: 'var(--text-primary)',
            marginBottom: 20, paddingBottom: 12,
            borderBottom: '1px solid var(--border-dim)',
          }}>
            What is EVA?
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, color: 'var(--text-secondary)', fontSize: 15, lineHeight: 1.75 }}>
            <p style={{ margin: 0 }}>
              <strong style={{ color: 'var(--text-primary)', fontWeight: 600 }}>EVA</strong>{BRAND.tagline ? <> — <em>{BRAND.tagline}</em> —</> : null} is a provider-agnostic AI agent built for the desktop. Unlike conventional AI tools that lock you to a single model or cloud service, EVA works with virtually any AI provider while keeping your data, conversations, and configurations entirely in your own workspace.
            </p>
            <p style={{ margin: 0 }}>
              The interface is a conversation window — familiar and immediate. Underneath it, an intelligence engine handles intent detection, multi-step planning, tool orchestration, and long-term memory. EVA decides when to act, which tools to call, and when to pause for your approval. You see a streaming response; behind it, an orchestrator is reasoning, acting, and verifying its own work.
            </p>
            <p style={{ margin: 0 }}>
              EVA is designed for everyone — from non-technical users who want a capable assistant with no setup friction, to power users who want fine-grained control over models, tools, agents, hooks, and execution behaviour. Both sit comfortably in the same application.
            </p>
          </div>
        </section>

        {/* ── Why EVA is Different ── */}
        <section style={{ marginBottom: 52 }}>
          <h2 style={{
            fontSize: 20, fontWeight: 600, color: 'var(--text-primary)',
            marginBottom: 20, paddingBottom: 12,
            borderBottom: '1px solid var(--border-dim)',
          }}>
            Why EVA is Different
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            {FEATURES.map(({ icon: Icon, color, dim, border, title, body }) => (
              <div
                key={title}
                style={{
                  background: dim,
                  border: `1px solid ${border}`,
                  borderRadius: 12,
                  padding: '20px 22px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                  <div style={{
                    width: 34, height: 34,
                    borderRadius: 8,
                    background: `${color}18`,
                    border: `1px solid ${color}30`,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color, flexShrink: 0,
                  }}>
                    <Icon size={16} />
                  </div>
                  <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{title}</span>
                </div>
                <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.7, color: 'var(--text-secondary)' }}>{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ── Know Your EVA ── */}
        <section style={{ marginBottom: 52 }}>
          <h2 style={{
            fontSize: 20, fontWeight: 600, color: 'var(--text-primary)',
            marginBottom: 20, paddingBottom: 12,
            borderBottom: '1px solid var(--border-dim)',
          }}>
            Know Your EVA
          </h2>
          <p style={{ margin: '0 0 20px', fontSize: 14.5, color: 'var(--text-secondary)', lineHeight: 1.7 }}>
            Start with the Getting Started guide to configure your first provider and run your first conversation. The full documentation covers every feature in depth.
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <button
              onClick={() => setViewDocs('01-introduction/01-getting-started')}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '18px 20px',
                background: 'rgba(0,201,255,0.06)',
                border: '1px solid rgba(0,201,255,0.2)',
                borderRadius: 12,
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'background 150ms, border-color 150ms',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(0,201,255,0.11)'; (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(0,201,255,0.38)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(0,201,255,0.06)'; (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(0,201,255,0.2)' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ width: 34, height: 34, borderRadius: 8, background: 'rgba(0,201,255,0.12)', border: '1px solid rgba(0,201,255,0.25)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--eva-primary)', flexShrink: 0 }}>
                  <BookOpen size={16} />
                </div>
                <div>
                  <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Getting Started Guide</p>
                  <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>Configure and run your first conversation</p>
                </div>
              </div>
              <ArrowRight size={16} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
            </button>

            <button
              onClick={() => setViewDocs()}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '18px 20px',
                background: 'rgba(124,108,243,0.06)',
                border: '1px solid rgba(124,108,243,0.2)',
                borderRadius: 12,
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'background 150ms, border-color 150ms',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(124,108,243,0.11)'; (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(124,108,243,0.38)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(124,108,243,0.06)'; (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(124,108,243,0.2)' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ width: 34, height: 34, borderRadius: 8, background: 'rgba(124,108,243,0.12)', border: '1px solid rgba(124,108,243,0.25)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--neural)', flexShrink: 0 }}>
                  <BookOpen size={16} />
                </div>
                <div>
                  <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Full Documentation</p>
                  <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>All features, concepts, and configuration</p>
                </div>
              </div>
              <ArrowRight size={16} style={{ color: 'var(--neural)', flexShrink: 0 }} />
            </button>
          </div>
        </section>

        {/* ── Disclaimer ── */}
        <section style={{ marginBottom: 40 }}>
          <div style={{
            padding: '18px 22px',
            background: 'rgba(245,158,11,0.05)',
            border: '1px solid rgba(245,158,11,0.18)',
            borderRadius: 10,
            borderLeft: '3px solid rgba(245,158,11,0.5)',
          }}>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.7, color: 'var(--text-muted)' }}>
              <strong style={{ color: 'var(--amber)', fontWeight: 600 }}>{BRAND.noticeTitle}</strong>
              {BRAND.noticeBody}
            </p>
          </div>
        </section>

        {/* ── Version Summary ── */}
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 32 }}>
          <button
            onClick={() => setShowWhatsNew(appVersion ?? '')}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 7,
              padding: '8px 18px',
              borderRadius: 999,
              border: '1px solid rgba(124,108,243,0.28)',
              background: 'transparent',
              color: 'rgba(124,108,243,0.7)',
              fontSize: 12,
              cursor: 'pointer',
              transition: 'background 150ms, border-color 150ms, color 150ms',
              fontFamily: 'inherit',
            }}
            onMouseEnter={e => {
              const b = e.currentTarget
              b.style.background = 'rgba(124,108,243,0.1)'
              b.style.borderColor = 'rgba(124,108,243,0.5)'
              b.style.color = 'var(--neural)'
            }}
            onMouseLeave={e => {
              const b = e.currentTarget
              b.style.background = 'transparent'
              b.style.borderColor = 'rgba(124,108,243,0.28)'
              b.style.color = 'rgba(124,108,243,0.7)'
            }}
          >
            <Sparkles size={12} />
            Version Summary
          </button>
        </div>

        {/* ── Footer ── */}
        <div style={{ textAlign: 'center', paddingTop: 8 }}>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted)', letterSpacing: '0.04em' }}>
            {BRAND.footerLead} · {BRAND.editionLabel}{appVersion ? ` V${appVersion}` : ''}
          </p>
        </div>

      </div>
      </div>
    </div>
  )
}
