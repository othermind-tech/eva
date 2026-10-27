import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Merge Tailwind classes safely */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Format ISO date to readable relative or absolute string */
export function formatDate(iso: string): string {
  const d   = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMins = Math.floor(diffMs / 60_000)
  const diffHours = Math.floor(diffMs / 3_600_000)
  const diffDays = Math.floor(diffMs / 86_400_000)

  if (diffMins < 1)    return 'just now'
  if (diffMins < 60)   return `${diffMins}m ago`
  if (diffHours < 24)  return `${diffHours}h ago`
  if (diffDays < 7)    return `${diffDays}d ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/** Truncate string to max length with ellipsis */
export function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + '…'
}

/** Format token counts for display */
export function formatTokens(n: number): string {
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

/** Format byte counts for display (e.g. 1.4 MB, 32 KB) */
export function formatBytes(n?: number | null): string {
  if (n == null || n < 0) return '—'
  if (n === 0) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1_048_576) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1_073_741_824) return `${(n / 1_048_576).toFixed(1)} MB`
  return `${(n / 1_073_741_824).toFixed(2)} GB`
}

/**
 * Open a file with the OS default application (Electron) or copy path to clipboard (browser).
 * In Electron: calls window.electronAPI.openPath → shell.openPath (PDF → viewer, etc.)
 * In browser/dev: copies the absolute path to clipboard as a fallback.
 * Returns a short user-facing message describing what happened.
 */
export async function openFile(fullPath: string): Promise<string> {
  if (window.electronAPI?.openPath) {
    await window.electronAPI.openPath(fullPath)
    return 'Opened'
  }
  try {
    await navigator.clipboard.writeText(fullPath)
    return `Path copied`
  } catch {
    return 'Cannot open — path copied'
  }
}

