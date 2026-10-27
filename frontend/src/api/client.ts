import axios, { type AxiosInstance, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import { API_BASE, LS_TOKEN, LS_REFRESH_TOKEN, LS_TOKEN_EXPIRES_AT, LS_USER, LS_AUTH_STORE } from '@/lib/constants'
import type { APIError } from '@/types/api'

// ─── Base axios instance ─────────────────────────────────────
const client: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

// Separate instance for token refresh — no interceptors to avoid infinite retry loops
const refreshClient = axios.create({
  baseURL: API_BASE,
  timeout: 10_000,
  headers: { 'Content-Type': 'application/json' },
})

// Attach JWT access token to every request
client.interceptors.request.use(config => {
  const token = localStorage.getItem(LS_TOKEN)
  if (token && config.headers) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

// ─── 401 refresh interceptor ─────────────────────────────────
// Single in-flight refresh — queues concurrent 401s until the refresh resolves
let isRefreshing = false
let refreshQueue: Array<(newToken: string) => void> = []

export function clearAuthAndRedirect() {
  localStorage.removeItem(LS_TOKEN)
  localStorage.removeItem(LS_USER)
  localStorage.removeItem(LS_REFRESH_TOKEN)
  localStorage.removeItem(LS_TOKEN_EXPIRES_AT)
  // Also clear the Zustand persist entry so isAuthed resets to false on reload.
  // Without this, the router re-hydrates isAuthed=true and loops back to ChatPage.
  localStorage.removeItem(LS_AUTH_STORE)
  window.location.href = '/auth'
}

client.interceptors.response.use(
  res => res,
  async err => {
    const status = err.response?.status

    // Always surface the error detail as a readable rejection
    const body = err.response?.data
    const apiErr: APIError = {
      code:   body?.code,
      detail: body?.detail ?? err.message ?? 'Unknown error',
      status,
    }

    // Non-401 errors — reject immediately, no refresh logic
    if (status !== 401) return Promise.reject(apiErr)

    // Auth endpoints (login, signup, refresh) returning 401 mean bad credentials,
    // not an expired session — reject immediately so the caller can show the error.
    const url = err.config?.url ?? ''
    if (url.includes('/auth/')) return Promise.reject(apiErr)

    const refreshToken = localStorage.getItem(LS_REFRESH_TOKEN)
    if (!refreshToken) {
      clearAuthAndRedirect()
      return Promise.reject({ detail: 'Session expired', status: 401 })
    }

    const originalRequest = err.config as InternalAxiosRequestConfig & { _retried?: boolean }

    // If this 401 came from the retry itself, bail out
    if (originalRequest._retried) {
      clearAuthAndRedirect()
      return Promise.reject({ detail: 'Session expired', status: 401 })
    }

    if (isRefreshing) {
      // Queue the original request — it will retry once refresh completes
      return new Promise<unknown>(resolve => {
        refreshQueue.push((newToken: string) => {
          originalRequest.headers['Authorization'] = `Bearer ${newToken}`
          originalRequest._retried = true
          resolve(client.request(originalRequest))
        })
      })
    }

    isRefreshing = true
    try {
      const res = await refreshClient.post<{
        access_token: string
        refresh_token: string
        expires_in: number
        token_type: string
      }>('/auth/refresh', { refresh_token: refreshToken })

      const { access_token, refresh_token: newRefresh, expires_in } = res.data

      // Persist new tokens
      localStorage.setItem(LS_TOKEN, access_token)
      localStorage.setItem(LS_REFRESH_TOKEN, newRefresh)
      localStorage.setItem(LS_TOKEN_EXPIRES_AT, String(Date.now() + expires_in * 1000))

      // Flush queued requests with new token
      refreshQueue.forEach(cb => cb(access_token))
      refreshQueue = []

      // Retry original request with new token
      originalRequest.headers['Authorization'] = `Bearer ${access_token}`
      originalRequest._retried = true
      return client.request(originalRequest)
    } catch {
      refreshQueue = []
      clearAuthAndRedirect()
      return Promise.reject({ detail: 'Session expired — please log in again', status: 401 })
    } finally {
      isRefreshing = false
    }
  }
)

export default client

// ─── Typed helpers ───────────────────────────────────────────
export async function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.get<T>(url, config)
  return res.data
}

export async function post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.post<T>(url, data, config)
  return res.data
}

export async function put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.put<T>(url, data, config)
  return res.data
}

export async function patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.patch<T>(url, data, config)
  return res.data
}

export async function del<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.delete<T>(url, config)
  return res.data
}
