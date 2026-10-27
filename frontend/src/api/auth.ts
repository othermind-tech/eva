import client, { get, post, patch } from './client'
import type { AuthResponse, SignupRequest, UserProfile, UpdateProfilePayload, ChangePasswordPayload } from '@/types/api'

export const authApi = {
  signup: (body: SignupRequest) =>
    post<AuthResponse>('/auth/signup', body),

  /**
   * Login uses OAuth2PasswordRequestForm — must be sent as
   * application/x-www-form-urlencoded, NOT JSON.
   */
  login: (username: string, password: string) => {
    const form = new URLSearchParams()
    form.append('username', username)
    form.append('password', password)
    return client
      .post<AuthResponse>('/auth/login', form, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      })
      .then(r => r.data)
  },

  logout: () =>
    post<void>('/auth/logout'),

  /**
   * Exchange an expiring refresh token for a new access + refresh token pair.
   * Refresh tokens are ROTATED — always store the new refresh_token from the response.
   */
  refresh: (refreshToken: string) =>
    post<AuthResponse>('/auth/refresh', { refresh_token: refreshToken }),

  /**
   * Get a short-lived (30s), single-use SSE token.
   * Call this immediately before opening an EventSource — the token expires in 30 seconds
   * and is deleted on first use. Never cache or reuse it.
   */
  getSseToken: () =>
    post<{ sse_token: string; expires_in: number }>('/auth/sse-token'),

  // ── V6.0 — User Profile (Section 71) ──────────────────────────────────────

  /** Fetch current user profile — user_id, username, email, full_name, description */
  getMe: () =>
    get<UserProfile>('/auth/me'),

  /**
   * Update profile fields. Omit a field to leave it unchanged.
   * Pass null / "" to clear the field.
   * preferred_name maps to the user.name memory slot (the casual name EVA uses).
   */
  updateProfile: (payload: UpdateProfilePayload) =>
    patch<UserProfile>('/auth/profile', payload),

  /**
   * Change the user's password. Requires the current password for verification.
   * Returns 400 if current_password is wrong; 422 if new_password < 8 chars.
   */
  changePassword: (payload: ChangePasswordPayload) =>
    post<{ status: string }>('/auth/change-password', payload),
}
