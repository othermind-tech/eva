import { useState } from 'react'
import { FolderOpen, Folder, ChevronDown, ChevronRight, Trash2, Check, X } from 'lucide-react'
import type { SkillFileEntry } from '@/types/api'
import { formatBytes } from '@/lib/utils'
import { fileIcon } from './skillHelpers'

// ── Recursive file tree node (V5.6 — skills only) ────────────────────────────
export default function SkillFileTreeNode({
  entry,
  onOpenFile,
  onDeleteFile,
  depth = 0,
}: {
  entry:        SkillFileEntry
  onOpenFile:   (path: string) => void
  onDeleteFile: (path: string) => void
  depth?:       number
}) {
  const [open,       setOpen]      = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const displayName = entry.path.split('/').pop() ?? entry.path
  const isSkillMd   = entry.path === 'SKILL.md'

  if (entry.type === 'directory') {
    return (
      <div>
        <button
          onClick={() => setOpen(o => !o)}
          style={{
            display: 'flex', alignItems: 'center', gap: 8, width: '100%',
            paddingLeft: depth * 16 + 10, paddingTop: 8, paddingBottom: 8,
            background: 'none', border: 'none', cursor: 'pointer', borderRadius: 8,
            textAlign: 'left',
          }}
          onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-surface2)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'none')}
        >
          {open
            ? <FolderOpen size={13} style={{ color: 'var(--amber)', flexShrink: 0 }} />
            : <Folder     size={13} style={{ color: 'var(--amber)', flexShrink: 0 }} />}
          <span style={{ flex: 1, fontSize: 13, color: 'var(--text-secondary)', fontFamily: 'JetBrains Mono, monospace' }}>
            {displayName}
          </span>
          {open
            ? <ChevronDown  size={10} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
            : <ChevronRight size={10} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />}
        </button>
        {open && entry.children?.map(child => (
          <SkillFileTreeNode
            key={child.path}
            entry={child}
            onOpenFile={onOpenFile}
            onDeleteFile={onDeleteFile}
            depth={depth + 1}
          />
        ))}
      </div>
    )
  }

  return (
    <div
      style={{ display: 'flex', alignItems: 'center', borderRadius: 8 }}
      onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-surface2)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >
      <button
        onClick={() => onOpenFile(entry.path)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, flex: 1, cursor: 'pointer',
          paddingTop: 9, paddingBottom: 9, paddingLeft: depth * 16 + 10, paddingRight: 10,
          background: 'none', border: 'none', textAlign: 'left',
        }}
      >
        <span style={{ color: 'var(--neural)', flexShrink: 0 }}>{fileIcon(displayName)}</span>
        <span style={{ flex: 1, fontSize: 13, color: 'var(--text-primary)', fontFamily: 'JetBrains Mono, monospace' }}>
          {displayName}
        </span>
        {entry.size != null && (
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono, monospace' }}>
            {formatBytes(entry.size)}
          </span>
        )}
        <ChevronRight size={12} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
      </button>

      {!isSkillMd && (
        <div style={{ paddingRight: 8, flexShrink: 0 }}>
          {confirmDel ? (
            <div style={{ display: 'flex', gap: 2 }}>
              <button
                onClick={() => { onDeleteFile(entry.path); setConfirmDel(false) }}
                style={{ width: 20, height: 20, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 4, background: 'rgba(255,77,109,0.1)', border: 'none', cursor: 'pointer', color: 'var(--danger)' }}
                title="Confirm delete"
              ><Check size={10} /></button>
              <button
                onClick={() => setConfirmDel(false)}
                style={{ width: 20, height: 20, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 4, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)' }}
              ><X size={10} /></button>
            </div>
          ) : (
            <button
              onClick={e => { e.stopPropagation(); setConfirmDel(true) }}
              style={{ width: 20, height: 20, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 4, background: 'none', border: 'none', cursor: 'pointer', color: 'transparent' }}
              title="Delete file"
              onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
              onMouseLeave={e => (e.currentTarget.style.color = 'transparent')}
            ><Trash2 size={10} /></button>
          )}
        </div>
      )}
      {isSkillMd && (
        <span style={{ paddingRight: 10, fontSize: 9, color: 'var(--neural)', opacity: 0.5 }} title="Core skill file — cannot be deleted">
          core
        </span>
      )}
    </div>
  )
}
