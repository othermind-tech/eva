import type { APIError } from '@/types/api'

// client.ts's response interceptor rejects with a flat { code, detail, status }
// object (see APIError), not an AxiosError — detail/status live at the top
// level, not under .response.data.

// Section 95 — no response reached the client at all (connection refused,
// DNS failure, timeout before any reply), or the server responded but
// couldn't actually serve the request (bad gateway / unavailable / gateway
// timeout). Distinct from a real response the backend chose to send back —
// a 400/422/500 means the server is up and running, just returning an error,
// and telling the user "check that the backend is running" for one of those
// would be actively wrong.
export function isUnreachableError(e: unknown): boolean {
  const status = (e as APIError | undefined)?.status
  return status === undefined || status === 502 || status === 503 || status === 504
}

export function extractErrorDetail(e: unknown, fallback: string): string {
  const detail = (e as APIError | undefined)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(d => d.msg).join('; ')
  // Sections 101/102 — structured details ({ code | error_code, message, ... }).
  // Without this branch every caller fell back to its generic sentence and the
  // server's own explanation was thrown away.
  if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') {
    return (detail as { message: string }).message
  }
  return fallback
}

// Section 106 - PDF text extraction (pypdf) reports a locked file as "PDF is password-protected",
// inside "PDF extraction failed: ..." on a chat attachment (422 detail) and bare on a
// knowledge-base ingest job (`error`). Match only that substring, as the backend asks: its exact
// string beyond it isn't a contract, so every other message is shown as the server wrote it.
export const LOCKED_PDF_MESSAGE = 'This PDF is locked. Remove the password and upload it again.'

export function friendlyUploadError(text: string): string {
  return /password-protected/i.test(text) ? LOCKED_PDF_MESSAGE : text
}

// Sections 101 + 102 (V6.59) — the backend refuses a provider/model choice that
// cannot be used, in two shapes that mean the same thing:
//   save-time (agent / daemon / set-session): { code: 'EVA_PROVIDER_NOT_USABLE',
//     field, provider, reason, message }                       -> 422, or 424 for no key
//   run-time  (POST /chat):  { error_code: 'provider_not_usable',
//     reason, provider, model, message }                       -> 422, or 424 for no key
// `field` is 'provider' | 'model' | 'fallback_chain[<i>].provider' and says which
// control the message belongs to; run-time rejections have no field.
export interface ProviderProblem {
  reason:    string
  message:   string
  provider?: string
  model?:    string
  field?:    string
}

export const REASON_NOT_CONFIGURED = 'provider_not_configured'

export function parseProviderError(e: unknown): ProviderProblem | null {
  const detail = (e as APIError | undefined)?.detail as
    { code?: string; error_code?: string; reason?: string; message?: string
      provider?: string; model?: string; field?: string } | undefined
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null
  if (detail.code !== 'EVA_PROVIDER_NOT_USABLE' && detail.error_code !== 'provider_not_usable') return null
  if (typeof detail.message !== 'string' || typeof detail.reason !== 'string') return null
  return {
    reason: detail.reason, message: detail.message,
    provider: detail.provider, model: detail.model, field: detail.field,
  }
}

// 'fallback_chain[2].provider' -> 2. Anything else -> null.
export function fallbackIndexFromField(field: string | undefined): number | null {
  const m = field?.match(/^fallback_chain\[(\d+)\]/)
  return m ? Number(m[1]) : null
}
