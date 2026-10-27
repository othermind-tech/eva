import client, { get, post, put, del } from './client'
import type {
  SkillInfo,
  SkillFilesResponse,
  SkillFileContentResponse,
  SkillFileWriteResponse,
} from '@/types/api'

export const skillsApi = {
  // ── Skill CRUD ─────────────────────────────────────────────
  list:   () => get<SkillInfo[]>('/skills'),

  /** Create a skill. Pass initialContent to write SKILL.md immediately after. */
  create: async (name: string, initialContent?: string): Promise<SkillInfo> => {
    const skill = await post<SkillInfo>('/skills', { name })
    if (initialContent) {
      await put<SkillFileWriteResponse>(
        `/skills/${name}/files/SKILL.md`,
        { content: initialContent },
      )
    }
    return skill
  },

  install: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return client.post<SkillInfo>('/skills/install', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then(r => r.data)
  },

  reload: () => post<void>('/skills/reload'),
  delete: (name: string) => del<void>(`/skills/${name}`),

  // ── File tree + editor (V5.6) ───────────────────────────────
  /** Returns the full recursive file tree for a skill */
  getFiles: (name: string) =>
    get<SkillFilesResponse>(`/skills/${name}/files`),

  /** Read content of a specific file.
   *  Do NOT encode slashes — FastAPI {filepath:path} captures them natively. */
  getFile: (name: string, filepath: string) =>
    get<SkillFileContentResponse>(`/skills/${name}/files/${filepath}`),

  /** Write or create a file (creates parent dirs on the server).
   *  Returns registry_reloaded=true when SKILL.md was edited. */
  writeFile: (name: string, filepath: string, content: string) =>
    put<SkillFileWriteResponse>(`/skills/${name}/files/${filepath}`, { content }),

  /** Delete a file. SKILL.md is protected — backend returns 400. */
  deleteFile: (name: string, filepath: string) =>
    del<{ status: string; name: string; filepath: string }>(
      `/skills/${name}/files/${filepath}`,
    ),
}
