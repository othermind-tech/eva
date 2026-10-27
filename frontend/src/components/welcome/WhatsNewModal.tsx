import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { X, Sparkles, ChevronLeft } from 'lucide-react'
import { useChatStore } from '@/store/chatStore'

function extractVersionSection(markdown: string, version: string): string {
  const lines    = markdown.split('\n')
  const startIdx = lines.findIndex(l => l.startsWith(`## v${version}`))
  if (startIdx === -1) return markdown
  const endIdx   = lines.findIndex((l, i) => i > startIdx && l.startsWith('## '))
  return lines.slice(startIdx, endIdx === -1 ? undefined : endIdx).join('\n')
}

export default function WhatsNewModal() {
  const showWhatsNew    = useChatStore(s => s.showWhatsNew)
  const whatsNewVersion = useChatStore(s => s.whatsNewVersion)
  const dismissWhatsNew = useChatStore(s => s.dismissWhatsNew)

  const [sectionContent, setSectionContent] = useState<string | null>(null)
  const [fullContent,    setFullContent]    = useState<string | null>(null)
  const [showFull,       setShowFull]       = useState(false)
  const [unavailable,    setUnavailable]    = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!showWhatsNew) { setShowFull(false); return }
    setSectionContent(null)
    setFullContent(null)
    setUnavailable(false)
    fetch('/docs/CHANGELOG.md')
      .then(r => {
        if (!r.ok) return null
        const ct = r.headers.get('content-type') || ''
        if (ct.includes('text/html')) return null
        return r.text()
      })
      .then(text => {
        if (!text) { setUnavailable(true); return }
        setFullContent(text)
        setSectionContent(whatsNewVersion ? extractVersionSection(text, whatsNewVersion) : text)
      })
      .catch(() => setUnavailable(true))
  }, [showWhatsNew, whatsNewVersion])

  // Reset scroll to top when toggling between section and full view
  const handleShowFull = () => {
    setShowFull(true)
    scrollRef.current?.scrollTo({ top: 0 })
  }

  const handleBackToVersion = () => {
    setShowFull(false)
    scrollRef.current?.scrollTo({ top: 0 })
  }

  if (!showWhatsNew) return null

  const displayContent = showFull ? fullContent : sectionContent
  const headerTitle = showFull
    ? 'Full Changelog'
    : `What's New in EVA${whatsNewVersion ? ` v${whatsNewVersion}` : ''}`

  return (
    <div
      onClick={dismissWhatsNew}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.55)',
        zIndex: 200,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: showFull ? 620 : 540,
          maxHeight: showFull ? '80vh' : '70vh',
          background: 'var(--bg-surface2)',
          border: '1px solid var(--border-mid)',
          borderRadius: 14,
          boxShadow: '0 24px 64px rgba(0,0,0,0.6)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          transition: 'width 200ms ease, max-height 200ms ease',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '18px 20px 14px',
          borderBottom: '1px solid var(--border-dim)',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              width: 30, height: 30, borderRadius: 8,
              background: 'rgba(124,108,243,0.15)',
              border: '1px solid rgba(124,108,243,0.3)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--neural)', flexShrink: 0,
            }}>
              <Sparkles size={15} />
            </div>
            <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
              {headerTitle}
            </p>
          </div>
          <button
            onClick={dismissWhatsNew}
            style={{
              width: 28, height: 28,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              borderRadius: 6,
              border: '1px solid var(--border-dim)',
              background: 'var(--bg-surface1)',
              color: 'var(--text-muted)',
              cursor: 'pointer',
            }}
          >
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>
          {displayContent ? (
            <div
              style={{ fontSize: 13.5, lineHeight: 1.8, color: 'var(--text-secondary)' }}
              className="eva-changelog"
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayContent}</ReactMarkdown>
            </div>
          ) : (
            <p style={{ color: 'var(--text-muted)', fontSize: 13, margin: 0 }}>
              {unavailable ? 'Release notes are not available for this version.' : 'Loading release notes…'}
            </p>
          )}
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 20px',
          borderTop: '1px solid var(--border-dim)',
          flexShrink: 0,
        }}>
          <div>
            {showFull ? (
              <button
                onClick={handleBackToVersion}
                style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '7px 12px', borderRadius: 7,
                  border: '1px solid var(--border-mid)',
                  background: 'transparent',
                  color: 'var(--text-secondary)',
                  fontSize: 13, cursor: 'pointer',
                }}
              >
                <ChevronLeft size={14} />
                Back to v{whatsNewVersion}
              </button>
            ) : unavailable ? null : (
              <button
                onClick={handleShowFull}
                style={{
                  padding: '7px 14px', borderRadius: 7,
                  border: '1px solid var(--border-mid)',
                  background: 'transparent',
                  color: 'var(--text-secondary)',
                  fontSize: 13, cursor: 'pointer',
                }}
              >
                See full changelog
              </button>
            )}
          </div>

          <button
            onClick={dismissWhatsNew}
            style={{
              padding: '7px 18px', borderRadius: 7,
              border: 'none',
              background: 'var(--neural)',
              color: '#fff',
              fontSize: 13, fontWeight: 600, cursor: 'pointer',
            }}
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  )
}
