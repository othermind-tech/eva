import { useEffect, useRef, useState } from 'react'
import { ChevronRight, Check, Share2, Edit3, Loader2, AlertTriangle } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { skillsApi } from '@/api/skills'
import { fileIcon } from './skillHelpers'

// ── Skill file editor (Level 3) ───────────────────────────────────────────────
export default function SkillFileEditor({
  skillName, fileName, onBack,
}: {
  skillName: string
  fileName:  string
  onBack:    () => void
}) {
  const qc = useQueryClient()
  const [draft,   setDraft]   = useState<string | null>(null)
  const [dirty,   setDirty]   = useState(false)
  const [saved,   setSaved]   = useState(false)
  const [editing, setEditing] = useState(false)
  // Tracks which file the current draft belongs to — avoids stale-closure deadlock
  const draftKeyRef = useRef<string>('')

  const { data: fileData, isLoading: fileLoading, isError: fileError } = useQuery({
    queryKey: ['skills', skillName, 'files', fileName],
    queryFn:  () => skillsApi.getFile(skillName, fileName),
    retry:    false,
  })

  // Single unified effect: whenever the target file or its data changes, sync draft.
  // Using a ref to compare the current draft's owner avoids the two-effect ordering
  // race where the reset (draft→null) fires AFTER init, but fileData no longer changes.
  useEffect(() => {
    const key = `${skillName}::${fileName}`
    if (draftKeyRef.current !== key) {
      // File switched — reset unconditionally, then init if data is already cached
      draftKeyRef.current = key
      setDirty(false)
      setSaved(false)
      setEditing(false)
      setDraft(fileData?.content ?? null)
    } else if (fileData?.content !== undefined && draft === null) {
      // Same file, data just arrived from network — initialise draft
      setDraft(fileData.content)
    }
  }, [skillName, fileName, fileData])

  const { mutate: save, isPending: saving } = useMutation({
    mutationFn: () => skillsApi.writeFile(skillName, fileName, draft!),
    onSuccess: (res) => {
      // Update the file content cache so re-opening the file is instant
      qc.setQueryData(['skills', skillName, 'files', fileName], {
        name: skillName, filepath: fileName, content: draft,
      })
      qc.invalidateQueries({ queryKey: ['skills', skillName, 'files'] })
      if (res.registry_reloaded) {
        // SKILL.md was edited — skill list metadata may have changed
        qc.invalidateQueries({ queryKey: ['skills'] })
      }
      setSaved(true); setDirty(false)
      setTimeout(() => setSaved(false), 2500)
    },
  })

  const handleExport = () => {
    if (draft === null) return
    const mime = fileName.endsWith('.md') ? 'text/markdown' : 'text/plain'
    const blob = new Blob([draft], { type: mime })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a'); a.href = url; a.download = fileName.split('/').pop()!; a.click()
    URL.revokeObjectURL(url)
  }

  if (fileLoading || (!fileError && draft === null)) {
    return (
      <div className="detail">
        <div className="detail-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Loader2 size={20} className="animate-spin" style={{ color: 'var(--text-muted)' }} />
        </div>
      </div>
    )
  }

  if (fileError) {
    return (
      <div className="detail">
        <div className="detail-body" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
          <AlertTriangle size={22} style={{ color: 'var(--danger)' }} />
          <p style={{ fontSize: 13, color: 'var(--danger)' }}>File not found</p>
          <button className="btn ghost" onClick={onBack}>← Back</button>
        </div>
      </div>
    )
  }

  const displayName = fileName.split('/').pop() ?? fileName

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
          <ChevronRight size={11} style={{ transform: 'rotate(180deg)' }} /> {skillName}
        </button>
        <div className="dh-icon" style={{ background: 'var(--bg-surface3)', borderColor: 'var(--border-mid)', color: 'var(--neural)' }}>
          {fileIcon(displayName)}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title" style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 18 }}>{displayName}</h2>
          <p className="dh-sub" style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>{fileName}</p>
        </div>
        <div className="detail-actions">
          {saved && (
            <span className="chip chip-sm success" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <Check size={11} /> Saved
            </span>
          )}
          <button className="btn ghost" onClick={handleExport} title="Download file">
            <Share2 size={12} /> Export
          </button>
          {!editing ? (
            <button
              className="btn ghost"
              onClick={() => setEditing(true)}
              title="Edit file"
            >
              <Edit3 size={12} /> Edit
            </button>
          ) : (
            <>
              <button
                className="btn ghost"
                onClick={() => { setDraft(fileData?.content ?? ''); setDirty(false); setEditing(false) }}
                title="Discard changes"
              >
                Cancel
              </button>
              <button
                className="btn primary"
                onClick={() => save()}
                disabled={saving || !dirty}
              >
                {saving ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Editor */}
      <div className="detail-body" style={{ maxWidth: 'none', display: 'flex', flexDirection: 'column' }}>
        {dirty && (
          <div style={{ fontSize: 11, color: 'var(--amber)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--amber)', display: 'inline-block' }} />
            Unsaved changes
          </div>
        )}
        <textarea
          className="form-textarea mono"
          style={{
            flex: 1, resize: 'none', minHeight: 400, fontSize: 13,
            cursor: editing ? 'text' : 'default',
            opacity: editing ? 1 : 0.85,
            borderColor: editing ? undefined : 'var(--border-dim)',
          }}
          value={draft ?? ''}
          readOnly={!editing}
          onChange={e => { setDraft(e.target.value); setDirty(true) }}
          spellCheck={false}
        />
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8, textAlign: 'right', fontFamily: 'JetBrains Mono, monospace' }}>
          {(draft ?? '').length.toLocaleString()} chars
        </p>
      </div>
    </div>
  )
}
