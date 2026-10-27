import { useState, useRef, useCallback } from 'react'
import { UploadCloud, X, FolderOpen, Plus, RotateCcw, Loader2, Wand2, Copy, AlertTriangle } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi } from '@/api/projects'
import type { ProjectFile } from '@/api/projects'
import { buildTree, FileTreeNode, copyPath } from '@/components/projects/projectFormat'

export default function ProjectFilesTab({ projectId }: { projectId: string }) {
  const qc = useQueryClient()
  const inputRef    = useRef<HTMLInputElement>(null)
  const modalInputRef = useRef<HTMLInputElement>(null)
  const [uploadOpen,  setUploadOpen]  = useState(false)
  const [dragging,    setDragging]    = useState(false)
  const [confirmFile, setConfirmFile] = useState<string | null>(null)
  const [jobs, setJobs] = useState<{ jobId: string; filename: string; status: string; done: number; total: number; error: string | null }[]>([])

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['project-files', projectId],
    queryFn:  () => projectsApi.listFiles(projectId),
    staleTime: 30_000,
  })

  const files: ProjectFile[]   = data?.files ?? []
  const workspaceRoot: string  = data?.workspace_root ?? ''

  // V6.2: deleteWorkspaceFile uses DELETE /projects/{id}/files with JSON body {path}
  const { mutate: deleteFile } = useMutation({
    mutationFn: (filePath: string) => projectsApi.deleteWorkspaceFile(projectId, filePath),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['project-files', projectId] }); setConfirmFile(null) },
    onError:   () => { setConfirmFile(null) },
  })

  // V6.2: workspace file upload is synchronous (200, no job_id) — no polling needed
  const uploadFiles = useCallback(async (fileList: FileList | File[]) => {
    setUploadOpen(false)
    for (const file of Array.from(fileList)) {
      const jobId = `upload-${Date.now()}-${file.name}`
      setJobs(prev => [{ jobId, filename: file.name, status: 'processing', done: 0, total: 0, error: null }, ...prev])
      try {
        await projectsApi.uploadFile(projectId, file)
        setJobs(prev => prev.map(j => j.jobId === jobId ? { ...j, status: 'completed' } : j))
        qc.invalidateQueries({ queryKey: ['project-files', projectId] })
      } catch {
        setJobs(prev => prev.map(j => j.jobId === jobId ? { ...j, status: 'failed', error: 'Upload failed' } : j))
      }
    }
    // Auto-dismiss completed jobs after 2 s
    setTimeout(() => setJobs(prev => prev.filter(j => j.status !== 'completed')), 2000)
  }, [projectId, qc])

  const activeJobs = jobs.filter(j => j.status !== 'completed')

  return (
    <div className="flex flex-col gap-3">

      {/* Upload modal */}
      {uploadOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
          onClick={() => setUploadOpen(false)}
        >
          <div
            className="flex flex-col gap-4 rounded-2xl p-6 w-[420px] max-w-[90vw]"
            style={{ background: 'var(--bg-surface1)', border: '1px solid var(--border-subtle)', boxShadow: '0 24px 64px rgba(0,0,0,0.5)' }}
            onClick={e => e.stopPropagation()}
          >
            {/* Modal header */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <UploadCloud size={16} style={{ color: 'var(--eva-primary)' }} />
                <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Add Files</span>
              </div>
              <button
                onClick={() => setUploadOpen(false)}
                className="w-7 h-7 flex items-center justify-center rounded-lg transition-colors"
                style={{ color: 'var(--text-muted)', background: 'var(--bg-surface2)' }}
                onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-primary)')}
                onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
              >
                <X size={14} />
              </button>
            </div>

            {/* Drop zone */}
            <div
              onDragOver={e => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={e => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files) }}
              className="flex flex-col items-center justify-center gap-3 py-10 rounded-xl cursor-pointer transition-all"
              style={{
                border: `2px dashed ${dragging ? 'var(--eva-primary)' : 'var(--border-dim)'}`,
                background: dragging ? 'rgba(0,201,255,0.06)' : 'var(--bg-base)',
              }}
            >
              <UploadCloud size={32} style={{ color: dragging ? 'var(--eva-primary)' : 'var(--text-muted)', transition: 'color 0.15s' }} />
              <div className="text-center">
                <p className="text-sm font-medium" style={{ color: dragging ? 'var(--eva-primary)' : 'var(--text-primary)' }}>
                  {dragging ? 'Release to upload' : 'Drop files here'}
                </p>
                <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>PDFs, code, docs, images — any file type</p>
              </div>
            </div>

            {/* Browse button */}
            <button
              onClick={() => modalInputRef.current?.click()}
              className="flex items-center justify-center gap-2 py-2.5 rounded-xl font-medium text-sm transition-all"
              style={{ background: 'rgba(0,201,255,0.1)', color: 'var(--eva-primary)', border: '1px solid rgba(0,201,255,0.25)' }}
              onMouseEnter={e => (e.currentTarget.style.background = 'rgba(0,201,255,0.18)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'rgba(0,201,255,0.1)')}
            >
              <FolderOpen size={15} />
              Browse files…
            </button>
            <input ref={modalInputRef} type="file" multiple className="hidden" onChange={e => e.target.files && uploadFiles(e.target.files)} />
          </div>
        </div>
      )}

      {/* Workspace root — same po-path pill as Project Overview */}
      {workspaceRoot && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <div className="po-synopsis-label">Server Path</div>
          <button
            className="po-path"
            type="button"
            title="Click to copy"
            aria-label={`Copy server path ${workspaceRoot}`}
            style={{ alignSelf: 'flex-start', maxWidth: '100%' }}
            onClick={() => copyPath(workspaceRoot)}
          >
            <FolderOpen size={11} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{workspaceRoot}</span>
            <Copy size={10} className="po-copy" />
          </button>
        </div>
      )}

      {/* Action bar — always at top */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setUploadOpen(true)}
          className="flex flex-1 items-center justify-center gap-2 py-2.5 rounded-xl font-medium text-sm transition-all"
          style={{ background: 'rgba(0,201,255,0.1)', color: 'var(--eva-primary)', border: '1px solid rgba(0,201,255,0.25)' }}
          onMouseEnter={e => (e.currentTarget.style.background = 'rgba(0,201,255,0.18)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'rgba(0,201,255,0.1)')}
        >
          <Plus size={15} />
          Add Files
        </button>
        <button
          onClick={() => refetch()}
          className="w-10 h-10 flex items-center justify-center rounded-xl shrink-0 transition-colors"
          style={{ border: '1px solid var(--border-dim)', background: 'var(--bg-surface1)', color: 'var(--text-muted)' }}
          title="Refresh file list"
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--eva-primary)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
        >
          <RotateCcw size={13} />
        </button>
      </div>
      <input ref={inputRef} type="file" multiple className="hidden" onChange={e => e.target.files && uploadFiles(e.target.files)} />

      {/* In-flight jobs */}
      {activeJobs.map(job => (
        <div key={job.jobId} className="px-3 py-2 rounded-xl flex items-center gap-2" style={{ background: 'var(--bg-surface1)', border: '1px solid var(--border-dim)' }}>
          {job.status === 'failed'
            ? <Wand2 size={11} style={{ color: 'var(--danger)', flexShrink: 0 }} />
            : <Loader2 size={11} className="animate-spin shrink-0" style={{ color: 'var(--eva-primary)' }} />}
          <span className="text-xs flex-1 truncate" style={{ color: 'var(--text-primary)' }}>{job.filename}</span>
          <span className="text-xs shrink-0" style={{ color: 'var(--text-muted)', fontSize: 10 }}>
            {job.status === 'failed' ? 'Failed' : job.status === 'queued' ? 'Queued…' : job.status === 'processing' ? 'Uploading…' : `${job.total > 0 ? Math.round((job.done / job.total) * 100) : 0}%`}
          </span>
        </div>
      ))}

      {isLoading && <div className="flex items-center gap-2 text-xs py-2" style={{ color: 'var(--text-muted)' }}><Loader2 size={12} className="animate-spin" /> Loading…</div>}

      {!isLoading && files.length === 0 && activeJobs.length === 0 && (
        <p className="text-xs text-center py-4" style={{ color: 'var(--text-muted)' }}>No files yet. Agent-created files and uploads appear here.</p>
      )}

      {/* Section 105 - the workspace has more files than the server will list */}
      {data?.truncated && (
        <div
          className="flex items-start gap-2 px-3 py-2 rounded-xl text-xs"
          style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', color: 'var(--amber)' }}
        >
          <AlertTriangle size={12} className="shrink-0" style={{ marginTop: 1 }} />
          <span>{data.truncated_message ?? 'This workspace is very large, so only part of it is listed.'}</span>
        </div>
      )}

      {/* File tree */}
      {files.length > 0 && (
        <div
          className="rounded-xl overflow-hidden"
          style={{ background: 'var(--bg-surface1)', border: '1px solid var(--border-dim)', padding: '6px 4px' }}
        >
          {buildTree(files).map((node, i) => (
            <FileTreeNode
              key={i} node={node} depth={0}
              workspaceRoot={workspaceRoot}
              onDelete={path => deleteFile(path)}
              confirmFile={confirmFile}
              setConfirmFile={setConfirmFile}
            />
          ))}
        </div>
      )}
    </div>
  )
}
