import { Tag, Star, Briefcase, Cpu, Globe, BookOpen, Heart, Lightbulb, Brain } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export interface CatStyle {
  icon:  LucideIcon
  color: string
  bg:    string
}

export const CAT_STYLES: Record<string, CatStyle> = {
  skills:       { icon: Cpu,       color: 'var(--eva-primary)', bg: 'rgba(0,201,255,0.12)'   },
  preferences:  { icon: Star,      color: 'var(--amber)',       bg: 'rgba(245,158,11,0.12)'  },
  work:         { icon: Briefcase, color: 'var(--neural)',      bg: 'rgba(124,108,243,0.12)' },
  personal:     { icon: Heart,     color: 'var(--danger)',      bg: 'rgba(255,77,109,0.10)'  },
  knowledge:    { icon: BookOpen,  color: 'var(--success)',     bg: 'rgba(0,212,160,0.10)'   },
  ideas:        { icon: Lightbulb, color: 'var(--amber)',       bg: 'rgba(245,158,11,0.10)'  },
  global:       { icon: Globe,     color: 'var(--neural)',      bg: 'rgba(124,108,243,0.10)' },
}

const ALL_CAT_STYLE: CatStyle = {
  icon:  Brain,
  color: 'var(--neural)',
  bg:    'rgba(124,108,243,0.12)',
}

const DEFAULT_CAT_STYLE: CatStyle = {
  icon:  Tag,
  color: 'var(--neural)',
  bg:    'rgba(124,108,243,0.10)',
}

export function getCatStyle(cat: string): CatStyle {
  if (cat === 'all') return ALL_CAT_STYLE
  return CAT_STYLES[cat.toLowerCase()] ?? DEFAULT_CAT_STYLE
}
