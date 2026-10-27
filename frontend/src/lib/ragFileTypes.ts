import type { LucideIcon } from 'lucide-react'
import { FileText, FileCode, FileImage, FileSpreadsheet, File } from 'lucide-react'

export interface FileTypeStyle {
  icon:  LucideIcon
  color: string
  bg:    string
  label: string
  exts:  string[]   // lowercase extensions that belong to this group
}

export const FILE_TYPE_STYLES: FileTypeStyle[] = [
  {
    label: 'PDF',
    exts:  ['pdf'],
    icon:  FileText,
    color: 'var(--danger)',
    bg:    'rgba(255,77,109,0.12)',
  },
  {
    label: 'Word',
    exts:  ['doc', 'docx'],
    icon:  FileText,
    color: 'var(--eva-primary)',
    bg:    'rgba(0,201,255,0.12)',
  },
  {
    label: 'Text',
    exts:  ['txt', 'md', 'markdown', 'rst'],
    icon:  FileText,
    color: 'var(--success)',
    bg:    'rgba(0,212,160,0.10)',
  },
  {
    label: 'Code',
    exts:  ['py', 'js', 'ts', 'tsx', 'jsx', 'json', 'yaml', 'yml', 'toml', 'csv'],
    icon:  FileCode,
    color: 'var(--neural)',
    bg:    'rgba(124,108,243,0.12)',
  },
  {
    label: 'Sheet',
    exts:  ['xls', 'xlsx'],
    icon:  FileSpreadsheet,
    color: 'var(--amber)',
    bg:    'rgba(245,158,11,0.12)',
  },
  {
    label: 'Image',
    exts:  ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'],
    icon:  FileImage,
    color: 'var(--neural)',
    bg:    'rgba(124,108,243,0.10)',
  },
]

const OTHER_FILE_STYLE: FileTypeStyle = {
  label: 'Other',
  exts:  [],
  icon:  File,
  color: 'var(--text-muted)',
  bg:    'var(--bg-surface2)',
}

/** Get the ext from a filename */
export function getExt(filename: string): string {
  const parts = filename.split('.')
  return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : ''
}

/** Get the FileTypeStyle for a given filename */
export function getFileTypeStyle(filename: string): FileTypeStyle {
  const ext = getExt(filename)
  return FILE_TYPE_STYLES.find(s => s.exts.includes(ext)) ?? OTHER_FILE_STYLE
}

/** Get the group label key used as ragFilter value */
export function getFileGroup(filename: string): string {
  const ext = getExt(filename)
  return FILE_TYPE_STYLES.find(s => s.exts.includes(ext))?.label ?? 'Other'
}

// Section display names for each file group — shared by RAGBrowseCard and
// ProjectKnowledgeTab (both render the same file-group sections).
export const RAG_SECTION_NAME: Record<string, string> = {
  PDF:   'PDF Documents',
  Word:  'Word Documents',
  Text:  'Markdown & Text',
  Code:  'Code Files',
  Sheet: 'Spreadsheets',
  Image: 'Images',
  Other: 'Other Files',
}
