import { useEffect, useRef, useState } from 'react'
import { ChevronRight, Wand2, Play, MoreHorizontal, Trash2, Loader2, Plus, Search, KeyRound, FilePlus, Check, AlertTriangle, X } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { skillsApi } from '@/api/skills'
import { toolsApi } from '@/api/tools'
import type { SkillInfo, Tool } from '@/types/api'
import { useChatStore } from '@/store/chatStore'
import SkillFileTreeNode from './SkillFileTreeNode'
import { setFrontmatterTools } from './skillHelpers'

// ── Skill folder view (Level 2) ───────────────────────────────────────────────
export default function SkillFolderView({
  name, onBack, onOpenFile,
}: {
  name:       string
  onBack:     () => void
  onOpenFile: (filepath: string) => void
}) {
  const qc = useQueryClient()
  const setViewConversation = useChatStore(s => s.setViewConversation)
  const [more,          setMore]          = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [showNewFile,   setShowNewFile]   = useState(false)
  const [newFilePath,   setNewFilePath]   = useState('')
  const [toolsOpen,     setToolsOpen]     = useState(false)
  const [toolSearch,    setToolSearch]    = useState('')
  const [savingTools,   setSavingTools]   = useState(false)
  const moreRef  = useRef<HTMLDivElement>(null)
  const toolsRef = useRef<HTMLDivElement>(null)

  // File tree (V5.6 — recursive)
  const { data: filesData, isLoading: listLoading } = useQuery({
    queryKey: ['skills', name, 'files'],
    queryFn:  () => skillsApi.getFiles(name),
    retry:    false,
  })

  // Skill metadata from list cache (GET /skills/{name} removed in V5.6)
  const skill = useQueryClient().getQueryData<SkillInfo[]>(['skills'])?.find(s => s.name === name)

  const treeFiles = filesData?.files ?? []

  // Local tools state — initialised from skill cache, editable
  const [localTools, setLocalTools] = useState<string[]>(() => skill?.allowed_tools ?? skill?.tools ?? [])
  useEffect(() => { setLocalTools(skill?.allowed_tools ?? skill?.tools ?? []) }, [skill?.name])

  // All available tools for the add-tool picker
  const { data: allTools = [] } = useQuery<Tool[]>({
    queryKey: ['tools-all'],
    queryFn:  toolsApi.listAll,
    staleTime: 60_000,
  })
  const filteredAvailableTools = (allTools as Tool[]).filter(t =>
    !localTools.includes(t.name) &&
    (!toolSearch || t.name.toLowerCase().includes(toolSearch.toLowerCase()))
  )

  // Write updated tools list back into SKILL.md frontmatter
  const updateSkillTools = async (tools: string[]) => {
    setSavingTools(true)
    try {
      const fileData = await skillsApi.getFile(name, 'SKILL.md')
      const updated  = setFrontmatterTools(fileData.content, tools)
      await skillsApi.writeFile(name, 'SKILL.md', updated)
      qc.invalidateQueries({ queryKey: ['skills'] })
      qc.removeQueries({ queryKey: ['skills', name, 'files', 'SKILL.md'] })
    } catch { /* silently ignore */ }
    finally { setSavingTools(false) }
  }

  const addTool = (toolName: string) => {
    if (localTools.includes(toolName)) return
    const next = [...localTools, toolName]
    setLocalTools(next)
    setToolSearch('')
    setToolsOpen(false)
    updateSkillTools(next)
  }

  const removeTool = (toolName: string) => {
    const next = localTools.filter(t => t !== toolName)
    setLocalTools(next)
    updateSkillTools(next)
  }

  // Close more menu on outside click
  useEffect(() => {
    if (!more) return
    const h = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) setMore(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [more])

  // Close tools picker on outside click
  useEffect(() => {
    if (!toolsOpen) return
    const h = (e: MouseEvent) => {
      if (toolsRef.current && !toolsRef.current.contains(e.target as Node)) setToolsOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [toolsOpen])

  const { mutate: deleteSkill } = useMutation({
    mutationFn: () => skillsApi.delete(name),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['skills'] }); onBack() },
  })

  const { mutate: deleteFile } = useMutation({
    mutationFn: (filepath: string) => skillsApi.deleteFile(name, filepath),
    onSuccess:  (_, filepath) => {
      qc.invalidateQueries({ queryKey: ['skills', name, 'files'] })
      qc.removeQueries({ queryKey: ['skills', name, 'files', filepath] })
    },
  })

  const { mutate: createFile, isPending: creating } = useMutation({
    mutationFn: (path: string) => skillsApi.writeFile(name, path, ''),
    onSuccess:  (_, path) => {
      qc.invalidateQueries({ queryKey: ['skills', name, 'files'] })
      setShowNewFile(false)
      setNewFilePath('')
      onOpenFile(path)
    },
  })

  return (
    <div className="detail" style={{ position: 'relative' }}>
      {/* Header */}
      <div className="detail-header">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-xs shrink-0"
          style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '2px 6px 2px 0', marginRight: 4 }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-primary)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
        >
          <ChevronRight size={11} style={{ transform: 'rotate(180deg)' }} /> Skills
        </button>
        <div className="dh-icon" style={{ background: 'rgba(124,108,243,0.12)', borderColor: 'rgba(124,108,243,0.25)', color: 'var(--neural)' }}>
          <Wand2 size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">{name}</h2>
          <p className="dh-sub">
            /{name}{skill?.version ? ` · v${skill.version}` : ''}
            {treeFiles.length > 0 ? ` · ${treeFiles.length} item${treeFiles.length !== 1 ? 's' : ''}` : ''}
          </p>
        </div>
        <div className="detail-actions">
          <button className="btn primary" onClick={() => setViewConversation()}>
            <Play size={12} /> Run
          </button>
          <div style={{ position: 'relative' }} ref={moreRef}>
            <button className="icon-btn" style={{ width: 30, height: 30 }} onClick={() => setMore(m => !m)}>
              <MoreHorizontal size={15} />
            </button>
            {more && (
              <div className="popover" style={{ top: 36, right: 0, minWidth: 180 }} onClick={e => e.stopPropagation()}>
                <button className="pop-item danger" onClick={() => { setConfirmDelete(true); setMore(false) }}>
                  <Trash2 size={13} /> Delete skill
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="detail-body" style={{ maxWidth: 'none' }}>

        {/* ── NAME ──────────────────────────────────────────────────────────── */}
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 8 }}>Name</div>
          <div style={{ padding: '10px 14px', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 10, fontFamily: 'JetBrains Mono, monospace', fontSize: 13, color: 'var(--text-primary)' }}>
            {name}
          </div>
          <p style={{ marginTop: 6, paddingLeft: 2, fontSize: 11.5, color: 'var(--text-muted)' }}>
            Trigger: <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--neural)', fontWeight: 600 }}>/{name}</span> · type in chat to activate this skill
          </p>
        </div>

        {/* ── DESCRIPTION ───────────────────────────────────────────────────── */}
        {skill?.description && (
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 8 }}>Description</div>
            <div style={{ padding: '12px 14px', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 10, fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.65, minHeight: 52 }}>
              {skill.description}
            </div>
          </div>
        )}

        {/* ── VERSION / AUTHOR / LICENSE ────────────────────────────────────── */}
        {(skill?.version || skill?.author || skill?.license) && (
          <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
            {skill?.version && (
              <div style={{ flex: '0 0 auto' }}>
                <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 8 }}>Version</div>
                <div style={{ padding: '8px 14px', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 10, fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
                  v{skill.version}
                </div>
              </div>
            )}
            {skill?.author && (
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 8 }}>Author</div>
                <div style={{ padding: '8px 14px', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 10, fontSize: 12, color: 'var(--text-primary)' }}>
                  {skill.author}
                </div>
              </div>
            )}
            {skill?.license && (
              <div style={{ flex: '0 0 auto' }}>
                <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 8 }}>License</div>
                <div style={{ padding: '8px 14px', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 10, fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                  {skill.license}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── BUNDLED TOOLS ─────────────────────────────────────────────────── */}
        <div style={{ marginBottom: 28 }}>
          {/* Header row */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>
              Bundled Tools
              {savingTools && <Loader2 size={11} className="animate-spin" style={{ marginLeft: 7, color: 'var(--text-muted)', display: 'inline' }} />}
            </div>
            <div style={{ position: 'relative' }} ref={toolsRef}>
              <button
                onClick={() => setToolsOpen(o => !o)}
                className="flex items-center gap-1"
                style={{
                  fontSize: 11, padding: '3px 8px', borderRadius: 6, cursor: 'pointer',
                  color: toolsOpen ? 'var(--eva-primary)' : 'var(--text-muted)',
                  background: toolsOpen ? 'rgba(0,201,255,0.08)' : 'transparent',
                  border: `1px solid ${toolsOpen ? 'rgba(0,201,255,0.2)' : 'transparent'}`,
                }}
              >
                <Plus size={11} /> Add Tool
              </button>
              {toolsOpen && (
                <div style={{
                  position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 50,
                  width: 260, background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)',
                  borderRadius: 10, boxShadow: '0 8px 32px rgba(0,0,0,0.4)', overflow: 'hidden',
                }}>
                  <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border-dim)' }}>
                    <div className="flex items-center gap-2" style={{ background: 'var(--bg-base)', borderRadius: 6, padding: '5px 9px', border: '1px solid var(--border-dim)' }}>
                      <Search size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                      <input
                        autoFocus
                        value={toolSearch}
                        onChange={e => setToolSearch(e.target.value)}
                        placeholder="Search tools…"
                        style={{ background: 'transparent', border: 'none', outline: 'none', fontSize: 12, color: 'var(--text-primary)', flex: 1 }}
                      />
                    </div>
                  </div>
                  <div style={{ maxHeight: 200, overflowY: 'auto', padding: '4px 0' }}>
                    {filteredAvailableTools.length === 0 ? (
                      <div style={{ padding: '10px 14px', fontSize: 12, color: 'var(--text-muted)' }}>
                        {toolSearch ? 'No tools match.' : 'All available tools already added.'}
                      </div>
                    ) : filteredAvailableTools.map(t => (
                      <button
                        key={t.name}
                        onClick={() => addTool(t.name)}
                        className="w-full text-left"
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          padding: '7px 14px', fontSize: 12.5, cursor: 'pointer',
                          color: 'var(--text-primary)', background: 'transparent',
                          border: 'none', transition: 'background 0.1s',
                        }}
                        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(0,201,255,0.06)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                      >
                        <KeyRound size={11} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
                        <span style={{ fontFamily: 'JetBrains Mono, monospace', flex: 1 }}>{t.name}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginBottom: 14 }}>Tools the skill prefers. Override per-conversation.</div>
          {localTools.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {localTools.map(t => (
                <div
                  key={t}
                  style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 10 }}
                >
                  <KeyRound size={13} style={{ color: 'var(--eva-primary)', flexShrink: 0 }} />
                  <span style={{ fontSize: 13, fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-primary)', flex: 1 }}>{t}</span>
                  <button
                    onClick={() => removeTool(t)}
                    className="icon-btn"
                    title="Remove tool"
                    style={{ width: 22, height: 22, padding: 0, color: 'var(--text-muted)', flexShrink: 0 }}
                    onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
                    onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ padding: '10px 14px', background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)', borderRadius: 10, fontSize: 12.5, color: 'var(--text-muted)' }}>
              All tools available — no restriction set in this skill.
            </div>
          )}
        </div>

        {/* ── DIVIDER before Files ──────────────────────────────────────────── */}
        <div style={{ borderTop: '1px solid var(--border-dim)', paddingTop: 20 }}>

        {/* Files section header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>Files</div>
          <button
            onClick={() => setShowNewFile(s => !s)}
            className="flex items-center gap-1"
            style={{
              fontSize: 11, padding: '3px 8px', borderRadius: 6, cursor: 'pointer',
              color: showNewFile ? 'var(--eva-primary)' : 'var(--text-muted)',
              background: showNewFile ? 'rgba(0,201,255,0.08)' : 'transparent',
              border: `1px solid ${showNewFile ? 'rgba(0,201,255,0.2)' : 'transparent'}`,
            }}
            title="New file"
          >
            <FilePlus size={11} /> New
          </button>
        </div>

        {/* New file input */}
        {showNewFile && (
          <div className="flex items-center gap-2" style={{ marginBottom: 12 }}>
            <input
              autoFocus
              value={newFilePath}
              onChange={e => setNewFilePath(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && newFilePath.trim()) createFile(newFilePath.trim())
                if (e.key === 'Escape') { setShowNewFile(false); setNewFilePath('') }
              }}
              placeholder="phases/06-deploy.md"
              className="form-input mono"
              style={{ flex: 1, fontSize: 12 }}
            />
            <button
              onClick={() => { if (newFilePath.trim()) createFile(newFilePath.trim()) }}
              disabled={!newFilePath.trim() || creating}
              className="icon-btn"
              style={{
                width: 28, height: 28,
                color: newFilePath.trim() ? 'var(--eva-primary)' : 'var(--text-muted)',
              }}
            >
              {creating ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
            </button>
          </div>
        )}

        {/* File tree */}
        {listLoading ? (
          <div className="flex items-center justify-center gap-2 py-10" style={{ color: 'var(--text-muted)' }}>
            <Loader2 size={16} className="animate-spin" />
          </div>
        ) : treeFiles.length === 0 ? (
          <p style={{ fontSize: 13, color: 'var(--text-muted)', padding: '16px 0' }}>No files found in this skill.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {treeFiles.map(entry => (
              <SkillFileTreeNode
                key={entry.path}
                entry={entry}
                onOpenFile={onOpenFile}
                onDeleteFile={path => deleteFile(path)}
              />
            ))}
          </div>
        )}
        </div>{/* end files wrapper */}
      </div>

      {/* Confirm delete skill overlay */}
      {confirmDelete && (
        <div
          className="flex flex-col items-center justify-center gap-4"
          style={{ position: 'absolute', inset: 0, zIndex: 20, background: 'rgba(6,9,15,0.85)', backdropFilter: 'blur(6px)', padding: 40, textAlign: 'center' }}
        >
          <AlertTriangle size={28} style={{ color: 'var(--danger)' }} />
          <div>
            <p style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>Delete "{name}"?</p>
            <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>This removes the entire skill from disk. This cannot be undone.</p>
          </div>
          <div className="flex gap-3">
            <button className="btn ghost" onClick={() => setConfirmDelete(false)}>Cancel</button>
            <button className="btn primary" style={{ background: 'var(--danger)', borderColor: 'var(--danger)' }} onClick={() => deleteSkill()}>
              <Trash2 size={12} /> Delete
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
