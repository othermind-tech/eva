import InlineDisclosure from './InlineDisclosure'
import type { ChatMessageUI } from '@/store/chatStore'

interface Props { steps: ChatMessageUI[] }

export default function ReActStepCard({ steps }: Props) {
  const toolCalls   = steps.filter(s => s.msgType === 'tool_call')
  const toolResults = steps.filter(s => s.msgType === 'tool_result')

  // Match results back to calls by toolCallId when available
  const resultFor = (tc: ChatMessageUI) =>
    toolResults.find(r => r.toolCallId && r.toolCallId === tc.toolCallId) ??
    toolResults[toolCalls.indexOf(tc)]  // fallback: positional match

  // The backend only stamps tool_name on the tool_result row, not the
  // tool_call row itself (see worker.py's _save_tool_step) — the call's own
  // name always comes through null on reload, so fall back to its matched result.
  const nameFor = (tc: ChatMessageUI) => tc.toolName ?? resultFor(tc)?.toolName

  const allDone  = toolResults.length >= toolCalls.length
  const anyError = toolResults.some(r => r.isError)

  // Named calls only — an unnamed fallback ('tool') would otherwise repeat
  // and read as "tool, tool, tool…" when a name is genuinely missing.
  const namedTools = toolCalls.map(nameFor).filter((n): n is string => !!n)
  const toolLabel = namedTools.length > 0
    ? namedTools.slice(0, 3).join(', ') + (namedTools.length > 3 ? `, +${namedTools.length - 3} more` : '')
    : `${toolCalls.length} tool${toolCalls.length !== 1 ? 's' : ''}`

  const summary = (
    <span style={{ color: 'var(--text-muted)' }}>
      Ran {toolLabel}
      <span style={{ color: anyError ? 'var(--danger)' : 'var(--text-dim)' }}>
        {' · '}{!allDone ? 'running…' : anyError ? 'failed' : 'done'}
      </span>
    </span>
  )

  return (
    <InlineDisclosure summary={summary} tone={anyError ? 'danger' : 'default'}>
      <div className="flex flex-col gap-2.5">
        {toolCalls.map((tc, idx) => {
          const result = resultFor(tc)
          return (
            <div key={tc.id} className="flex flex-col gap-1">
              <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                {nameFor(tc) ?? 'unknown'}
                {toolCalls.length > 1 && <span style={{ color: 'var(--text-dim)' }}>#{idx + 1}</span>}
              </div>

              {tc.toolInput && Object.keys(tc.toolInput).length > 0 && (
                <pre
                  className="text-xs rounded p-2 overflow-auto"
                  style={{
                    background:  'rgba(0,0,0,0.15)',
                    color:       'var(--text-dim)',
                    fontFamily:  'var(--font-mono)',
                    maxHeight:   120,
                    lineHeight:  1.5,
                  }}
                >
                  {JSON.stringify(tc.toolInput, null, 2)}
                </pre>
              )}

              {result?.blocks[0]?.kind === 'text' && result.blocks[0].content && (
                <pre
                  className="text-xs rounded p-2 overflow-auto"
                  style={{
                    background: 'rgba(0,0,0,0.15)',
                    color:      result.isError ? 'var(--danger)' : 'var(--text-dim)',
                    fontFamily: 'var(--font-mono)',
                    maxHeight:  180,
                    lineHeight: 1.5,
                  }}
                >
                  {result.blocks[0].content}
                </pre>
              )}
            </div>
          )
        })}
      </div>
    </InlineDisclosure>
  )
}
