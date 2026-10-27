import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { LS_TOKEN, LS_USER, LS_REFRESH_TOKEN, LS_TOKEN_EXPIRES_AT } from '@/lib/constants'
import { clearAuthAndRedirect } from '@/api/client'

interface AuthState {
  token:        string | null
  refreshToken: string | null
  expiresAt:    number | null   // epoch ms when access token expires
  username:     string | null
  isAuthed:     boolean

  setAuth:      (token: string, username: string, expiresIn: number, refreshToken: string) => void
  clearAuth:    () => void
  updateTokens: (token: string, refreshToken: string, expiresIn: number) => void
}

// Proactive refresh timer handle — lives outside Zustand to survive re-renders
let refreshTimer: ReturnType<typeof setTimeout> | null = null

function scheduleRefresh(expiresAt: number) {
  if (refreshTimer) clearTimeout(refreshTimer)

  // Refresh 5 minutes before expiry
  const msUntilRefresh = expiresAt - Date.now() - 5 * 60 * 1000

  if (msUntilRefresh <= 0) {
    // Already close to expiry — refresh immediately
    doRefresh()
    return
  }

  refreshTimer = setTimeout(doRefresh, msUntilRefresh)
}

async function doRefresh() {
  const refreshToken = localStorage.getItem(LS_REFRESH_TOKEN)
  if (!refreshToken) return

  try {
    // Dynamic import avoids a circular dependency (auth.ts → client.ts → localStorage)
    const { authApi } = await import('@/api/auth')
    const data = await authApi.refresh(refreshToken)

    useAuthStore.getState().updateTokens(data.access_token, data.refresh_token, data.expires_in)
  } catch {
    // Refresh failed — redirect to login immediately (same path as the 401 interceptor)
    clearAuthAndRedirect()
  }
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token:        null,
      refreshToken: null,
      expiresAt:    null,
      username:     null,
      isAuthed:     false,

      setAuth: (token, username, expiresIn, refreshToken) => {
        const expiresAt = Date.now() + expiresIn * 1000
        localStorage.setItem(LS_TOKEN, token)
        localStorage.setItem(LS_USER, username)
        localStorage.setItem(LS_REFRESH_TOKEN, refreshToken)
        localStorage.setItem(LS_TOKEN_EXPIRES_AT, String(expiresAt))
        set({ token, refreshToken, expiresAt, username, isAuthed: true })
        scheduleRefresh(expiresAt)
      },

      updateTokens: (token, refreshToken, expiresIn) => {
        const expiresAt = Date.now() + expiresIn * 1000
        localStorage.setItem(LS_TOKEN, token)
        localStorage.setItem(LS_REFRESH_TOKEN, refreshToken)
        localStorage.setItem(LS_TOKEN_EXPIRES_AT, String(expiresAt))
        set({ token, refreshToken, expiresAt })
        scheduleRefresh(expiresAt)
      },

      clearAuth: () => {
        if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null }
        localStorage.removeItem(LS_TOKEN)
        localStorage.removeItem(LS_USER)
        localStorage.removeItem(LS_REFRESH_TOKEN)
        localStorage.removeItem(LS_TOKEN_EXPIRES_AT)
        set({ token: null, refreshToken: null, expiresAt: null, username: null, isAuthed: false })
      },
    }),
    {
      name: 'eva-auth',
      // Re-schedule the refresh timer when the store is rehydrated from localStorage
      onRehydrateStorage: () => (state) => {
        if (state?.isAuthed && state.expiresAt) {
          scheduleRefresh(state.expiresAt)
        }
      },
    }
  )
)
