import { get, post, put, del } from './client'
import type {
  UserMemoryFact, AgentMemoryEntry, AgentScopeInfo, MemorySummary, GrantAccessStatus
} from '@/types/api'

export const memoryApi = {
  // Scope 2 — User facts
  getUserFacts:      () => get<UserMemoryFact[]>('/memory/user-facts'),
  createUserFact:    (fact: string, category?: string) =>
    post<UserMemoryFact>('/memory/user-facts', { fact, category }),
  updateUserFact:    (factId: string, fact: string, category?: string) =>
    put<UserMemoryFact>(`/memory/user-facts/${factId}`, { fact, category }),
  deleteUserFact:    (factId: string) => del<void>(`/memory/user-facts/${factId}`),

  // Scope 3 — Agent memory
  // Scope discovery (B-012) — list all scope keys + entry counts
  listAgentScopes:   () => get<AgentScopeInfo[]>('/memory/agent'),
  getAgentEntries:   (scopeKey: string) => get<AgentMemoryEntry[]>(`/memory/agent/${scopeKey}`),

  // Grant-access for sensitive entries (B-033)
  // Check if a grant is currently active (TTL remaining)
  checkGrant:   (scopeKey: string, entryId: string) =>
    get<GrantAccessStatus>(`/memory/agent/${scopeKey}/${entryId}/grant-access`),
  // Create a 1-hour Redis grant so memory_recall returns real content
  grantAccess:  (scopeKey: string, entryId: string) =>
    post<GrantAccessStatus>(`/memory/agent/${scopeKey}/${entryId}/grant-access`, {}),
  // Revoke an active grant immediately
  revokeAccess: (scopeKey: string, entryId: string) =>
    del<void>(`/memory/agent/${scopeKey}/${entryId}/grant-access`),

  // Summary
  getSummary: () => get<MemorySummary>('/memory/summary'),
}
