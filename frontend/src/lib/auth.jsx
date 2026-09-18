import { useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useState } from 'react'
import { fetchMe, tokenStore } from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const qc = useQueryClient()

  // On boot, exchange any stored token for the user. A token that has expired
  // or was signed with a rotated SECRET_KEY 401s here and is discarded, so the
  // app never sits in a half-logged-in state.
  useEffect(() => {
    if (!tokenStore.get()) { setLoading(false); return }
    fetchMe()
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false))
  }, [])

  // Wipe the query cache on both edges of a session. Signing out otherwise
  // leaves progress and bookmarks sitting in the cache - the queries stop
  // refetching because they are disabled without a user, so the old numbers
  // stay on screen. Signing in matters for the same reason in reverse: on a
  // shared machine the next person would briefly see the previous user's data.
  const signIn = (token, u) => { qc.clear(); tokenStore.set(token); setUser(u) }
  const signOut = () => { tokenStore.clear(); setUser(null); qc.clear() }

  return (
    <AuthContext.Provider value={{ user, loading, signIn, signOut, setUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)

/**
 * Where to send someone who clicks "sign in" from `location`.
 *
 * The destination rides in ?next= rather than router state, because the Google
 * flow leaves the app entirely and comes back through the backend - state
 * would not survive the round trip, a query param can be stashed before it.
 */
export function loginHref(location, path = '/login') {
  const here = `${location.pathname}${location.search}`
  return here === '/' ? path : `${path}?next=${encodeURIComponent(here)}`
}

/** Only same-site paths, so ?next= cannot be used to bounce users off-site. */
export function safeNext(value) {
  return value && value.startsWith('/') && !value.startsWith('//') ? value : '/'
}
