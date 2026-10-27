// Reusable system-prompt textarea with ✨ Enhance button (B-055)
import { useState } from 'react'
import { Loader2, Sparkles, RotateCcw } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { promptsApi } from '@/api/prompts'
import { useUiStore } from '@/store/uiStore'
import type { PromptEnhanceRequest } from '@/types/api'

interface Props {
  value:    string
  onChange: (v: string) => void
  context:  PromptEnhanceRequest['context']
  rows?:    number
  placeholder?: string
  showEnhance?: boolean
  readOnly?: boolean
}

export default function SystemPromptField({
  value, onChange, context, rows = 5,
  placeholder = 'Describe the assistant persona…',
  showEnhance = true,
  readOnly = false,
}: Props) {
  const [original, setOriginal]   = useState<string | null>(null)
  const [showOrig, setShowOrig]   = useState(false)
  const [enhanceError, setEnhanceError] = useState<string | null>(null)
  const openProviderRequired = useUiStore(s => s.openProviderRequired)

  const { mutate: enhance, isPending } = useMutation({
    mutationFn: () => promptsApi.enhance({ raw_prompt: value, context }),
    onSuccess: (data) => {
      setEnhanceError(null)
      setOriginal(value)
      setShowOrig(false)
      onChange(data.enhanced_prompt)
    },
    onError: (err) => {
      const apiErr = err as { status?: number; detail?: string | { error_code?: string } }
      const code = typeof apiErr.detail === 'object' ? apiErr.detail?.error_code : undefined
      if (apiErr.status === 424 && code === 'provider_not_configured') {
        openProviderRequired()
      } else if (apiErr.status === 424 && code === 'api_key_not_configured') {
        setEnhanceError('API key not configured for this provider — add it in Provider settings.')
      } else {
        setEnhanceError('Enhance failed — try again.')
      }
    },
  })

  const restore = () => {
    if (original !== null) { onChange(original); setOriginal(null); setShowOrig(false) }
  }

  const charCount = value.length
  const warn      = charCount > 1800

  return (
    <div className="flex flex-col gap-1.5">
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={isPending}
        readOnly={readOnly}
        placeholder={placeholder}
        rows={rows}
        className="w-full resize-none text-xs rounded-lg px-3 py-2.5 outline-none leading-relaxed"
        style={{
          background: 'var(--bg-surface2)',
          border: '1px solid var(--border-dim)',
          color: 'var(--text-primary)',
          fontFamily: 'JetBrains Mono, monospace',
          opacity: readOnly ? 0.8 : isPending ? 0.6 : 1,
          cursor: readOnly ? 'default' : 'text',
          resize: readOnly ? 'none' : undefined,
        }}
        onFocus={e => { if (!readOnly) e.target.style.borderColor = 'var(--border-bright)' }}
        onBlur={e  => (e.target.style.borderColor = 'var(--border-dim)')}
      />

      {/* Footer row — hidden in read-only mode */}
      {!readOnly && <div className="flex items-center justify-between">
        <span
          className="text-xs"
          style={{ color: warn ? 'var(--amber)' : 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}
        >
          {charCount} / 2000
        </span>

        <div className="flex items-center gap-2">
          {/* Restore original — shown after enhance */}
          {original !== null && (
            <button
              type="button"
              onClick={() => setShowOrig(v => !v)}
              className="text-xs transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-secondary)')}
              onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
            >
              {showOrig ? 'Hide original' : 'Show original'}
            </button>
          )}
          {original !== null && (
            <button
              type="button"
              onClick={restore}
              className="flex items-center gap-1 text-xs transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => (e.currentTarget.style.color = 'var(--amber)')}
              onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
            >
              <RotateCcw size={10} /> Restore
            </button>
          )}

          {/* Enhance button */}
          {showEnhance && (
            <button
              type="button"
              disabled={isPending || !value.trim()}
              onClick={() => { setEnhanceError(null); enhance() }}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all"
              style={{
                background: isPending || !value.trim()
                  ? 'var(--bg-surface3)'
                  : 'rgba(0,201,255,0.12)',
                border: '1px solid',
                borderColor: isPending || !value.trim()
                  ? 'var(--border-dim)'
                  : 'rgba(0,201,255,0.3)',
                color: isPending || !value.trim()
                  ? 'var(--text-muted)'
                  : 'var(--eva-primary)',
                cursor: isPending || !value.trim() ? 'not-allowed' : 'pointer',
              }}
            >
              {isPending
                ? <><Loader2 size={10} className="animate-spin" /> Enhancing…</>
                : <><Sparkles size={10} /> Enhance</>}
            </button>
          )}
        </div>
      </div>}

      {enhanceError && (
        <p className="text-xs" style={{ color: 'var(--amber)' }}>
          {enhanceError}
        </p>
      )}

      {/* Original prompt preview */}
      {showOrig && original !== null && (
        <div
          className="text-xs rounded-lg px-3 py-2.5 leading-relaxed whitespace-pre-wrap"
          style={{
            background: 'var(--bg-surface3)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-muted)',
            fontFamily: 'JetBrains Mono, monospace',
          }}
        >
          {original}
        </div>
      )}
    </div>
  )
}
