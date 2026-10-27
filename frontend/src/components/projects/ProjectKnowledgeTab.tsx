import { useState, useEffect, useRef, useCallback } from 'react'
import { Search, X, Upload, CheckCircle2, AlertCircle, Loader2, BookOpen, ChevronRight, ChevronDown, Trash2 } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi } from '@/api/projects'
import type { IngestJobStatus } from '@/types/api'
import { RAG_POLL_INTERVAL_MS } from '@/lib/constants'
import { FILE_TYPE_STYLES, getFileGroup, getFileTypeStyle as getFileTypeStyleForFile, getExt, RAG_SECTION_NAME } from '@/lib/ragFileTypes'
import { openFile } from '@/lib/utils'
import { friendlyUploadError } from '@/lib/errors'
import { useChatStore } from '@/store/chatStore'

// ── Project Knowledge tab — same icon-card grid design as RAGBrowseCard ───────
interface ProjKnowledgeJob {
  jobId:    string
  filename: string
  status:   IngestJobStatus['status']
  done:     number
  total:    number
  error:    string | null
}

export default function ProjectKnowledgeTab({ projectId }: { projectId: string }) {
  const qc      = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)

  // Upload-trigger signal from WorkspacePanel (+) button
  const uploadPending    = useChatStore(s => s.projectKnowledgeUpload)
  const clearUploadPending = useChatStore(s => s.setProjectKnowledgeUpload)

  const [search,      setSearch]      = useState('')
  const [collapsed,   setCollapsed]   = useState<Set<string>>(new Set())
  const [jobs,        setJobs]        = useState<ProjKnowledgeJob[]>([])
  const [confirmFile, setConfirmFile] = useState<string | null>(null)
  const [openToast,   setOpenToast]   = useState<string | null>(null)

  // Fire the file dialog when (+) was clicked in the sidebar
  useEffect(() => {
    if (!uploadPending) return
    fileRef.current?.click()
    clearUploadPending(false)
  }, [uploadPending, clearUploadPending])

  // Auto-dismiss completed jobs after 3 s
  const hasCompleted = jobs.some(j => j.status === 'completed')
  useEffect(() => {
    if (!hasCompleted) return
    const t = setTimeout(() => setJobs(prev => prev.filter(j => j.status !== 'completed')), 3000)
    return () => clearTimeout(t)
  }, [hasCompleted])

  const { data, isLoading } = useQuery({
    queryKey: ['project-files', projectId],
    queryFn:  () => projectsApi.listFiles(projectId),
    staleTime: 30_000,
  })

  const allFiles      = data?.files ?? []
  // V6.2: knowledge files have source === 'knowledge'
  const knowledgeFiles = allFiles.filter(f => f.source === 'knowledge')

  // Delete a knowledge file — DELETE /projects/{id}/knowledge/{filename}
  const { mutate: deleteKnFile, isPending: isDeletingKn, variables: deletingKnName } = useMutation({
    mutationFn: (filename: string) => projectsApi.deleteKnowledgeFile(projectId, filename),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['project-files', projectId] }); setConfirmFile(null) },
    onError:    () => { setConfirmFile(null) },
  })

  const pollJob = useCallback(async (jobId: string) => {
    const tick = async () => {
      try {
        const s = await projectsApi.getIngestStatus(jobId)
        setJobs(prev => prev.map(j => j.jobId === jobId
          ? { ...j, status: s.status, done: s.chunks_done, total: s.total_chunks, error: s.error }
          : j,
        ))
        if (s.status === 'completed') { qc.invalidateQueries({ queryKey: ['project-files', projectId] }); return }
        if (s.status === 'failed') return
        setTimeout(tick, RAG_POLL_INTERVAL_MS)
      } catch { setTimeout(tick, RAG_POLL_INTERVAL_MS * 2) }
    }
    setTimeout(tick, 1500)
  }, [projectId, qc])

  const uploadFile = useCallback(async (file: File) => {
    try {
      // V6.2: knowledge upload → POST /projects/{id}/knowledge (async, returns job_id)
      const res = await projectsApi.uploadKnowledge(projectId, file)
      setJobs(prev => [{ jobId: res.job_id, filename: res.filename, status: 'queued', done: 0, total: 0, error: null }, ...prev])
      pollJob(res.job_id)
    } catch (err: unknown) {
      // client.ts flattens errors to { status, detail }; there is no `.response`
      const is415    = (err as { status?: number } | null)?.status === 415
      setJobs(prev => [{
        jobId:    `err-${Date.now()}`,
        filename: file.name,
        status:   'failed',
        done:     0,
        total:    0,
        error:    is415
          ? 'File type not supported for knowledge indexing. Use the Files tab to store it.'
          : 'Upload failed',
      }, ...prev])
    }
  }, [projectId, pollJob])

  // Totals
  const totalBytes   = knowledgeFiles.reduce((s, f) => s + (f.size ?? 0), 0)
  const totalSizeStr = totalBytes >= 1024 * 1024
    ? `${(totalBytes / (1024 * 1024)).toFixed(1)} MB`
    : `${(totalBytes / 1024).toFixed(1)} KB`

  // Basename helper
  const bn = (path: string) => path.split('/').pop() ?? path

  // Search filter (against basename)
  const searchLc       = search.toLowerCase().trim()
  const filteredFiles  = searchLc
    ? knowledgeFiles.filter(f => bn(f.path).toLowerCase().includes(searchLc))
    : knowledgeFiles

  // Group by file type
  const knownLabels = new Set(FILE_TYPE_STYLES.map(s => s.label))
  const otherKFiles = filteredFiles.filter(f => !knownLabels.has(getFileGroup(bn(f.path))))
  const groups = [
    ...FILE_TYPE_STYLES.map(style => ({
      label:      style.label,
      style,
      groupFiles: filteredFiles.filter(f => getFileGroup(bn(f.path)) === style.label),
    })).filter(g => g.groupFiles.length > 0),
    ...(otherKFiles.length > 0 ? [{
      label:      'Other',
      style:      getFileTypeStyleForFile('unknown.bin'),
      groupFiles: otherKFiles,
    }] : []),
  ]

  const activeJobs = jobs.filter(j => j.status !== 'completed')

  return (
    <div className="flex flex-col gap-5 relative">

      {/* Open-file toast */}
      {openToast && (
        <div
          className="pointer-events-none absolute bottom-0 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-xl text-xs whitespace-nowrap"
          style={{ background: 'var(--bg-surface3)', border: '1px solid var(--border-mid)', color: 'var(--text-secondary)', boxShadow: '0 4px 20px rgba(0,0,0,0.5)' }}
        >
          {openToast}
        </div>
      )}

      {/* Action row: search + upload */}
      <div className="flex items-center gap-3">
        <div className="search-field" style={{ flex: 1 }}>
          <Search size={13} />
          <input
            placeholder="Search knowledge files…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
              <X size={11} />
            </button>
          )}
        </div>
        <button
          className="btn primary"
          style={{ fontSize: 12, gap: 5, flexShrink: 0 }}
          onClick={() => fileRef.current?.click()}
        >
          <Upload size={12} /> Upload
        </button>
      </div>

      <input
        ref={fileRef}
        type="file"
        className="hidden"
        accept=".pdf,.docx,.txt,.md,.csv,.json,.yaml,.yml"
        multiple
        onChange={e => { if (e.target.files) Array.from(e.target.files).forEach(uploadFile) }}
      />

      {/* In-flight jobs */}
      {jobs.length > 0 && (
        <div className="flex flex-col gap-2">
          {jobs.map(job => (
            <div
              key={job.jobId}
              className="rounded-xl p-3 flex flex-col gap-2"
              style={{
                background: 'var(--bg-surface2)',
                border: `1px solid ${
                  job.status === 'completed' ? 'rgba(0,212,160,0.3)'
                  : job.status === 'failed'  ? 'rgba(255,77,109,0.3)'
                  : 'var(--border-mid)'
                }`,
              }}
            >
              <div className="flex items-center gap-2">
                {job.status === 'completed' ? (
                  <CheckCircle2 size={13} style={{ color: 'var(--success)', flexShrink: 0 }} />
                ) : job.status === 'failed' ? (
                  <AlertCircle size={13} style={{ color: 'var(--danger)', flexShrink: 0 }} />
                ) : (
                  <Loader2 size={13} className="animate-spin shrink-0" style={{ color: 'var(--eva-primary)' }} />
                )}
                <p className="text-xs font-medium truncate flex-1" style={{ color: 'var(--text-primary)' }}>
                  {job.filename}
                </p>
                {(job.status === 'completed' || job.status === 'failed') && (
                  <button onClick={() => setJobs(prev => prev.filter(j => j.jobId !== job.jobId))} style={{ color: 'var(--text-muted)' }}>
                    <X size={11} />
                  </button>
                )}
              </div>
              <p className="text-xs" style={{ color: job.status === 'failed' ? 'var(--danger)' : 'var(--text-muted)' }}>
                {job.status === 'queued'     ? 'Queued for processing…'
                : job.status === 'processing' ? `Processing… (${job.done}/${job.total} chunks)`
                : job.status === 'completed'  ? 'Indexed successfully'
                : job.error ? friendlyUploadError(job.error) : 'Ingestion failed'}
              </p>
              {job.status === 'processing' && job.total > 0 && (
                <div className="w-full rounded-full h-1.5 overflow-hidden" style={{ background: 'var(--bg-surface3)' }}>
                  <div className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${Math.round((job.done / job.total) * 100)}%`, background: 'var(--eva-primary)' }} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Stats row */}
      {!isLoading && knowledgeFiles.length > 0 && (
        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: 0 }}>
          {knowledgeFiles.length} file{knowledgeFiles.length !== 1 ? 's' : ''} indexed
          {totalBytes > 0 ? ` · ${totalSizeStr}` : ''}
        </p>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center gap-2 text-xs py-4" style={{ color: 'var(--text-muted)' }}>
          <Loader2 size={12} className="animate-spin" /> Loading…
        </div>
      )}

      {/* Empty state */}
      {!isLoading && filteredFiles.length === 0 && activeJobs.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 gap-3 text-center">
          <BookOpen size={28} style={{ color: 'var(--text-muted)', opacity: 0.4 }} />
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {search ? `No files match "${search}"` : 'No knowledge files yet.'}
          </p>
          {!search && (
            <p className="text-xs max-w-xs" style={{ color: 'var(--text-dim)', lineHeight: 1.6 }}>
              Upload PDFs, docs, or code files to give EVA persistent knowledge for this project.
              Workspace files are visible in the <strong>Files</strong> tab.
            </p>
          )}
        </div>
      )}

      {/* File groups */}
      {!isLoading && groups.map(({ label, style, groupFiles }) => {
        const isCollapsed = collapsed.has(label)
        const toggleSection = () => setCollapsed(prev => {
          const next = new Set(prev)
          if (next.has(label)) next.delete(label); else next.add(label)
          return next
        })
        return (
          <div key={label}>
            {/* Section header */}
            <button
              type="button"
              className="flex items-center gap-3 w-full mb-4"
              style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
              onClick={toggleSection}
            >
              <span style={{
                fontSize: 10.5, fontWeight: 700, letterSpacing: '0.1em',
                textTransform: 'uppercase', color: 'var(--text-muted)',
                whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 6,
              }}>
                {isCollapsed
                  ? <ChevronRight size={12} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
                  : <ChevronDown  size={12} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
                }
                {RAG_SECTION_NAME[label] ?? label}
                <span style={{ color: style.color }}>{groupFiles.length}</span>
              </span>
              <div style={{ flex: 1, height: 1, background: 'var(--border-dim)' }} />
            </button>

            {/* Icon card grid */}
            {!isCollapsed && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 10 }}>
                {groupFiles.map(f => {
                  const name        = bn(f.path)
                  const FIcon       = style.icon
                  const ext         = getExt(name).toUpperCase()
                  const sizeStr     = f.size >= 1024 * 1024
                    ? `${(f.size / (1024 * 1024)).toFixed(1)} MB`
                    : `${(f.size / 1024).toFixed(1)} KB`
                  const isConfirming = confirmFile === name
                  const isThisDeleting = isDeletingKn && deletingKnName === name
                  return (
                    <div
                      key={f.path}
                      className="relative flex flex-col items-center gap-2 rounded-xl p-3 group"
                      style={{
                        background: 'var(--bg-surface2)',
                        border: '1px solid var(--border-dim)',
                        transition: 'border-color 0.15s, background 0.15s',
                        cursor: f.storage_path && !isConfirming ? 'pointer' : 'default',
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = style.color + '44'; e.currentTarget.style.background = 'var(--bg-surface3)' }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-dim)'; e.currentTarget.style.background = 'var(--bg-surface2)' }}
                      onClick={async () => {
                        if (!f.storage_path || isConfirming) return
                        const msg = await openFile(f.storage_path)
                        setOpenToast(msg)
                        setTimeout(() => setOpenToast(null), 2000)
                      }}
                    >

                      {/* Delete hover button (top-right) */}
                      {!isConfirming && !isThisDeleting && (
                        <button
                          type="button"
                          className="absolute top-2 right-2 w-5 h-5 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 transition-opacity"
                          style={{ color: 'var(--text-muted)', background: 'rgba(0,0,0,0.3)', zIndex: 2 }}
                          onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
                          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
                          onClick={e => { e.stopPropagation(); setConfirmFile(name) }}
                        >
                          <Trash2 size={10} />
                        </button>
                      )}

                      {/* Delete confirm overlay */}
                      {isConfirming && (
                        <div
                          className="absolute inset-0 flex flex-col items-center justify-center gap-2.5"
                          style={{ background: 'rgba(30,36,50,0.97)', backdropFilter: 'blur(3px)', zIndex: 10, borderRadius: 'inherit' }}
                          onClick={e => e.stopPropagation()}
                        >
                          <Trash2 size={16} style={{ color: 'var(--danger)' }} />
                          <p style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>Delete file?</p>
                          <div className="flex gap-2">
                            <button
                              type="button"
                              className="btn danger"
                              style={{ fontSize: 11, padding: '4px 12px' }}
                              onClick={e => { e.stopPropagation(); deleteKnFile(name) }}
                            >Yes</button>
                            <button
                              type="button"
                              className="btn ghost"
                              style={{ fontSize: 11, padding: '4px 12px' }}
                              onClick={e => { e.stopPropagation(); setConfirmFile(null) }}
                            >No</button>
                          </div>
                        </div>
                      )}

                      {/* Icon area */}
                      <div
                        className="relative flex items-center justify-center rounded-2xl"
                        style={{ width: 68, height: 68, background: style.bg, marginTop: 8, flexShrink: 0 }}
                      >
                        <FIcon size={28} style={{ color: style.color }} />
                        {ext && !isThisDeleting && (
                          <span style={{
                            position: 'absolute', bottom: 5, right: 5,
                            fontSize: 7, fontWeight: 800, lineHeight: 1,
                            background: style.color + '28', color: style.color,
                            padding: '2px 3px', borderRadius: 3, letterSpacing: '0.03em',
                          }}>{ext}</span>
                        )}
                        {/* Deletion spinner */}
                        {isThisDeleting && (
                          <div className="absolute inset-0 flex items-center justify-center rounded-2xl"
                            style={{ background: 'rgba(6,9,15,0.72)' }}>
                            <Loader2 size={22} className="animate-spin" style={{ color: 'var(--danger)' }} />
                          </div>
                        )}
                      </div>

                      {/* Filename */}
                      <p style={{
                        fontSize: 11.5, fontWeight: 500, color: 'var(--text-primary)',
                        textAlign: 'center', lineHeight: 1.4, margin: 0,
                        overflow: 'hidden', display: '-webkit-box',
                        WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                        wordBreak: 'break-all', width: '100%',
                      }}>
                        {name}
                      </p>

                      {/* Size */}
                      <p style={{ fontSize: 10, color: 'var(--text-muted)', textAlign: 'center', lineHeight: 1.3, margin: 0 }}>
                        {sizeStr}
                      </p>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}

    </div>
  )
}
