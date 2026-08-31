// Thin fetch wrapper. Base is empty in dev so requests go to the Vite proxy.
const BASE = import.meta.env.VITE_API_BASE ?? ''

const TOKEN_KEY = 'tcf.token'

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

export async function api(path, { method = 'GET', body, auth = false } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const t = tokenStore.get()
    if (t) headers['Authorization'] = `Bearer ${t}`
  }

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (res.status === 204) return null
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    // FastAPI puts the message in `detail`, which is a string for our
    // HTTPExceptions but a list of field errors for 422s
    const d = data?.detail
    const msg = Array.isArray(d)
      ? d.map((e) => `${e.loc?.slice(1).join('.') || 'field'}: ${e.msg}`).join('; ')
      : d || `request failed (${res.status})`
    throw new Error(msg)
  }
  return data
}

export const listQuestions = (params) =>
  api(`/api/questions?${new URLSearchParams(params)}`)

export const questionsMeta = () => api('/api/questions/meta')
export const googleConfig = () => api('/api/auth/google/config')
export const signup = (body) => api('/api/auth/signup', { method: 'POST', body })
export const login = (body) => api('/api/auth/login', { method: 'POST', body })
export const fetchMe = () => api('/api/auth/me', { auth: true })
