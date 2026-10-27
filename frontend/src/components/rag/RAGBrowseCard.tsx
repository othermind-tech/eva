import { useEffect, useRef, useState } from 'react'
import { Loader2, ChevronRight, ChevronDown, Search, X, Trash2, BookOpen, Upload, CheckCircle2, AlertCircle } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ragApi } from '@/api/rag'
import type { RAGFile, IngestJobStatus } from '@/types/api'
import { openFile } from '@/lib/utils'
import { friendlyUploadError } from '@/lib/errors'
import { FILE_TYPE_STYLES, getFileGroup, getFileTypeStyle as getFileTypeStyleForFile, getExt, RAG_SECTION_NAME } from '@/lib/ragFileTypes'
import { RAG_POLL_INTERVAL_MS, RAG_POLL_MAX_RETRIES } from '@/lib/constants'

// ── Knowledge Base — full view (header + upload + filter tiles + file list) ───
interface RagUploadJob {
  filename:    string
  status:      IngestJobStatus['status']
  chunksDone:  number
  totalChunks: number
  error:       string | null
}

export default function RAGBrowseCard() {
  const qc      = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [confirmFile, setConfirmFile] = useState<string | null>(null)
  const [uploadJob,   setUploadJob]   = useState<RagUploadJob | null>(null)
  const [search,      setSearch]      = useState('')
  const [collapsed,   setCollapsed]   = useState<Set<string>>(new Set())
  const [openToast,   setOpenToast]   = useState<string | null>(null)

  const { data: files = [], isLoading } = useQuery<RAGFile[]>({
    queryKey: ['rag-files'],
    queryFn:  () => ragApi.listFiles(),
  })

  // Auto-dismiss completed banner after 3 s
  useEffect(() => {
    if (uploadJob?.status !== 'completed') return
    const t = setTimeout(() => setUploadJob(null), 3000)
    return () => clearTimeout(t)
  }, [uploadJob?.status])

  // Upload mutation — POST file, then poll job status
  const { mutate: ingest, isPending: uploading } = useMutation({
    mutationFn: (file: File) => ragApi.ingest(file),
    onSuccess: ({ job_id, filename }) => {
      setUploadJob({ filename, status: 'queued', chunksDone: 0, totalChunks: 0, error: null })
      pollJob(job_id)
    },
    onError: () => {
      setUploadJob(prev => prev ? { ...prev, status: 'failed', error: 'Upload failed' } : null)
    },
  })

  const pollJob = (jobId: string, attempt = 0) => {
    if (pollRef.current) clearTimeout(pollRef.current)
    if (attempt >= RAG_POLL_MAX_RETRIES) {
      setUploadJob(prev => prev ? { ...prev, status: 'failed', error: 'Timed out waiting for ingestion' } : null)
      return
    }
    pollRef.current = setTimeout(async () => {
      try {
        const s = await ragApi.getIngestStatus(jobId)
        setUploadJob({ filename: s.filename, status: s.status, chunksDone: s.chunks_done, totalChunks: s.total_chunks, error: s.error })
        if (s.status === 'completed') {
          qc.invalidateQueries({ queryKey: ['rag-files'] })
        } else if (s.status !== 'failed') {
          pollJob(jobId, attempt + 1)
        }
      } catch {
        pollJob(jobId, attempt + 1)
      }
    }, RAG_POLL_INTERVAL_MS)
  }

  const { mutate: deleteFile, isPending: isDeleting, variables: deletingName } = useMutation({
    mutationFn: (name: string) => ragApi.deleteFile(name),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['rag-files'] }),
    onError:    () => { /* overlay already closed; card stays visible */ },
  })

  const handleFileUpload = (file: File) => { setUploadJob(null); ingest(file) }

  // Header subtitle: file count + total size
  // size_bytes is optional on UnifiedFileEntry; fall back to file_size if present
  const totalBytes   = files.reduce((s, f) => s + (f.size_bytes ?? f.file_size ?? 0), 0)
  const totalSizeStr = totalBytes >= 1024 * 1024
    ? `${(totalBytes / (1024 * 1024)).toFixed(1)} MB`
    : `${(totalBytes / 1024).toFixed(1)} KB`

  // Search filter
  const searchLc      = search.toLowerCase().trim()
  const filteredFiles = searchLc
    ? files.filter(f => f.file_name.toLowerCase().includes(searchLc))
    : files

  // Group by file type (preserves FILE_TYPE_STYLES order, then Other)
  const knownLabels = new Set(FILE_TYPE_STYLES.map(s => s.label))
  const otherFiles  = filteredFiles.filter(f => !knownLabels.has(getFileGroup(f.file_name)))
  const groups = [
    ...FILE_TYPE_STYLES.map(style => ({
      label:      style.label,
      style,
      groupFiles: filteredFiles.filter(f => getFileGroup(f.file_name) === style.label),
    })).filter(g => g.groupFiles.length > 0),
    ...(otherFiles.length > 0 ? [{
      label:      'Other',
      style:      getFileTypeStyleForFile('unknown.bin'),
      groupFiles: otherFiles,
    }] : []),
  ]

  const uploadPct   = uploadJob && uploadJob.totalChunks > 0
    ? Math.round((uploadJob.chunksDone / uploadJob.totalChunks) * 100) : 0
  const uploadLabel = !uploadJob ? null
    : uploadJob.status === 'queued'     ? 'Queued for processing…'
    : uploadJob.status === 'processing' ? `Processing… (${uploadJob.chunksDone}/${uploadJob.totalChunks} chunks)`
    : uploadJob.status === 'completed'  ? 'Indexed successfully'
    : uploadJob.error ? friendlyUploadError(uploadJob.error) : 'Ingestion failed'

  return (
    <div className="flex flex-col h-full">

      {/* Header */}
      <div className="detail-header">
        <div className="dh-icon" style={{ background: 'rgba(0,201,255,0.08)', borderColor: 'rgba(0,201,255,0.2)', color: 'var(--eva-primary)' }}>
          <BookOpen size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="dh-title">Knowledge Base</h2>
          <p className="dh-sub">
            {isLoading ? '…' : `${files.length} file${files.length !== 1 ? 's' : ''} indexed${totalBytes > 0 ? ` · ${totalSizeStr}` : ''}`}
          </p>
        </div>
        <div className="detail-actions">
          <button
            className="btn primary"
            style={{ fontSize: 12, gap: 5 }}
            onClick={() => !uploading && fileRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            Upload
          </button>
        </div>
      </div>

      <input
        ref={fileRef}
        type="file"
        className="hidden"
        accept=".pdf,.docx,.txt,.md"
        onChange={e => { const f = e.target.files?.[0]; if (f) handleFileUpload(f) }}
      />

      {/* Open-file toast */}
      {openToast && (
        <div
          className="pointer-events-none absolute bottom-6 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-xl text-xs whitespace-nowrap"
          style={{ background: 'var(--bg-surface3)', border: '1px solid var(--border-mid)', color: 'var(--text-secondary)', boxShadow: '0 4px 20px rgba(0,0,0,0.5)' }}
        >
          {openToast}
        </div>
      )}

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto">
        <div className="flex flex-col gap-5 px-6 py-4">

          {/* Search bar */}
          <div className="search-field">
            <Search size={13} />
            <input
              placeholder="Search knowledge base…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            {search && (
              <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
                <X size={11} />
              </button>
            )}
          </div>

          {/* Upload progress banner */}
          {uploadJob && (
            <div
              className="rounded-xl p-3 flex flex-col gap-2"
              style={{
                background: 'var(--bg-surface2)',
                border: `1px solid ${
                  uploadJob.status === 'completed' ? 'rgba(0,212,160,0.3)'
                  : uploadJob.status === 'failed'  ? 'rgba(255,77,109,0.3)'
                  : 'var(--border-mid)'
                }`,
              }}
            >
              <div className="flex items-center gap-2">
                {uploadJob.status === 'completed' ? (
                  <CheckCircle2 size={13} style={{ color: 'var(--success)', flexShrink: 0 }} />
                ) : uploadJob.status === 'failed' ? (
                  <AlertCircle size={13} style={{ color: 'var(--danger)', flexShrink: 0 }} />
                ) : (
                  <Loader2 size={13} className="animate-spin shrink-0" style={{ color: 'var(--eva-primary)' }} />
                )}
                <p className="text-xs font-medium truncate flex-1" style={{ color: 'var(--text-primary)' }}>
                  {uploadJob.filename}
                </p>
                {(uploadJob.status === 'completed' || uploadJob.status === 'failed') && (
                  <button onClick={() => setUploadJob(null)} style={{ color: 'var(--text-muted)' }}>
                    <X size={11} />
                  </button>
                )}
              </div>
              <p className="text-xs" style={{ color: uploadJob.status === 'failed' ? 'var(--danger)' : 'var(--text-muted)' }}>
                {uploadLabel}
              </p>
              {uploadJob.status === 'processing' && uploadJob.totalChunks > 0 && (
                <div className="w-full rounded-full h-1.5 overflow-hidden" style={{ background: 'var(--bg-surface3)' }}>
                  <div className="h-full rounded-full transition-all duration-500" style={{ width: `${uploadPct}%`, background: 'var(--eva-primary)' }} />
                </div>
              )}
            </div>
          )}

          {/* Loading */}
          {isLoading && (
            <div className="flex items-center justify-center py-16 gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
              <Loader2 size={14} className="animate-spin" /> Loading files…
            </div>
          )}

          {/* Empty state */}
          {!isLoading && filteredFiles.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
              <BookOpen size={32} style={{ color: 'var(--text-muted)', opacity: 0.4 }} />
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
                {search ? `No files match "${search}"` : 'No files ingested yet.'}
              </p>
              {!search && (
                <p className="text-xs max-w-xs" style={{ color: 'var(--text-dim)', lineHeight: 1.6 }}>
                  Click Upload to add PDFs, DOCX, TXT, or Markdown files.
                </p>
              )}
            </div>
          )}

          {/* File groups — each type gets a labelled section + icon grid */}
          {!isLoading && groups.map(({ label, style, groupFiles }) => {
            const isCollapsed = collapsed.has(label)
            const toggleSection = () => setCollapsed(prev => {
              const next = new Set(prev)
              if (next.has(label)) next.delete(label); else next.add(label)
              return next
            })
            return (
            <div key={label}>

              {/* Section header — clickable to collapse */}
              <button
                type="button"
                className="flex items-center gap-3 w-full mb-4 group/sec"
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
              {!isCollapsed && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 10 }}>
                {groupFiles.map(f => {
                  const FIcon        = style.icon
                  const ext          = getExt(f.file_name).toUpperCase()
                  const isConfirming = confirmFile === f.file_name
                  const fileSz       = f.size_bytes ?? f.file_size ?? null
                  const sizeStr      = fileSz != null
                    ? fileSz >= 1024 * 1024
                      ? `${(fileSz / (1024 * 1024)).toFixed(1)} MB`
                      : `${(fileSz / 1024).toFixed(1)} KB`
                    : null
                  return (
                    <div
                      key={f.file_name}
                      className="group relative flex flex-col items-center gap-2 rounded-xl p-3"
                      style={{
                        background: 'var(--bg-surface2)',
                        border: '1px solid var(--border-dim)',
                        transition: 'border-color 0.15s, background 0.15s',
                        overflow: 'hidden',
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
                      {/* Library badge — top-left (hidden when confirm overlay is showing) */}
                      {f.source === 'library' && !isConfirming && (
                        <span className="absolute" style={{
                          top: 7, left: 7,
                          fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
                          padding: '1px 5px', borderRadius: 99,
                          background: 'rgba(124,108,243,0.15)', color: 'var(--neural)',
                          border: '1px solid rgba(124,108,243,0.3)',
                        }}>lib</span>
                      )}

                      {/* Hover-reveal delete trigger */}
                      {!isConfirming && (
                        <button
                          className="icon-btn absolute opacity-0 group-hover:opacity-100 transition-opacity"
                          style={{ top: 7, right: 7, width: 20, height: 20, padding: 0, borderRadius: 5, zIndex: 5 }}
                          title="Delete file"
                          onClick={e => { e.stopPropagation(); setConfirmFile(f.file_name) }}
                        ><Trash2 size={10} /></button>
                      )}

                      {/* Full-card confirm overlay */}
                      {isConfirming && (
                        <div
                          className="absolute inset-0 flex flex-col items-center justify-center gap-2.5"
                          style={{ background: 'rgba(30,36,50,0.97)', backdropFilter: 'blur(3px)', zIndex: 10 }}
                          onClick={e => e.stopPropagation()}
                        >
                          <Trash2 size={16} style={{ color: 'var(--danger)' }} />
                          <p style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>Delete file?</p>
                          <div className="flex gap-2">
                            <button
                              type="button"
                              className="btn danger"
                              style={{ fontSize: 11, padding: '4px 12px' }}
                              onClick={e => { e.stopPropagation(); setConfirmFile(null); deleteFile(f.file_name) }}
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
                        {/* Extension badge — bottom-right of icon */}
                        {ext && (
                          <span style={{
                            position: 'absolute', bottom: 5, right: 5,
                            fontSize: 7, fontWeight: 800, lineHeight: 1,
                            background: style.color + '28', color: style.color,
                            padding: '2px 3px', borderRadius: 3, letterSpacing: '0.03em',
                          }}>{ext}</span>
                        )}
                        {/* Deletion in-progress spinner */}
                        {isDeleting && deletingName === f.file_name && (
                          <div className="absolute inset-0 flex items-center justify-center rounded-2xl"
                            style={{ background: 'rgba(6,9,15,0.72)' }}>
                            <Loader2 size={22} className="animate-spin" style={{ color: 'var(--danger)' }} />
                          </div>
                        )}
                      </div>

                      {/* Filename — 2-line clamp */}
                      <p style={{
                        fontSize: 11.5, fontWeight: 500, color: 'var(--text-primary)',
                        textAlign: 'center', lineHeight: 1.4, margin: 0,
                        overflow: 'hidden', display: '-webkit-box',
                        WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                        wordBreak: 'break-all', width: '100%',
                      }}>
                        {f.file_name}
                      </p>

                      {/* Meta — size · chunks */}
                      {(sizeStr || f.chunk_count != null) && (
                        <p style={{
                          fontSize: 10, color: 'var(--text-muted)',
                          textAlign: 'center', lineHeight: 1.3, margin: 0,
                        }}>
                          {[sizeStr, f.chunk_count != null ? `${f.chunk_count} chunks` : null].filter(Boolean).join(' · ')}
                        </p>
                      )}
                    </div>
                  )
                })}
              </div>}
            </div>
          )
          })}


        </div>
      </div>
    </div>
  )
}
