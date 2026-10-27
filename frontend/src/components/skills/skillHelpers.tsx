import { FileText, FileCode, File } from 'lucide-react'

// ── Skill helpers ─────────────────────────────────────────────────────────────
export function fileIcon(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase()
  if (ext === 'md' || ext === 'txt') return <FileText size={14} />
  if (ext === 'yaml' || ext === 'yml') return <FileCode size={14} />
  if (ext === 'json') return <FileText size={14} />
  return <File size={14} />
}

// Updates allowed_tools in SKILL.md YAML frontmatter (handles block & inline list)
export function setFrontmatterTools(content: string, tools: string[]): string {
  const match = content.match(/^(---\n)([\s\S]*?)(\n---)([\s\S]*)$/)
  if (!match) return content
  const [, open, fm, close, rest] = match
  const lines = fm.split('\n')
  const kept: string[] = []
  let skipping = false
  for (const line of lines) {
    if (/^(allowed_tools|tools)\s*:/.test(line)) { skipping = true; continue }
    if (skipping && /^\s+-\s/.test(line)) continue
    skipping = false
    kept.push(line)
  }
  if (tools.length > 0) {
    kept.push('allowed_tools:')
    tools.forEach(t => kept.push(`  - ${t}`))
  }
  return `${open}${kept.join('\n')}${close}${rest}`
}
