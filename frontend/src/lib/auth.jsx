import { createContext, useContext, useEffect, useState } from 'react'
import { fetchMe, tokenStore } from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

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

  const signIn = (token, u) => { tokenStore.set(token); setUser(u) }
  const signOut = () => { tokenStore.clear(); setUser(null) }

  return (
    <AuthContext.Provider value={{ user, loading, signIn, signOut, setUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
