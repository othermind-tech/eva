import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { SessionConfig } from '@/types/api'

export type SessionSaveStatus = 'idle' | 'saving' | 'saved' | 'error'

interface SessionState {
  config: SessionConfig
  isDirty: boolean           // local changes not yet pushed to server
  saveStatus: SessionSaveStatus   // driven by useSessionAutoSave — read-only for UI
  activeProjectId: string | null   // project linked to new conversations
  activeProjectName: string | null
  setConfig: (patch: Partial<SessionConfig>) => void
  replaceConfig: (c: SessionConfig) => void
  markClean: () => void
  setSaveStatus: (status: SessionSaveStatus) => void
  setActiveProject: (id: string | null, name: string | null) => void
}

const defaultConfig: SessionConfig = {
  // No provider/model default (V6.22 / Section 82) — "not configured" is a
  // real, honest state for a brand-new account, not a value to fake with a
  // hardcoded guess. GET /session replaces this with the real value on load.
  tools_enabled:       [],
  tool_approval_mode:  'auto',
  agent_mode:          'auto',
  user_memory_enabled: true,
  agent_memory_enabled: true,
  skills_mode:         'auto',
  fallback_chain:      [],
  tool_hooks:          [],
  shell_readonly:      false,
  user_timezone:       null,
  user_location:       null,
  user_language:       null,
  require_plan_approval: false,
  max_step_extensions: 2,
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      config:            defaultConfig,
      isDirty:           false,
      saveStatus:        'idle',
      activeProjectId:   null,
      activeProjectName: null,

      setConfig: (patch) =>
        set(s => ({ config: { ...s.config, ...patch }, isDirty: true })),

      replaceConfig: (c) =>
        set({ config: c, isDirty: false }),

      markClean: () => set({ isDirty: false }),

      setSaveStatus: (status) => set({ saveStatus: status }),

      setActiveProject: (id, name) =>
        set({ activeProjectId: id, activeProjectName: name }),
    }),
    {
      name: 'eva-session',
      // isDirty and activeProject are transient — not persisted
      partialize: (s: SessionState) => ({ config: s.config }),
    }
  )
)
