import { useState, useEffect, useRef, useCallback, type ReactNode, type RefObject } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import {
  ChevronRight, ChevronDown, ArrowLeft, ArrowRight,
  Copy, Check, Library,
} from 'lucide-react'
import { useChatStore } from '@/store/chatStore'
import { useDocsIndex, type DocSection } from '@/lib/docsIndex'

// ── helpers ───────────────────────────────────────────────────────────────────

const slugify = (text: string) =>
  String(text)
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
    .replace(/--+/g, '-')
    .replace(/^-|-$/g, '')

interface TOCItem {
  id:    string
  text:  string
  level: 2 | 3
}

function extractTOC(markdown: string): TOCItem[] {
  const items: TOCItem[] = []
  for (const line of markdown.split('\n')) {
    const m2 = line.match(/^## (.+)/)
    const m3 = line.match(/^### (.+)/)
    if (m2) items.push({ id: slugify(m2[1]), text: m2[1], level: 2 })
    else if (m3) items.push({ id: slugify(m3[1]), text: m3[1], level: 3 })
  }
  return items
}

// ── DocsSidebar ───────────────────────────────────────────────────────────────

function DocsSidebar({ sections, isLoading, currentId, onNavigate }: {
  sections:  DocSection[]
  isLoading: boolean
  currentId: string
  onNavigate: (id: string) => void
}) {
  // Tracks collapsed sections rather than open ones: the index now arrives
  // asynchronously, and "everything open" (today's default) then needs no
  // initial-state derivation from data that doesn't exist yet at mount.
  const [closedSections, setClosedSections] = useState<Set<string>>(() => new Set())

  const toggleSection = (id: string) =>
    setClosedSections(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })

  return (
    <aside style={{
      width: 252,
      flexShrink: 0,
      background: 'var(--bg-surface1)',
      borderRight: '1px solid var(--border-dim)',
      overflowY: 'auto',
      display: 'flex',
      flexDirection: 'column',
    }}>
      {/* Brand header */}
      <div style={{
        padding: '18px 16px 14px',
        borderBottom: '1px solid var(--border-dim)',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        flexShrink: 0,
      }}>
        <Library size={14} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.01em' }}>
          EVA Documentation
        </span>
      </div>

      {/* Nav tree */}
      <nav style={{ padding: '8px 0 24px', flex: 1 }}>
        {isLoading && (
          <div aria-hidden="true" style={{ padding: '8px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[70, 90, 60, 80, 65].map((w, i) => (
              <div key={i} style={{ height: 10, width: `${w}%`, borderRadius: 4, background: 'var(--bg-surface2)' }} />
            ))}
          </div>
        )}
        {sections.map(section => {
          const isOpen = !closedSections.has(section.id)
          return (
            <div key={section.id}>
              <button
                onClick={() => toggleSection(section.id)}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  width: '100%', padding: '7px 16px',
                  background: 'none', border: 'none', cursor: 'pointer',
                }}
              >
                <span style={{
                  fontSize: 10.5, fontWeight: 700, letterSpacing: '0.09em',
                  textTransform: 'uppercase', color: 'var(--text-muted)',
                }}>
                  {section.title}
                </span>
                {isOpen
                  ? <ChevronDown size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                  : <ChevronRight size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                }
              </button>

              {isOpen && (
                <ul style={{ margin: '2px 0 6px', padding: 0, listStyle: 'none' }}>
                  {section.pages.map(page => {
                    const isActive = page.id === currentId
                    return (
                      <li key={page.id}>
                        <button
                          onClick={() => onNavigate(page.id)}
                          style={{
                            display: 'block', width: '100%', textAlign: 'left',
                            padding: '5px 14px 5px 24px',
                            background: isActive ? 'rgba(0,201,255,0.07)' : 'transparent',
                            border: 'none',
                            borderLeft: isActive
                              ? '2px solid var(--eva-primary)'
                              : '2px solid transparent',
                            cursor: 'pointer',
                            fontSize: 13,
                            color: isActive ? 'var(--eva-primary)' : 'var(--text-secondary)',
                            transition: 'color 80ms, background 80ms',
                          }}
                          onMouseEnter={e => {
                            if (!isActive) {
                              (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)'
                              ;(e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.03)'
                            }
                          }}
                          onMouseLeave={e => {
                            if (!isActive) {
                              (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)'
                              ;(e.currentTarget as HTMLElement).style.background = 'transparent'
                            }
                          }}
                        >
                          {page.title}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          )
        })}
      </nav>
    </aside>
  )
}

// ── DocsTOC ───────────────────────────────────────────────────────────────────

// Takes the ref object, not `ref.current`: reading a ref during render is unreliable (null until after mount)
// and flagged by react-hooks/refs; the effect reads it once the element exists.
function DocsTOC({ items, scrollContainerRef }: { items: TOCItem[]; scrollContainerRef: RefObject<HTMLElement | null> }) {
  const [activeId, setActiveId] = useState<string>('')

  useEffect(() => {
    const scrollContainer = scrollContainerRef.current
    if (!items.length || !scrollContainer) return
    const observer = new IntersectionObserver(
      entries => {
        const visible = entries.filter(e => e.isIntersecting)
        if (visible.length > 0) setActiveId(visible[0].target.id)
      },
      { root: scrollContainer, rootMargin: '-80px 0px -55% 0px', threshold: 0 }
    )
    items.forEach(({ id }) => {
      const el = document.getElementById(id)
      if (el) observer.observe(el)
    })
    return () => observer.disconnect()
  }, [items, scrollContainerRef])

  if (!items.length) return null

  return (
    <aside style={{
      width: 196,
      flexShrink: 0,
      padding: '36px 16px 36px 8px',
      alignSelf: 'flex-start',
      position: 'sticky',
      top: 0,
    }}>
      <p style={{
        fontSize: 10.5, fontWeight: 700, letterSpacing: '0.09em',
        textTransform: 'uppercase', color: 'var(--text-muted)',
        margin: '0 0 10px',
      }}>
        On this page
      </p>
      {items.map(item => (
        <a
          key={item.id}
          href={`#${item.id}`}
          onClick={e => {
            e.preventDefault()
            document.getElementById(item.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
            setActiveId(item.id)
          }}
          style={{
            display: 'block',
            padding: item.level === 3 ? '3px 0 3px 10px' : '3px 0',
            fontSize: 12,
            lineHeight: 1.55,
            color: activeId === item.id ? 'var(--eva-primary)' : 'var(--text-muted)',
            borderLeft: item.level === 3 ? '1px solid var(--border-dim)' : 'none',
            textDecoration: 'none',
            transition: 'color 80ms',
          }}
          onMouseEnter={e => {
            if (activeId !== item.id)
              (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)'
          }}
          onMouseLeave={e => {
            if (activeId !== item.id)
              (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)'
          }}
        >
          {item.text}
        </a>
      ))}
    </aside>
  )
}

// ── Copy button ───────────────────────────────────────────────────────────────

function CopyBtn({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(code).catch(() => {})
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }}
      style={{
        position: 'absolute', top: 10, right: 10,
        display: 'flex', alignItems: 'center', gap: 4,
        padding: '3px 8px', borderRadius: 5,
        border: '1px solid var(--border-dim)',
        background: 'var(--bg-surface3)',
        cursor: 'pointer',
        color: 'var(--text-muted)',
        fontSize: 11,
        transition: 'color 80ms',
      }}
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}

// ── Markdown custom renderers ─────────────────────────────────────────────────

const H = (level: 1 | 2 | 3 | 4) => ({ children }: { children?: ReactNode }) => {
  const id = slugify(String(children))
  const styles: Record<number, React.CSSProperties> = {
    1: { fontSize: 28, fontWeight: 800, color: 'var(--text-primary)', margin: '0 0 24px', lineHeight: 1.2 },
    2: { fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', margin: '40px 0 12px', paddingBottom: 8, borderBottom: '1px solid var(--border-dim)', lineHeight: 1.3 },
    3: { fontSize: 16, fontWeight: 600, color: 'var(--text-primary)', margin: '28px 0 8px', lineHeight: 1.4 },
    4: { fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)', margin: '20px 0 6px' },
  }
  const Tag = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4'
  return <Tag id={id} style={styles[level]}>{children}</Tag>
}

const mdComponents = {
  h1: H(1),
  h2: H(2),
  h3: H(3),
  h4: H(4),
  p: ({ children }: { children?: ReactNode }) => (
    <p style={{ fontSize: 14.5, color: 'var(--text-secondary)', margin: '0 0 16px', lineHeight: 1.8 }}>
      {children}
    </p>
  ),
  ul: ({ children }: { children?: ReactNode }) => (
    <ul style={{ margin: '0 0 16px', paddingLeft: 22, color: 'var(--text-secondary)', fontSize: 14.5, lineHeight: 1.8 }}>
      {children}
    </ul>
  ),
  ol: ({ children }: { children?: ReactNode }) => (
    <ol style={{ margin: '0 0 16px', paddingLeft: 22, color: 'var(--text-secondary)', fontSize: 14.5, lineHeight: 1.8 }}>
      {children}
    </ol>
  ),
  li: ({ children }: { children?: ReactNode }) => (
    <li style={{ marginBottom: 6 }}>{children}</li>
  ),
  blockquote: ({ children }: { children?: ReactNode }) => (
    <blockquote style={{
      margin: '20px 0',
      padding: '12px 18px',
      borderLeft: '3px solid var(--eva-primary)',
      background: 'rgba(0,201,255,0.05)',
      borderRadius: '0 8px 8px 0',
      color: 'var(--text-secondary)',
      fontSize: 14,
      lineHeight: 1.75,
    }}>
      {children}
    </blockquote>
  ),
  code: ({ className, children }: { className?: string; children?: ReactNode }) => {
    const isBlock = !!className
    const raw = String(children ?? '').replace(/\n$/, '')
    if (isBlock) {
      return (
        <div style={{ position: 'relative', margin: '16px 0' }}>
          <pre style={{
            background: 'var(--bg-surface2)',
            border: '1px solid var(--border-dim)',
            borderRadius: 8,
            padding: '14px 14px 14px',
            paddingRight: 72,
            overflowX: 'auto',
            fontSize: 13,
            lineHeight: 1.65,
            fontFamily: "'JetBrains Mono', monospace",
            margin: 0,
          }}>
            <code className={className} style={{ fontFamily: 'inherit' }}>{children}</code>
          </pre>
          <CopyBtn code={raw} />
        </div>
      )
    }
    return (
      <code style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12.5,
        background: 'var(--bg-surface3)',
        color: 'var(--eva-primary)',
        padding: '1px 5px',
        borderRadius: 4,
        border: '1px solid rgba(0,201,255,0.12)',
      }}>
        {children}
      </code>
    )
  },
  table: ({ children }: { children?: ReactNode }) => (
    <div style={{ overflowX: 'auto', margin: '16px 0' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13.5 }}>
        {children}
      </table>
    </div>
  ),
  thead: ({ children }: { children?: ReactNode }) => (
    <thead style={{ borderBottom: '2px solid var(--border-mid)', background: 'rgba(255,255,255,0.02)' }}>
      {children}
    </thead>
  ),
  th: ({ children }: { children?: ReactNode }) => (
    <th style={{ padding: '9px 14px', textAlign: 'left', color: 'var(--text-primary)', fontWeight: 600, fontSize: 12.5, letterSpacing: '0.01em' }}>
      {children}
    </th>
  ),
  td: ({ children }: { children?: ReactNode }) => (
    <td style={{ padding: '9px 14px', color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-dim)', verticalAlign: 'top' }}>
      {children}
    </td>
  ),
  tr: ({ children }: { children?: ReactNode }) => (
    <tr
      style={{ transition: 'background 80ms' }}
      onMouseEnter={e => { (e.currentTarget as HTMLTableRowElement).style.background = 'rgba(255,255,255,0.025)' }}
      onMouseLeave={e => { (e.currentTarget as HTMLTableRowElement).style.background = 'transparent' }}
    >
      {children}
    </tr>
  ),
  a: ({ href, children }: { href?: string; children?: ReactNode }) => (
    <a
      href={href}
      target={href?.startsWith('http') ? '_blank' : undefined}
      rel={href?.startsWith('http') ? 'noopener noreferrer' : undefined}
      style={{ color: 'var(--eva-primary)', textDecoration: 'none' }}
      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.textDecoration = 'underline' }}
      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.textDecoration = 'none' }}
    >
      {children}
    </a>
  ),
  hr: () => (
    <hr style={{ border: 'none', borderTop: '1px solid var(--border-dim)', margin: '32px 0' }} />
  ),
  strong: ({ children }: { children?: ReactNode }) => (
    <strong style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{children}</strong>
  ),
  em: ({ children }: { children?: ReactNode }) => (
    <em style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>{children}</em>
  ),
}

// ── DocsPanel ─────────────────────────────────────────────────────────────────

export default function DocsPanel() {
  const docsPath    = useChatStore(s => s.docsPath)
  const setViewDocs = useChatStore(s => s.setViewDocs)

  const [markdown, setMarkdown] = useState('')
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)

  const { sections, allPages, isLoading: indexLoading, isError: indexError } = useDocsIndex()

  // Undefined until the manifest arrives (or if it never does) — everything
  // below that touches the current page has to tolerate that. An unknown
  // docsPath still falls back to the first page, as it always did.
  const currentPage = allPages.find(p => p.id === docsPath) ?? allPages[0]
  const currentIdx  = currentPage ? allPages.findIndex(p => p.id === currentPage.id) : -1
  const prevPage    = currentIdx > 0 ? allPages[currentIdx - 1] : null
  const nextPage    = currentIdx >= 0 && currentIdx < allPages.length - 1 ? allPages[currentIdx + 1] : null
  const currentSection = currentPage ? sections.find(s => s.pages.some(p => p.id === currentPage.id)) : undefined

  const toc = extractTOC(markdown)

  const navigate = useCallback((id: string) => {
    setViewDocs(id)
    scrollRef.current?.scrollTo({ top: 0, behavior: 'instant' })
  }, [setViewDocs])

  useEffect(() => {
    if (!currentPage) return
    setLoading(true)
    setError(null)
    setMarkdown('')
    fetch(`/docs/${currentPage.id}.md`)
      .then(r => {
        if (!r.ok) throw new Error(`${r.status}`)
        return r.text()
      })
      .then(text => setMarkdown(text))
      .catch(() => setError('Could not load this page.'))
      .finally(() => setLoading(false))
  }, [currentPage?.id])

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: 'var(--bg-base)' }}>

      <DocsSidebar sections={sections} isLoading={indexLoading} currentId={currentPage?.id ?? ''} onNavigate={navigate} />

      {/* Scrollable content column */}
      <div
        ref={scrollRef}
        style={{ flex: 1, overflowY: 'auto', display: 'flex' }}
      >
        {/* Centre article + right TOC */}
        <div style={{ display: 'flex', flex: 1, justifyContent: 'center', minWidth: 0 }}>

          <article style={{ flex: 1, maxWidth: 780, padding: '36px 40px 80px', minWidth: 0 }}>

            {/* Breadcrumb */}
            <nav style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 28, fontSize: 12, color: 'var(--text-muted)' }}>
              <span>Docs</span>
              <ChevronRight size={10} />
              <span>{currentSection?.title}</span>
              <ChevronRight size={10} />
              <span style={{ color: 'var(--text-secondary)' }}>{currentPage?.title}</span>
            </nav>

            {/* Loading (manifest first, then the page itself) */}
            {(indexLoading || loading) && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-muted)', marginTop: 32 }}>
                <div style={{
                  width: 16, height: 16, borderRadius: '50%',
                  border: '2px solid var(--border-mid)',
                  borderTopColor: 'var(--eva-primary)',
                  animation: 'spin 0.8s linear infinite',
                }} />
                Loading…
              </div>
            )}

            {/* Error */}
            {error && (
              <p style={{ color: 'var(--danger)', fontSize: 14 }}>{error}</p>
            )}
            {!indexLoading && (indexError || !currentPage) && (
              <p style={{ color: 'var(--danger)', fontSize: 14 }}>Could not load the documentation index.</p>
            )}

            {/* Markdown content */}
            {!indexLoading && !loading && !error && markdown && (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={mdComponents as Record<string, React.ComponentType<Record<string, unknown>>>}
              >
                {markdown}
              </ReactMarkdown>
            )}

            {/* Prev / Next footer */}
            {!indexLoading && !loading && !error && currentPage && (
              <div style={{ display: 'flex', gap: 12, marginTop: 52, paddingTop: 24, borderTop: '1px solid var(--border-dim)' }}>
                {prevPage ? (
                  <button
                    onClick={() => navigate(prevPage.id)}
                    style={{
                      flex: 1, padding: '14px 18px', borderRadius: 10,
                      border: '1px solid var(--border-dim)',
                      background: 'var(--bg-surface2)',
                      cursor: 'pointer', textAlign: 'left',
                      transition: 'border-color 80ms',
                    }}
                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-mid)' }}
                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-dim)' }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                      <ArrowLeft size={11} /> Previous
                    </div>
                    <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>
                      {prevPage.title}
                    </div>
                  </button>
                ) : <div style={{ flex: 1 }} />}

                {nextPage && (
                  <button
                    onClick={() => navigate(nextPage.id)}
                    style={{
                      flex: 1, padding: '14px 18px', borderRadius: 10,
                      border: '1px solid var(--border-dim)',
                      background: 'var(--bg-surface2)',
                      cursor: 'pointer', textAlign: 'right',
                      transition: 'border-color 80ms',
                    }}
                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-mid)' }}
                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-dim)' }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4, fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                      Next <ArrowRight size={11} />
                    </div>
                    <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>
                      {nextPage.title}
                    </div>
                  </button>
                )}
              </div>
            )}
          </article>

          {/* Right on-page TOC */}
          <DocsTOC items={toc} scrollContainerRef={scrollRef} />
        </div>
      </div>

      {/* Spinner keyframe — injected once */}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
