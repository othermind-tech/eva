import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import InlineDisclosure from './InlineDisclosure'
import { useChatStore } from '@/store/chatStore'
import type { ToolCallBlock } from '@/store/chatStore'

interface Props {
  block: ToolCallBlock
}

// Tool results arrive wrapped as {ok, result: {...actual payload...}, metadata}
// (core/tools/dispatcher.py's ToolExecutionResult shape) — describe_tool's own
// "tool" field (the tool being inspected) lives at result.result.tool, not
// result.tool directly. Falls back to a flattened top-level "tool" in case
// that wrapper is ever absent.
function describeToolTargetName(result: unknown): string {
  if (result !== null && typeof result === 'object') {
    const r = result as Record<string, unknown>
    const inner = r.result
    if (inner !== null && typeof inner === 'object' && typeof (inner as Record<string, unknown>).tool === 'string') {
      return (inner as Record<string, unknown>).tool as string
    }
    if (typeof r.tool === 'string') return r.tool
  }
  return ''
}

// The preview pane's screenshot tool (browser_take_screenshot, wire name
// playwright__browser_take_screenshot per Section 88's MCP name-prefixing)
// writes its file under _browser_output/ per playwright.yaml's --output-dir
// and returns an absolute path in the result (--file-paths absolute) rather
// than inlining the image (--image-responses omit). The exact result field
// name isn't pinned down from static inspection of the bundled/minified
// @playwright/mcp package, so this scans shallowly for any string value
// that looks like an absolute image path instead of hardcoding one field —
// safe if the guess is wrong (falls through to the generic renderer below),
// easy to tighten once confirmed against a real screenshot call.
const IMAGE_PATH_RE = /\.(png|jpe?g|webp)$/i
function findScreenshotPath(result: unknown, depth = 0): string | null {
  if (depth > 2 || result === null || typeof result !== 'object') return null
  for (const v of Object.values(result as Record<string, unknown>)) {
    if (typeof v === 'string' && IMAGE_PATH_RE.test(v) && (v.startsWith('/') || /^[A-Za-z]:[\\/]/.test(v))) {
      return v
    }
    if (typeof v === 'object') {
      const found = findScreenshotPath(v, depth + 1)
      if (found) return found
    }
  }
  return null
}

function ScreenshotThumbnail({ path }: { path: string }) {
  const [dataUrl, setDataUrl] = useState<string | null>(null)
  const [failed,  setFailed]  = useState(false)
  const setPreviewOpen = useChatStore(s => s.setPreviewOpen)

  useEffect(() => {
    let cancelled = false
    window.electronAPI?.readImageFile(path)
      .then(url => { if (!cancelled) setDataUrl(url) })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [path])

  if (failed) {
    return <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{path}</span>
  }
  if (!dataUrl) {
    return <Loader2 size={14} className="animate-spin" style={{ color: 'var(--text-dim)' }} />
  }
  return (
    <img
      src={dataUrl}
      alt="Screenshot"
      onClick={() => setPreviewOpen(true)}
      title="Open the preview pane"
      style={{
        maxWidth: 260, maxHeight: 160, borderRadius: 8, display: 'block',
        border: '1px solid var(--border-dim)', cursor: 'pointer',
      }}
    />
  )
}

export default function ToolCallCard({ block }: Props) {
  const { tool_name, result, is_error } = block
  const isDone    = result !== undefined
  const isRunning = !isDone

  const statusText = isRunning ? 'running…' : is_error ? 'failed' : 'done'

  const summary = (
    <span className="flex items-center gap-1.5">
      {isRunning && <Loader2 size={10} className="animate-spin" style={{ color: 'var(--text-dim)' }} />}
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{tool_name}</span>
      <span style={{ color: is_error ? 'var(--danger)' : 'var(--text-dim)' }}>· {statusText}</span>
    </span>
  )

  return (
    <InlineDisclosure summary={summary} disabled={!isDone} tone={is_error ? 'danger' : 'default'}>
      {typeof result === 'string' ? (
        <pre style={{
          margin: 0,
          fontSize: 11.5,
          color: is_error ? 'var(--danger)' : 'var(--text-secondary)',
          fontFamily: "'JetBrains Mono', monospace",
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          lineHeight: 1.6,
          maxHeight: 200,
          overflow: 'auto',
        }}>
          {result}
        </pre>
      ) : result != null && tool_name === 'describe_tool' ? (
        // describe_tool's result carries the target tool's full internal
        // description + JSON parameter schema (core/tools/dispatcher.py's
        // progressive-disclosure mechanism) — that's implementation detail
        // for the model, not something a user should see verbatim. Only
        // the tool name being inspected is meaningful in this UI.
        <div className="flex gap-2 text-xs" style={{ padding: '2px 0' }}>
          <span style={{ color: 'var(--text-dim)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>tool</span>
          <span style={{ color: 'var(--text-secondary)' }}>{describeToolTargetName(result)}</span>
        </div>
      ) : result != null && findScreenshotPath(result) ? (
        <ScreenshotThumbnail path={findScreenshotPath(result)!} />
      ) : result != null ? (
        Object.entries(result as Record<string, unknown>).map(([k, v]) => (
          <div key={k} className="flex gap-2 text-xs" style={{ padding: '2px 0' }}>
            <span style={{ color: 'var(--text-dim)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>{k}</span>
            {v !== null && typeof v === 'object' ? (
              <pre style={{
                margin: 0, color: 'var(--text-secondary)', fontFamily: "'JetBrains Mono', monospace",
                fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {JSON.stringify(v, null, 2)}
              </pre>
            ) : (
              <span style={{ color: 'var(--text-secondary)' }}>{String(v)}</span>
            )}
          </div>
        ))
      ) : null}
    </InlineDisclosure>
  )
}
