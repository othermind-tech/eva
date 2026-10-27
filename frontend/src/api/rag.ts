import client, { get } from './client'
import type { RAGFile, UnifiedFileEntry, IngestJobResponse, IngestJobStatus } from '@/types/api'

// B-066 (V4.32): backend now returns UnifiedFileEntry[] with source field.
// Pass ?include_library=false for legacy RAG-only results.
interface RAGFilesResponse { files: UnifiedFileEntry[] }

export const ragApi = {
  // V4.1 (B-018): returns {job_id, status, filename} immediately — poll getIngestStatus for progress
  ingest: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return client.post<IngestJobResponse>('/rag/ingest', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then(r => r.data)
  },

  // V4.1 (B-018): poll ingestion job (503 = Redis unavailable → retry; 404 = expired)
  getIngestStatus: (jobId: string) =>
    get<IngestJobStatus>(`/rag/ingest/${jobId}/status`),

  // B-066 (V4.32): returns UnifiedFileEntry[] — mapped to RAGFile with source/size fields.
  // includeLibrary=false → ?include_library=false (legacy RAG-only view)
  listFiles: (includeLibrary = true) => {
    const params = includeLibrary ? {} : { include_library: 'false' }
    return client.get<RAGFilesResponse>('/rag/files', { params }).then(r =>
      (r.data?.files ?? []).map(f => ({
        file_name:   f.filename,
        source:      f.source,
        rag_indexed: f.rag_indexed,
        size_bytes:  f.size_bytes,
      } as RAGFile))
    )
  },

  deleteFile: (fileName: string) =>
    client.delete<void>('/rag/file', { data: { file_name: fileName } }).then(r => r.data),
}
