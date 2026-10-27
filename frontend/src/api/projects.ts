import client, { get, post, patch } from './client'
import type { ProjectAgent, ProjectDaemon, IngestJobStatus, ProjectMetricsResponse } from '@/types/api'

// ── Project file types (Section 73 / V6.2 — File/Knowledge Split) ────────────
export interface ProjectFile {
  path:          string              // relative path e.g. "src/main.py"
  source:        'knowledge' | 'uploaded' | 'workspace'  // V6.2: added 'knowledge'
  purpose:       'knowledge' | 'workspace'               // V6.2: NEW field
  size:          number              // bytes
  modified_at:   string              // ISO 8601 UTC
  storage_path?: string              // absolute OS path — present in Electron, undefined in browser
}

export interface ProjectFilesResponse {
  files:          ProjectFile[]
  total:          number
  workspace_root: string           // absolute server path — display only
  // Section 105 (V6.60) — set only when the project's WORKSPACE has more files than the
  // server's limit (50 000 by default) and the listing was cut at it. Knowledge and
  // uploaded files are never cut. Absent/false for ordinary projects.
  truncated?:         boolean
  truncated_message?: string | null
}

// V6.2 response types
export interface KnowledgeUploadResponse {
  job_id:     string
  status:     'queued'
  filename:   string
  project_id: string
}

export interface WorkspaceFileUploadResponse {
  status:      'ok'
  filename:    string
  stored_path: string
  size:        number
}

export interface DeleteKnowledgeResponse {
  status:     'ok'
  filename:   string
  project_id: string
  deleted: { vectors_deleted: boolean; blob_deleted: boolean }
}

export interface DeleteWorkspaceFileResponse {
  status:       'ok'
  deleted_path: string
}

// Section 83 — manual project resync (Phase C)
export interface ProjectSyncResponse {
  triggered:   boolean
  project_id:  string
  execution_id: string
  note:        string
}

// ── Types ────────────────────────────────────────────────────
export type ProjectStatus = 'active' | 'paused' | 'archived'

export interface ProjectCreate {
  name:            string
  synopsis:        string
  description?:    string
  workspace_path?: string   // optional — omit for managed workspace
}

export interface Project {
  id:             string
  name:           string
  synopsis:       string
  description?:   string
  status:         ProjectStatus
  workspace_path: string | null
  is_external:    boolean
  created_at:     string
  updated_at?:    string
}

export type ArtifactType = 'tasks' | 'knowledge' | 'policies' | 'journal' | 'errors' | 'project'

// ── API client ────────────────────────────────────────────────
export const projectsApi = {
  list:   () => get<Project[]>('/projects'),
  get:    (id: string) => get<Project>(`/projects/${id}`),
  create: (data: ProjectCreate) => post<Project>('/projects', data),
  update: (id: string, data: Partial<ProjectCreate> & { status?: ProjectStatus; workspace_path?: string | null }) =>
    patch<Project>(`/projects/${id}`, data),
  sync:   (id: string) => post<ProjectSyncResponse>(`/projects/${id}/sync`),

  /** Fetch one .eva workspace artifact as raw text */
  getArtifact: (id: string, type: ArtifactType) =>
    get<string>(`/projects/${id}/artifacts/${type}`),

  /** List conversations linked to this project */
  listConversations: (id: string) =>
    get<{ id: string; title?: string; created_at: string }[]>(`/projects/${id}/conversations`),

  /** List agents scoped to this project (B-056) */
  listAgents: (id: string) =>
    get<ProjectAgent[]>(`/projects/${id}/agents`),

  /** List daemons scoped to this project (B-056) */
  listDaemons: (id: string) =>
    get<ProjectDaemon[]>(`/projects/${id}/daemons`),

  // ── V6.2 — Project Knowledge (RAG-indexed) ───────────────────

  /** Upload a file for RAG indexing into this project's knowledge scope.
   *  POST /projects/{id}/knowledge → 202 + job_id; poll getIngestStatus.
   *  Throws 415 if file type cannot be text-extracted (binary files). */
  uploadKnowledge: (projectId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return client.post<KnowledgeUploadResponse>(
      `/projects/${projectId}/knowledge`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    ).then(r => r.data)
  },

  /** Delete a knowledge file by bare filename (no path separators).
   *  DELETE /projects/{id}/knowledge/{filename} */
  deleteKnowledgeFile: (projectId: string, filename: string) =>
    client.delete<DeleteKnowledgeResponse>(
      `/projects/${projectId}/knowledge/${encodeURIComponent(filename)}`,
    ).then(r => r.data),

  // ── V6.2 — Project Workspace Files ───────────────────────────

  /** Upload a file into this project's workspace (blob storage, no RAG).
   *  POST /projects/{id}/files → 200 sync; no job_id polling required.
   *  Optional targetPath places the file under a subfolder (e.g. "data/report.csv"). */
  uploadFile: (projectId: string, file: File, targetPath?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (targetPath) form.append('path', targetPath)
    return client.post<WorkspaceFileUploadResponse>(
      `/projects/${projectId}/files`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    ).then(r => r.data)
  },

  /** Delete a workspace file by relative path.
   *  DELETE /projects/{id}/files  body: { path } */
  deleteWorkspaceFile: (projectId: string, filePath: string) =>
    client.delete<DeleteWorkspaceFileResponse>(
      `/projects/${projectId}/files`,
      { data: { path: filePath } },
    ).then(r => r.data),

  // ── Shared ────────────────────────────────────────────────────

  /** List all files in this project's workspace (knowledge + uploaded + workspace).
   *  V6.2: each item now has source ('knowledge'|'uploaded'|'workspace') + purpose field. */
  listFiles: (projectId: string) =>
    // Section 105: a workspace at the server's 50 000-file ceiling takes 30-40 s to list on a
    // typical Windows disk (and returns ~14 MB), longer than the app-wide 30 s timeout - so the
    // request died before the "truncated" banner could ever show. Per-call override, as
    // mcpApi.update() does; every other endpoint keeps the 30 s default.
    get<ProjectFilesResponse>(`/projects/${projectId}/files`, { timeout: 120_000 }),

  /** Poll ingestion job status (shared with global /rag/ingest) */
  getIngestStatus: (jobId: string) =>
    get<IngestJobStatus>(`/rag/ingest/${jobId}/status`),

  /** Section 70 — one-shot project metrics overview (tokens, cost, time, executions, daemons, tasks) */
  getMetrics: (projectId: string) =>
    get<ProjectMetricsResponse>(`/projects/${projectId}/metrics`),
}
