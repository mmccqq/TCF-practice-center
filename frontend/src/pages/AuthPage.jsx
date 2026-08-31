import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { fetchMe, googleConfig, login, signup, tokenStore } from '../lib/api'
import { useAuth } from '../lib/auth'

const OAUTH_ERRORS = {
  bad_state: 'Sign-in could not be verified. Please try again.',
  token_exchange_failed: 'Google rejected the sign-in. Please try again.',
  no_id_token: 'Google did not return an identity token.',
  no_email: 'That Google account did not share an email address.',
  no_code: 'Sign-in was cancelled.',
  access_denied: 'Sign-in was cancelled.',
}

export default function AuthPage({ mode }) {
  const isSignup = mode === 'signup'
  const { signIn, setUser } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const { data: google } = useQuery({ queryKey: ['googleConfig'], queryFn: googleConfig })

  // The Google callback redirects to /login#token=... - consume it, then strip
  // it from the URL so the credential is not left sitting in history.
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const oauthError = params.get('error')
    if (oauthError) {
      setError(OAUTH_ERRORS[oauthError] || `Sign-in failed (${oauthError}).`)
      window.history.replaceState({}, '', location.pathname)
      return
    }
    const hash = new URLSearchParams(location.hash.replace(/^#/, ''))
    const token = hash.get('token')
    if (!token) return
    tokenStore.set(token)
    window.history.replaceState({}, '', location.pathname)
    fetchMe()
      .then((u) => { setUser(u); navigate('/', { replace: true }) })
      .catch(() => { tokenStore.clear(); setError('Could not complete Google sign-in.') })
  }, [location, navigate, setUser])

  async function onSubmit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const fn = isSignup ? signup : login
      const body = isSignup
        ? { email, password, display_name: displayName || undefined }
        : { email, password }
      const data = await fn(body)
      signIn(data.access_token, data.user)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-sm">
      <h1 className="text-2xl font-bold tracking-tight">
        {isSignup ? 'Create an account' : 'Log in'}
      </h1>
      <p className="mt-1 text-sm text-slate-600">
        {isSignup
          ? 'Track which questions you have practised.'
          : 'Welcome back.'}
      </p>

      {error && (
        <p className="mt-4 rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>
      )}

      <form onSubmit={onSubmit} className="mt-6 space-y-3">
        {isSignup && (
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Display name (optional)"
            autoComplete="nickname"
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-sky-500 focus:outline-none"
          />
        )}
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          autoComplete="email"
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-sky-500 focus:outline-none"
        />
        <input
          type="password"
          required
          minLength={isSignup ? 8 : undefined}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={isSignup ? 'Password (at least 8 characters)' : 'Password'}
          autoComplete={isSignup ? 'new-password' : 'current-password'}
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-sky-500 focus:outline-none"
        />
        <button
          disabled={busy}
          className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {busy ? 'Working…' : isSignup ? 'Sign up' : 'Log in'}
        </button>
      </form>

      <div className="my-5 flex items-center gap-3 text-xs text-slate-400">
        <span className="h-px flex-1 bg-slate-200" />or<span className="h-px flex-1 bg-slate-200" />
      </div>

      {google?.enabled ? (
        <a
          href="/api/auth/google/login"
          className="flex w-full items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
            <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.4a5.5 5.5 0 0 1-2.4 3.6v3h3.9c2.3-2.1 3.6-5.2 3.6-8.8Z"/>
            <path fill="#34A853" d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-3.9-3a7.2 7.2 0 0 1-10.7-3.8H1.3v3.1A12 12 0 0 0 12 24Z"/>
            <path fill="#FBBC05" d="M5.3 14.3a7.2 7.2 0 0 1 0-4.6V6.6H1.3a12 12 0 0 0 0 10.8l4-3.1Z"/>
            <path fill="#EA4335" d="M12 4.8c1.8 0 3.4.6 4.6 1.8l3.4-3.4A12 12 0 0 0 1.3 6.6l4 3.1A7.2 7.2 0 0 1 12 4.8Z"/>
          </svg>
          Continue with Google
        </a>
      ) : (
        <p
          title="Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in backend/.env"
          className="rounded-md border border-dashed border-slate-300 px-3 py-2 text-center text-xs text-slate-500"
        >
          Google sign-in is not configured on this server
        </p>
      )}

      <p className="mt-6 text-center text-sm text-slate-600">
        {isSignup ? 'Already have an account? ' : 'No account yet? '}
        <Link to={isSignup ? '/login' : '/signup'} className="font-medium text-sky-700 hover:underline">
          {isSignup ? 'Log in' : 'Sign up'}
        </Link>
      </p>
    </div>
  )
}
