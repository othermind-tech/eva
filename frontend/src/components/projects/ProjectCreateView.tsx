import { useState } from 'react'
import { ChevronRight, FolderOpen, Loader2, Check, AlertTriangle, X, FolderSymlink } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi } from '@/api/projects'

// ── Create Project (full page) ──────────────────────────────────────────────
// Restored 2026-07 — the original compact-card version was deleted in a dead-code cleanup that
// correctly migrated Memory/RAG/Subagents/Tools into this file's full-view
// panels, but incorrectly assumed Projects had been migrated too.
// Rebuilt 2026-07 as a full-page view matching the Daemon/Agent create
// pattern (detail / detail-header / dh-title / form-section) rather than
// restoring the old compact card, for visual consistency with the rest of
// the app's create flows.
export default function ProjectCreateView({ onBack, onCreated }: { onBack: () => void; onCreated: (id: string) => void }) {
  const qc = useQueryClient()
  const [name,          setName]          = useState('')
  const [synopsis,      setSynopsis]      = useState('')
  const [desc,          setDesc]          = useState('')
  const [useExternal,   setUseExternal]   = useState(false)
  const [workspacePath, setWorkspacePath] = useState('')
  const [error,         setError]         = useState<string | null>(null)

  const { mutate: create, isPending } = useMutation({
    mutationFn: () => projectsApi.create({
      name:           name.trim(),
      synopsis:       synopsis.trim(),
      description:    desc.trim() || undefined,
      workspace_path: useExternal && workspacePath.trim() ? workspacePath.trim() : undefined,
    }),
    onSuccess: (project) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      onCreated(project.id)
    },
    onError: () => setError('Failed to create project. Check the workspace path is an absolute path to an existing directory on the server.'),
  })

  const canCreate = name.trim() && synopsis.trim() && (!useExternal || workspacePath.trim())

  return (
    <div className="detail">
      {/* Header */}
      <div className="detail-header">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-xs shrink-0"
          style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '2px 6px 2px 0', marginRight: 4 }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-primary)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
        >
          <ChevronRight size={11} style={{ transform: 'rotate(180deg)' }} /> Projects
        </button>
        <div className="dh-icon" style={{ background: 'rgba(0,212,160,0.1)', borderColor: 'rgba(0,212,160,0.25)', color: 'var(--success)' }}>
          <FolderOpen size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">New Project</h2>
          <p className="dh-sub">Give EVA persistent context for this work</p>
        </div>
        <div className="detail-actions">
          <button className="btn primary" onClick={() => create()} disabled={!canCreate || isPending}>
            {isPending ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
            {isPending ? 'Creating…' : 'Create Project'}
          </button>
        </div>
      </div>

      {/* Server error — outside the scrollable body so it's always visible */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-2.5 text-xs"
          style={{ background: 'rgba(255,77,109,0.09)', borderBottom: '1px solid rgba(255,77,109,0.25)', color: 'var(--danger)', flexShrink: 0 }}>
          <AlertTriangle size={12} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{error}</span>
          <button onClick={() => setError(null)} style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', padding: 0 }}>
            <X size={12} />
          </button>
        </div>
      )}

      <div className="detail-body" style={{ maxWidth: 'none' }}>
        <div className="form-section">
          <h3>Identity</h3>
          <div className="form-row">
            <label className="form-label">Project Name *</label>
            <input
              type="text"
              className="form-input"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="FastAPI Auth Service"
            />
          </div>
          <div className="form-row">
            <label className="form-label">Synopsis * <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>(what the project builds)</span></label>
            <textarea
              className="form-input"
              rows={2}
              value={synopsis}
              onChange={e => setSynopsis(e.target.value)}
              placeholder="JWT auth service with refresh tokens and rate limiting"
              style={{ resize: 'vertical', fontFamily: 'inherit' }}
            />
          </div>
          <div className="form-row" style={{ marginBottom: 0 }}>
            <label className="form-label">Description <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>(optional)</span></label>
            <input
              type="text"
              className="form-input"
              value={desc}
              onChange={e => setDesc(e.target.value)}
              placeholder="Backend microservice for the SaaS platform"
            />
          </div>
        </div>

        <div className="form-section">
          <h3>Workspace</h3>
          <div className="form-row" style={{ marginBottom: 0 }}>
            <div
              className="rounded-lg overflow-hidden"
              style={{ border: `1px solid ${useExternal ? 'rgba(245,158,11,0.3)' : 'var(--border-dim)'}`, background: useExternal ? 'rgba(245,158,11,0.04)' : 'var(--bg-surface2)' }}
            >
              <button
                type="button"
                onClick={() => setUseExternal(v => !v)}
                className="w-full flex items-center gap-2 px-3 py-2.5 text-left"
              >
                <div
                  className="w-7 h-4 rounded-full flex items-center transition-all shrink-0"
                  style={{ background: useExternal ? 'rgba(245,158,11,0.5)' : 'var(--bg-surface3)', border: `1px solid ${useExternal ? 'rgba(245,158,11,0.6)' : 'var(--border-mid)'}`, padding: '1px' }}
                >
                  <div
                    className="w-3 h-3 rounded-full transition-all"
                    style={{ background: useExternal ? 'var(--amber)' : 'var(--text-muted)', marginLeft: useExternal ? 'auto' : 0 }}
                  />
                </div>
                <FolderSymlink size={13} style={{ color: useExternal ? 'var(--amber)' : 'var(--text-muted)', flexShrink: 0 }} />
                <span className="text-xs font-medium" style={{ color: useExternal ? 'var(--amber)' : 'var(--text-muted)' }}>
                  Use existing folder as workspace
                </span>
              </button>
              {useExternal && (
                <div className="px-3 pb-3 flex flex-col gap-1.5" style={{ borderTop: '1px solid rgba(245,158,11,0.15)' }}>
                  <input
                    value={workspacePath}
                    onChange={e => setWorkspacePath(e.target.value)}
                    placeholder="/absolute/path/on/server"
                    className="w-full px-2.5 py-1.5 rounded-lg text-xs outline-none font-mono mt-2"
                    style={{ background: 'var(--bg-surface3)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
                  />
                  <p className="text-xs" style={{ color: 'var(--text-muted)', fontSize: 10 }}>
                    Server folder path — absolute path on the machine running EVA, not your local computer.
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
