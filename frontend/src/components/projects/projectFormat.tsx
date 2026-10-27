import { useState } from 'react'
import { ChevronDown, ChevronRight, Folder, FolderOpen, File, FileText, FileCode, Trash2, Check, X } from 'lucide-react'
import type { ProjectFile } from '@/api/projects'
import { formatBytes, openFile } from '@/lib/utils'

// ── Project overview body ────────────────────────────────────────────────────
export function copyPath(text: string) {
  if (navigator?.clipboard) navigator.clipboard.writeText(text).catch(() => {})
}

export function daemonHealth(daemons: { lifecycle_status: string }[]) {
  const count  = daemons.length
  if (count === 0) return { label: '—', color: 'var(--text-muted)' }
  const active = daemons.filter(d => d.lifecycle_status === 'running').length
  if (active === count) return { label: `${active} active`,            color: 'var(--success)' }
  if (active === 0)     return { label: 'all paused',                  color: 'var(--danger)'  }
  return                       { label: `${active} of ${count} active`, color: 'var(--amber)'  }
}

export function relativeDate(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime()
    const mins  = Math.floor(diff / 60_000)
    const hours = Math.floor(diff / 3_600_000)
    const days  = Math.floor(diff / 86_400_000)
    if (mins  <  1) return 'now'
    if (mins  < 60) return `${mins}m`
    if (hours < 24) return `${hours}h`
    if (days  <  7) return `${days}d`
    return `${Math.floor(days / 7)}w`
  } catch { return '' }
}

// ── Metric formatting helpers ─────────────────────────────────────────────────
export function fmtCost(usd: number | null, partial: boolean): string {
  if (usd === null) return '—'
  const s = usd < 0.01 ? '<$0.01' : `$${usd.toFixed(2)}`
  return partial ? `${s}*` : s
}
export function fmtDuration(ms: number | null): string {
  if (!ms) return '—'
  if (ms < 60_000)       return `${(ms / 1000).toFixed(0)}s`
  if (ms < 3_600_000)    return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
  const h = Math.floor(ms / 3_600_000)
  const m = Math.round((ms % 3_600_000) / 60_000)
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}
export function fmtTokensK(n: number): string {
  if (n === 0)     return '0'
  if (n < 1_000)   return String(n)
  if (n < 1_000_000) return `${(n / 1_000).toFixed(1)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}

// ── File tree helpers ─────────────────────────────────────────
export interface TreeFile {
  kind:    'file'
  name:    string
  file:    ProjectFile
}
export interface TreeDir {
  kind:     'dir'
  name:     string
  children: TreeNode[]
}
export type TreeNode = TreeFile | TreeDir

export function buildTree(files: ProjectFile[]): TreeNode[] {
  const root: TreeDir = { kind: 'dir', name: '', children: [] }
  for (const file of files) {
    const parts = file.path.split('/')
    let cur = root
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i]
      let child = cur.children.find(c => c.kind === 'dir' && c.name === seg) as TreeDir | undefined
      if (!child) {
        child = { kind: 'dir', name: seg, children: [] }
        cur.children.push(child)
      }
      cur = child
    }
    cur.children.push({ kind: 'file', name: parts[parts.length - 1], file })
  }
  // Sort: dirs first (alpha), then files (alpha)
  function sortNode(dir: TreeDir) {
    dir.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    dir.children.forEach(c => { if (c.kind === 'dir') sortNode(c) })
  }
  sortNode(root)
  return root.children
}

export function FileTreeNode({
  node, depth, workspaceRoot, onDelete, confirmFile, setConfirmFile,
}: {
  node: TreeNode
  depth: number
  workspaceRoot: string
  onDelete: (path: string) => void
  confirmFile: string | null
  setConfirmFile: (p: string | null) => void
}) {
  const [open, setOpen] = useState(false)
  // Declared before the folder early-return below: hooks must run in the same order on every render, and
  // this tree keys nodes by index, so one position can switch between a folder and a file.
  const [openToast, setOpenToast] = useState<string | null>(null)
  const indent = depth * 14

  if (node.kind === 'dir') {
    return (
      <div>
        <button
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-1.5 w-full text-left py-1 rounded-lg transition-colors group"
          style={{ paddingLeft: indent + 4 }}
          onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.04)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        >
          {open
            ? <ChevronDown  size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
            : <ChevronRight size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />}
          {open
            ? <FolderOpen size={13} style={{ color: 'var(--amber)', flexShrink: 0 }} />
            : <Folder     size={13} style={{ color: 'var(--amber)', flexShrink: 0 }} />}
          <span className="text-xs font-semibold truncate" style={{ color: 'var(--text-secondary)' }}>
            {node.name}
          </span>
          <span className="text-xs ml-1 shrink-0" style={{ color: 'var(--text-muted)', fontSize: 10 }}>
            {node.children.length}
          </span>
        </button>
        {open && node.children.map((child, i) => (
          <FileTreeNode
            key={i} node={child} depth={depth + 1} workspaceRoot={workspaceRoot}
            onDelete={onDelete} confirmFile={confirmFile} setConfirmFile={setConfirmFile}
          />
        ))}
      </div>
    )
  }

  const { file } = node

  const handleOpenFile = async () => {
    // Prefer backend-resolved absolute path; fall back to manual construction
    const fullPath = file.storage_path ?? (workspaceRoot ? `${workspaceRoot}/${file.path}` : file.path)
    const msg = await openFile(fullPath)
    setOpenToast(msg)
    setTimeout(() => setOpenToast(null), 2500)
  }

  return (
    <div className="relative">
      {openToast && (
        <div
          className="absolute left-4 -top-7 z-50 px-3 py-1 rounded-lg text-xs whitespace-nowrap pointer-events-none"
          style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-mid)', color: 'var(--text-secondary)', boxShadow: '0 4px 16px rgba(0,0,0,0.4)' }}
        >
          {openToast}
        </div>
      )}
      <div
        className="flex items-center gap-2 py-1 rounded-lg group transition-colors cursor-pointer"
        style={{ paddingLeft: indent + 4 }}
        onClick={handleOpenFile}
        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.06)')}
        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        title={workspaceRoot ? `${workspaceRoot}/${file.path}` : file.path}
      >
      <FileIcon path={file.path} size={11} />
      <span className="text-xs flex-1 truncate font-mono" style={{ color: 'var(--text-primary)', fontSize: 11 }} title={file.path}>
        {node.name}
      </span>
      {/* source badge */}
      <span className="shrink-0" style={{
        fontSize: 9, fontWeight: 600, padding: '1px 5px', borderRadius: 99,
        background: file.source === 'uploaded'  ? 'rgba(245,158,11,0.1)'
                  : file.source === 'knowledge' ? 'rgba(124,108,243,0.12)'
                  : 'rgba(0,201,255,0.08)',
        color:      file.source === 'uploaded'  ? 'var(--amber)'
                  : file.source === 'knowledge' ? 'var(--neural)'
                  : 'var(--eva-primary)',
        border:     `1px solid ${
          file.source === 'uploaded'  ? 'rgba(245,158,11,0.25)'
          : file.source === 'knowledge' ? 'rgba(124,108,243,0.28)'
          : 'rgba(0,201,255,0.2)'
        }`,
      }}>
        {file.source}
      </span>
      <span className="text-xs shrink-0" style={{ color: 'var(--text-muted)', fontSize: 10 }}>{formatBytes(file.size)}</span>
      <span className="text-xs shrink-0 font-mono" style={{ color: 'var(--text-muted)', fontSize: 10 }} title={new Date(file.modified_at).toLocaleString()}>
        {fmtRelTime(file.modified_at)}
      </span>
      {/* delete — uploaded only; stop propagation so click doesn't trigger openFile */}
      {file.source === 'uploaded' && (
        confirmFile === file.path ? (
          <div className="flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
            <span className="text-xs" style={{ color: 'var(--danger)', fontSize: 10 }}>Delete?</span>
            <button onClick={() => onDelete(file.path)} className="w-5 h-5 flex items-center justify-center rounded" style={{ color: 'var(--danger)', background: 'rgba(255,77,109,0.1)' }}><Check size={9} /></button>
            <button onClick={() => setConfirmFile(null)} className="w-5 h-5 flex items-center justify-center rounded" style={{ color: 'var(--text-muted)' }}><X size={9} /></button>
          </div>
        ) : (
          <button
            onClick={e => { e.stopPropagation(); setConfirmFile(file.path) }}
            className="w-5 h-5 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 transition-opacity"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
          >
            <Trash2 size={10} />
          </button>
        )
      )}
      </div>
    </div>
  )
}

export function fmtRelTime(iso: string): string {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1)  return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24)  return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export function FileIcon({ path, size = 12 }: { path: string; size?: number }) {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  const codeExts = ['py', 'ts', 'tsx', 'js', 'jsx', 'sh', 'json', 'yaml', 'yml', 'toml', 'css', 'html', 'rs', 'go', 'java', 'cpp', 'c', 'rb']
  const textExts = ['md', 'txt', 'csv', 'log', 'rst', 'pdf', 'doc', 'docx']
  if (codeExts.includes(ext)) return <FileCode size={size} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
  if (textExts.includes(ext)) return <FileText size={size} style={{ color: 'var(--neural)', flexShrink: 0 }} />
  return <File size={size} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
}
