import { useEffect, useRef, useState } from 'react'
import { Wand2, RefreshCw, Loader2, ChevronDown, Plus, UploadCloud, Search, X, AlertTriangle, ChevronRight } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { skillsApi } from '@/api/skills'
import SkillCreateForm from './SkillCreateForm'

// ── Skills list ───────────────────────────────────────────────────────────────
export default function SkillsListView({ onPick }: { onPick: (name: string) => void }) {
  const [search,      setSearch]      = useState('')
  const [showCreate,  setShowCreate]  = useState(false)
  const [newMenuOpen, setNewMenuOpen] = useState(false)
  const [uploading,   setUploading]   = useState(false)
  const newMenuRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()

  const { data: skills = [], isLoading, isError } = useQuery({
    queryKey: ['skills'],
    queryFn:  skillsApi.list,
  })

  const { mutate: reload, isPending: reloading } = useMutation({
    mutationFn: () => skillsApi.reload(),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['skills'] }),
  })

  // Close "New" dropdown on outside click
  useEffect(() => {
    if (!newMenuOpen) return
    const h = (e: MouseEvent) => {
      if (newMenuRef.current && !newMenuRef.current.contains(e.target as Node)) setNewMenuOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [newMenuOpen])

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      await skillsApi.install(file)
      qc.invalidateQueries({ queryKey: ['skills'] })
    } catch { /* silently ignore */ }
    finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const filtered = search.trim()
    ? skills.filter(s =>
        s.name.toLowerCase().includes(search.toLowerCase()) ||
        (s.description ?? '').toLowerCase().includes(search.toLowerCase())
      )
    : skills

  return (
    <div className="detail">
      {/* Hidden file input for upload */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".md,.yaml,.yml,.zip"
        style={{ display: 'none' }}
        onChange={handleFileUpload}
      />

      {/* Header */}
      <div className="detail-header">
        <div className="dh-icon" style={{ background: 'rgba(124,108,243,0.12)', borderColor: 'rgba(124,108,243,0.25)', color: 'var(--neural)' }}>
          <Wand2 size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">Skills</h2>
          <p className="dh-sub">Reusable prompt presets · {skills.length} loaded</p>
        </div>
        <div className="detail-actions">
          <button
            className="btn ghost"
            onClick={() => reload()}
            disabled={reloading}
            title="Reload skills from disk"
          >
            <RefreshCw size={12} className={reloading ? 'animate-spin' : ''} /> Reload
          </button>

          {/* New skill — split button with dropdown */}
          <div style={{ position: 'relative' }} ref={newMenuRef}>
            <button
              className="btn primary"
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              onClick={() => setNewMenuOpen(o => !o)}
            >
              {uploading
                ? <Loader2 size={12} className="animate-spin" />
                : <Plus size={12} />}
              New skill
              <ChevronDown size={11} style={{ opacity: 0.7, marginLeft: 2 }} />
            </button>
            {newMenuOpen && (
              <div className="popover" style={{ top: 'calc(100% + 6px)', right: 0, minWidth: 200 }}>
                <button
                  className="pop-item"
                  onClick={() => { setShowCreate(true); setNewMenuOpen(false) }}
                >
                  <Plus size={13} /> Create new skill
                </button>
                <button
                  className="pop-item"
                  onClick={() => { setNewMenuOpen(false); fileInputRef.current?.click() }}
                  disabled={uploading}
                >
                  <UploadCloud size={13} /> {uploading ? 'Uploading…' : 'Upload skill file'}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="detail-body" style={{ maxWidth: 'none' }}>
        {/* Search */}
        <div className="search-field" style={{ marginBottom: 16 }}>
          <Search size={13} />
          <input
            placeholder="Search skills…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
              <X size={11} />
            </button>
          )}
        </div>

        {/* Create form — inline */}
        {showCreate && (
          <SkillCreateForm onDone={() => setShowCreate(false)} />
        )}

        {/* States */}
        {isLoading && (
          <div className="flex items-center justify-center gap-2 py-10" style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            <Loader2 size={16} className="animate-spin" /> Loading skills…
          </div>
        )}
        {isError && (
          <div className="flex items-center gap-2 py-6" style={{ color: 'var(--danger)', fontSize: 13 }}>
            <AlertTriangle size={14} /> Failed to load skills
          </div>
        )}
        {!isLoading && !isError && filtered.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-14 text-center">
            <Wand2 size={28} style={{ color: 'var(--text-muted)' }} />
            <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              {search ? `No skills match "${search}"` : 'No skills loaded yet.'}
            </p>
            {!search && (
              <button className="btn ghost" onClick={() => setShowCreate(true)}>
                <Plus size={12} /> Create first skill
              </button>
            )}
          </div>
        )}

        {/* Skill cards */}
        {filtered.map(skill => (
          <div
            key={skill.name}
            className="kn-card"
            style={{ cursor: 'pointer' }}
            onClick={() => onPick(skill.name)}
          >
            <div className="kn-head">
              <div className="row-icon" style={{ background: 'rgba(124,108,243,0.1)', color: 'var(--neural)', width: 32, height: 32, borderRadius: 9, flexShrink: 0 }}>
                <Wand2 size={14} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text-primary)' }}>{skill.name}</div>
                <div style={{ fontSize: 11.5, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono, monospace' }}>
                  /{skill.name}{skill.version ? ` · v${skill.version}` : ''}{skill.author ? ` · ${skill.author}` : ''}
                </div>
              </div>
              <ChevronRight size={14} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
            </div>
            {skill.description && (
              <div className="kn-body" style={{ marginTop: 6 }}>
                {skill.description.slice(0, 140)}{skill.description.length > 140 ? '…' : ''}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
