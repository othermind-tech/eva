import { useState } from 'react'
import { Loader2, Check, Copy, X, Link } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { daemonsApi } from '@/api/daemons'
import type { DaemonWebhook } from '@/types/api'
import { formatDate } from '@/lib/utils'

// ── Daemon webhook sub-section ────────────────────────────────────────────────
export default function DaemonWebhookSection({ daemonName, expanded }: { daemonName: string; expanded: boolean }) {
  const qc = useQueryClient()
  const [copied,        setCopied]        = useState(false)
  const [revokeConfirm, setRevokeConfirm] = useState(false)

  const { data: webhook, isError, isLoading: whLoading } = useQuery<DaemonWebhook | undefined>({
    queryKey: ['daemon-webhook', daemonName],
    queryFn:  async () => { try { return await daemonsApi.getWebhook(daemonName) } catch { return undefined } },
    enabled:  expanded,
    retry:    false,
    throwOnError: false,
  })

  const { mutate: createWebhook, isPending: creating } = useMutation({
    mutationFn: () => daemonsApi.createWebhook(daemonName),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['daemon-webhook', daemonName] }),
  })
  const { mutate: deleteWebhook, isPending: revoking } = useMutation({
    mutationFn: () => daemonsApi.deleteWebhook(daemonName),
    onSuccess:  () => { setRevokeConfirm(false); qc.invalidateQueries({ queryKey: ['daemon-webhook', daemonName] }) },
  })

  const handleCopy = () => {
    if (webhook?.webhook_url) {
      navigator.clipboard.writeText(webhook.webhook_url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  if (whLoading) return (
    <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={10} className="animate-spin" /> Loading…
    </div>
  )

  if (isError || !webhook) return (
    <div className="rounded-lg p-3 flex flex-col gap-2" style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}>
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No webhook configured. Create one to trigger via HTTP POST.</p>
      <button
        onClick={() => createWebhook()} disabled={creating}
        className="self-start flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium"
        style={{ background: 'var(--eva-primary-dim)', border: '1px solid var(--border-mid)', color: 'var(--eva-primary)' }}
      >
        {creating ? <Loader2 size={10} className="animate-spin" /> : <Link size={10} />} Create Webhook
      </button>
    </div>
  )

  return (
    <div className="rounded-lg p-3 flex flex-col gap-2" style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}>
      <div className="flex items-center gap-1.5">
        <input readOnly value={webhook.webhook_url} className="flex-1 min-w-0 text-xs px-2 py-1.5 rounded-md outline-none font-mono truncate"
          style={{ background: 'var(--bg-surface1)', border: '1px solid var(--border-dim)', color: 'var(--text-secondary)' }} />
        <button onClick={handleCopy} className="w-7 h-7 flex items-center justify-center rounded transition-colors shrink-0"
          style={{ color: copied ? 'var(--success)' : 'var(--text-muted)' }} title="Copy URL"
          onMouseEnter={e => { if (!copied) e.currentTarget.style.color = 'var(--eva-primary)' }}
          onMouseLeave={e => { if (!copied) e.currentTarget.style.color = 'var(--text-muted)' }}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
      </div>
      <div className="flex items-center justify-between text-xs" style={{ color: 'var(--text-muted)' }}>
        <span>{webhook.trigger_count} trigger{webhook.trigger_count !== 1 ? 's' : ''}</span>
        <span>Last: {webhook.last_triggered_at ? formatDate(webhook.last_triggered_at) : 'Never'}</span>
      </div>
      {!revokeConfirm ? (
        <button onClick={() => setRevokeConfirm(true)} className="self-start flex items-center gap-1 text-xs" style={{ color: 'var(--danger)' }}>
          <X size={9} /> Revoke webhook
        </button>
      ) : (
        <div className="flex items-center gap-2">
          <span className="text-xs flex-1" style={{ color: 'var(--text-muted)' }}>Revoke this webhook?</span>
          <button onClick={() => deleteWebhook()} disabled={revoking} className="flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold" style={{ background: 'var(--danger)', color: '#fff' }}>
            {revoking ? <Loader2 size={9} className="animate-spin" /> : <Check size={9} />} Revoke
          </button>
          <button onClick={() => setRevokeConfirm(false)} disabled={revoking} className="flex items-center gap-1 px-2 py-0.5 rounded text-xs" style={{ background: 'var(--bg-surface3)', color: 'var(--text-muted)' }}>
            <X size={9} /> Cancel
          </button>
        </div>
      )}
    </div>
  )
}
