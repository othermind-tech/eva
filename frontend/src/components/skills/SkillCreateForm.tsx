import { useState } from 'react'
import { Wand2, X, AlertTriangle, Loader2, Check } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { skillsApi } from '@/api/skills'

// ── Skill create form (inline in list) ───────────────────────────────────────
export default function SkillCreateForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const [name,    setName]    = useState('')
  const [content, setContent] = useState(
    '---\nname: my_skill\ndescription: What this skill does\ntrigger: /my_skill\n---\n\nYou are a specialist. When this skill is triggered...\n'
  )
  const [error, setError] = useState<string | null>(null)

  const { mutate: create, isPending } = useMutation({
    mutationFn: () => skillsApi.create(name.trim(), content),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['skills'] }); onDone() },
    onError:   () => setError('Failed to create skill. Check the name and content.'),
  })

  const canCreate = name.trim().length > 0 && content.trim().length > 0

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ border: '1px solid rgba(124,108,243,0.3)', background: 'rgba(124,108,243,0.04)', marginBottom: 16 }}
    >
      <div
        className="flex items-center justify-between px-3 py-2"
        style={{ borderBottom: '1px solid rgba(124,108,243,0.15)' }}
      >
        <div className="flex items-center gap-2">
          <Wand2 size={12} style={{ color: 'var(--neural)' }} />
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--neural)' }}>New Skill</span>
        </div>
        <button
          className="icon-btn"
          style={{ width: 20, height: 20 }}
          onClick={onDone}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
        >
          <X size={11} />
        </button>
      </div>
      <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="form-row" style={{ marginBottom: 0 }}>
          <label>Skill Name</label>
          <input
            className="form-input"
            value={name}
            onChange={e => setName(e.target.value.replace(/\s+/g, '_').toLowerCase())}
            placeholder="weekly_status"
          />
          <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 4 }}>
            Trigger: <span style={{ color: 'var(--neural)', fontFamily: 'JetBrains Mono, monospace' }}>/{name || 'skill_name'}</span>
          </p>
        </div>
        <div className="form-row" style={{ marginBottom: 0 }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 6 }}>
            <label style={{ margin: 0 }}>SKILL.md Content</label>
            <span style={{ fontSize: 10, color: content.length > 9000 ? 'var(--danger)' : 'var(--text-muted)' }}>
              {content.length.toLocaleString()} / 10,000
            </span>
          </div>
          <textarea
            className="form-textarea mono"
            value={content}
            onChange={e => setContent(e.target.value.slice(0, 10000))}
            rows={8}
          />
        </div>
        {error && (
          <div className="flex items-center gap-2" style={{ color: 'var(--danger)', fontSize: 12 }}>
            <AlertTriangle size={11} /> {error}
          </div>
        )}
        <div className="flex gap-2">
          <button className="btn ghost" onClick={onDone}>Cancel</button>
          <button
            className="btn primary"
            disabled={!canCreate || isPending}
            onClick={() => create()}
          >
            {isPending ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
            {isPending ? 'Creating…' : 'Create Skill'}
          </button>
        </div>
      </div>
    </div>
  )
}
