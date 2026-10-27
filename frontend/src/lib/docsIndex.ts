import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

export interface DocPage {
  id:    string  // path relative to public/docs, no extension
  title: string
}

export interface DocSection {
  id:    string
  title: string
  pages: DocPage[]
}

// The doc index is no longer hardcoded here — docs/manifest.json is generated
// at build time alongside the docs themselves and copied into the served
// bundle by vite.config.ts (viteStaticCopy), so adding/renaming a page
// can't drift from what this file says exists.
const MANIFEST_URL = '/docs/manifest.json'

async function fetchManifest(): Promise<DocSection[]> {
  const res = await fetch(MANIFEST_URL)
  if (!res.ok) throw new Error(`Failed to load ${MANIFEST_URL}: ${res.status}`)
  return res.json()
}

const EMPTY: DocSection[] = []

export function useDocsIndex(): {
  sections: DocSection[]
  allPages: DocPage[]
  isLoading: boolean
  isError: boolean
} {
  // staleTime: Infinity — the manifest is baked into the build, it can't
  // change under a running app, so there's nothing to ever refetch.
  const { data, isLoading, isError } = useQuery({
    queryKey: ['docs-manifest'],
    queryFn: fetchManifest,
    staleTime: Infinity,
  })
  const sections = data ?? EMPTY
  const allPages = useMemo(() => sections.flatMap(s => s.pages), [sections])
  return { sections, allPages, isLoading, isError }
}
