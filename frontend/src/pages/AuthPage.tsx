import { useState, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/store/authStore'
import { authApi } from '@/api/auth'
import { Eye, EyeOff, Loader2, AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

type Mode = 'login' | 'signup'

export default function AuthPage() {
  const [mode, setMode]         = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail]       = useState('')
  const [showPw, setShowPw]     = useState(false)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const setAuth     = useAuthStore(s => s.setAuth)
  const queryClient = useQueryClient()

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      if (mode === 'signup') {
        if (!email) { setError('Email is required for signup'); setLoading(false); return }
        await authApi.signup({ username, password, email })
      }
      // Login sends form-encoded data (OAuth2PasswordRequestForm)
      const res = await authApi.login(username, password)
      queryClient.clear()
      setAuth(res.access_token, username, res.expires_in, res.refresh_token)
    } catch (err: unknown) {
      const msg = (err as { detail?: string })?.detail ?? 'Authentication failed'
      setError(typeof msg === 'string' ? msg : 'Authentication failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="h-screen w-screen flex items-center justify-center relative overflow-hidden"
      style={{ background: 'var(--bg-base)' }}
    >
      {/* Background grid */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: `linear-gradient(var(--eva-primary) 1px, transparent 1px),
                            linear-gradient(90deg, var(--eva-primary) 1px, transparent 1px)`,
          backgroundSize: '48px 48px',
        }}
      />

      {/* Glow orb */}
      <div
        className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 rounded-full pointer-events-none"
        style={{
          background: 'radial-gradient(circle, rgba(0,201,255,0.06) 0%, transparent 70%)',
        }}
      />

      {/* Auth card */}
      <div
        className="relative w-full max-w-sm mx-4 rounded-2xl overflow-hidden animate-fade-up"
        style={{
          background: 'var(--bg-surface1)',
          border: '1px solid var(--border-mid)',
          boxShadow: '0 24px 80px rgba(0,0,0,0.5), 0 0 40px rgba(0,201,255,0.05)',
        }}
      >
        {/* Top gradient bar */}
        <div className="h-[2px] w-full animated-border" />

        <div className="p-8">
          {/* Logo */}
          <div className="flex flex-col items-center gap-3 mb-8">
            <img
              src="/eva-main-no-underline.svg"
              alt="EVA"
              style={{ width: 180, height: 'auto', objectFit: 'contain' }}
            />
          </div>

          {/* Mode toggle */}
          <div
            className="flex rounded-lg p-1 mb-6"
            style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}
          >
            {(['login', 'signup'] as Mode[]).map(m => (
              <button
                key={m}
                onClick={() => { setMode(m); setError(null) }}
                className={cn(
                  'flex-1 py-1.5 rounded-md text-sm font-medium transition-all duration-150',
                  mode === m
                    ? 'text-[var(--eva-primary)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'
                )}
                style={mode === m ? {
                  background: 'var(--eva-primary-dim)',
                  border: '1px solid var(--border-mid)',
                } : {}}
              >
                {m === 'login' ? 'Sign In' : 'Sign Up'}
              </button>
            ))}
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            {/* Username */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                USERNAME
              </label>
              <input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                required
                autoFocus
                placeholder="your_username"
                className="w-full px-3 py-2.5 rounded-lg text-sm outline-none transition-all duration-150"
                style={{
                  background: 'var(--bg-surface2)',
                  border: '1px solid var(--border-dim)',
                  color: 'var(--text-primary)',
                  fontFamily: 'JetBrains Mono, monospace',
                }}
                onFocus={e => e.target.style.borderColor = 'var(--border-bright)'}
                onBlur={e => e.target.style.borderColor = 'var(--border-dim)'}
              />
            </div>

            {/* Email (signup only) */}
            {mode === 'signup' && (
              <div className="flex flex-col gap-1.5 animate-fade-up">
                <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                  EMAIL
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required
                  placeholder="you@example.com"
                  className="w-full px-3 py-2.5 rounded-lg text-sm outline-none transition-all duration-150"
                  style={{
                    background: 'var(--bg-surface2)',
                    border: '1px solid var(--border-dim)',
                    color: 'var(--text-primary)',
                  }}
                  onFocus={e => e.target.style.borderColor = 'var(--border-bright)'}
                  onBlur={e => e.target.style.borderColor = 'var(--border-dim)'}
                />
              </div>
            )}

            {/* Password */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                PASSWORD
              </label>
              <div className="relative">
                <input
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required
                  placeholder="••••••••"
                  className="w-full px-3 py-2.5 pr-10 rounded-lg text-sm outline-none transition-all duration-150"
                  style={{
                    background: 'var(--bg-surface2)',
                    border: '1px solid var(--border-dim)',
                    color: 'var(--text-primary)',
                    fontFamily: 'JetBrains Mono, monospace',
                  }}
                  onFocus={e => e.target.style.borderColor = 'var(--border-bright)'}
                  onBlur={e => e.target.style.borderColor = 'var(--border-dim)'}
                />
                <button
                  type="button"
                  onClick={() => setShowPw(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 transition-colors"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            {/* Error */}
            {error && (
              <div
                className="flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm animate-fade-up"
                style={{
                  background: 'var(--danger-dim)',
                  border: '1px solid rgba(255,77,109,0.25)',
                  color: 'var(--danger)',
                }}
              >
                <AlertCircle size={14} className="shrink-0" />
                {error}
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 rounded-lg text-sm font-semibold transition-all duration-150 flex items-center justify-center gap-2 mt-1"
              style={{
                background: loading ? 'var(--eva-primary-dim)' : 'var(--eva-primary)',
                color: loading ? 'var(--eva-primary)' : 'var(--bg-base)',
                border: '1px solid var(--border-bright)',
                boxShadow: loading ? 'none' : '0 0 20px rgba(0,201,255,0.25)',
              }}
            >
              {loading && <Loader2 size={15} className="animate-spin" />}
              {mode === 'login' ? 'Sign In' : 'Create Account'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
