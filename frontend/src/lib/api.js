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

// params is optional: {tache} scopes the filter options to one task
// Per-question state. Everything is keyed on f_id (the question), not on the
// list row, so acting on one card acts on every month that question appears in.
export const getProgress = (params) =>
  api(`/api/progress?${new URLSearchParams(params)}`, { auth: true })
export const markPracticed = (fId) =>
  api(`/api/attempts/${fId}`, { method: 'PUT', auth: true })
export const unmarkPracticed = (fId) =>
  api(`/api/attempts/${fId}`, { method: 'DELETE', auth: true })
export const addBookmark = (fId) =>
  api(`/api/bookmarks/${fId}`, { method: 'PUT', auth: true })
export const removeBookmark = (fId) =>
  api(`/api/bookmarks/${fId}`, { method: 'DELETE', auth: true })
export const coreSetProgress = (params) =>
  api(`/api/progress/summary?${new URLSearchParams(params)}`, { auth: true })
export const listBookmarks = (params) =>
  api(`/api/bookmarks?${new URLSearchParams(params)}`, { auth: true })

export const frequentSubjects = (params) =>
  api(`/api/questions/frequent?${new URLSearchParams(params)}`)

export const questionsMeta = (params) =>
  api('/api/questions/meta' + (params ? `?${new URLSearchParams(params)}` : ''))
export const googleConfig = () => api('/api/auth/google/config')

// ---- admin. Every one of these 403s for a signed-in non-admin. ----
const adminGet = (path, params) =>
  api(`/api/admin/${path}${params ? `?${new URLSearchParams(params)}` : ''}`, { auth: true })

export const adminVocabulary = (params) => adminGet('vocabulary', params)
export const adminCreateTheme = (body) =>
  api('/api/admin/themes', { method: 'POST', body, auth: true })
export const adminRenameTheme = (id, body) =>
  api(`/api/admin/themes/${id}`, { method: 'PATCH', body, auth: true })
export const adminDeleteTheme = (id) =>
  api(`/api/admin/themes/${id}`, { method: 'DELETE', auth: true })
export const adminCreateSubject = (body) =>
  api('/api/admin/core-subjects', { method: 'POST', body, auth: true })
export const adminUpdateSubject = (id, body) =>
  api(`/api/admin/core-subjects/${id}`, { method: 'PATCH', body, auth: true })
export const adminDeleteSubject = (id) =>
  api(`/api/admin/core-subjects/${id}`, { method: 'DELETE', auth: true })
export const adminMergeSubject = (id, intoId) =>
  api(`/api/admin/core-subjects/${id}/merge`, { method: 'POST', body: { into_id: intoId }, auth: true })

export const adminQuestions = (params) => adminGet('questions', params)
export const adminQuestionIds = (params) => adminGet('questions/ids', params)
export const adminSetLabels = (fId, body) =>
  api(`/api/admin/questions/${fId}`, { method: 'PATCH', body, auth: true })
export const adminBulkLabel = (body) =>
  api('/api/admin/questions/bulk', { method: 'POST', body, auth: true })

export const adminBatches = () => adminGet('reviews')
export const adminBatch = (id, params) => adminGet(`reviews/${id}`, params)
export const adminCreateBatch = (body) =>
  api('/api/admin/reviews', { method: 'POST', body, auth: true })
export const adminDecide = (batchId, itemId, decision) =>
  api(`/api/admin/reviews/${batchId}/items/${itemId}`,
      { method: 'PATCH', body: { decision }, auth: true })
export const adminDecideAll = (id, body) =>
  api(`/api/admin/reviews/${id}/decide-all`, { method: 'POST', body, auth: true })
export const adminApplyBatch = (id) =>
  api(`/api/admin/reviews/${id}/apply`, { method: 'POST', body: {}, auth: true })
export const adminDeleteBatch = (id) =>
  api(`/api/admin/reviews/${id}`, { method: 'DELETE', auth: true })
export const adminDecideAgreed = (id) =>
  api(`/api/admin/reviews/${id}/decide-agreed`, { method: 'POST', body: {}, auth: true })

// running llm.py on the server, importing scraper output, exporting questions
export const adminLlm = () => adminGet('llm')
export const adminJobs = () => adminGet('jobs')
export const adminCreateJob = (body) =>
  api('/api/admin/jobs', { method: 'POST', body, auth: true })
export const adminCancelJob = (id) =>
  api(`/api/admin/jobs/${id}/cancel`, { method: 'POST', body: {}, auth: true })
export const adminScrape = (sources) =>
  api('/api/admin/scrape', { method: 'POST', body: { sources }, auth: true })
/**
 * Download a file from an authenticated endpoint.
 *
 * Not a plain <a href>: these routes need the bearer token, and a link cannot
 * carry a header. So it is fetched as a blob and handed to the browser through
 * an object URL. The filename comes from Content-Disposition, so the server
 * decides what the file is called.
 */
export async function adminDownload(path, params) {
  const res = await fetch(`${BASE}/api/admin/${path}?${new URLSearchParams(params)}`, {
    headers: { Authorization: `Bearer ${tokenStore.get()}` },
  })
  if (!res.ok) {
    const text = await res.text()
    let detail
    try { detail = JSON.parse(text)?.detail } catch { detail = null }
    throw new Error(detail || `download failed (${res.status})`)
  }
  const disposition = res.headers.get('Content-Disposition') || ''
  const name = /filename="?([^"]+)"?/.exec(disposition)?.[1] || 'export.xlsx'
  const url = URL.createObjectURL(await res.blob())
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  a.click()
  URL.revokeObjectURL(url)
  return name
}

export const adminExport = (params) => adminDownload('questions/export', params)

export const adminBatchStats = (id) => adminGet(`reviews/${id}/stats`)

export const adminCompare = (body) =>
  api('/api/admin/compare', { method: 'POST', body, auth: true })
export const signup = (body) => api('/api/auth/signup', { method: 'POST', body })
export const login = (body) => api('/api/auth/login', { method: 'POST', body })
export const fetchMe = () => api('/api/auth/me', { auth: true })

/**
 * Query keys that admin edits invalidate.
 *
 * The public pages compute from the same tables, so a label written in the
 * admin area is live on the server immediately - but the browser holds its
 * answers for `staleTime` (60s), so the Core set page would keep showing the
 * old grouping until that expired or the tab was reloaded. Same shape of bug
 * as bookmarks not appearing after starring one.
 */
export const PUBLIC_KEYS = [
  ['frequent'],        // the core set: subjects, counts, representatives
  ['core-progress'],   // "12 of 86", on the home page and the task banner
  ['questions'],       // the task list, which renders theme and core_subject
  ['meta'],            // the theme filter's options and counts
  ['bookmarks'],       // bookmarked rows carry theme and core_subject too
]

export function invalidatePublic(qc) {
  PUBLIC_KEYS.forEach((queryKey) => qc.invalidateQueries({ queryKey }))
}
