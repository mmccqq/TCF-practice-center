import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  adminCancelJob, adminCreateJob, adminJobs, adminLlm, adminScopePreview,
  adminVocabulary,
} from '../../lib/api'
import { TacheTabs } from './AdminLayout'

const TASKS = ['theme', 'abstract', 'core_subject']
const DONE = ['done', 'failed', 'cancelled', 'interrupted']

export default function Jobs() {
  const [form, setForm] = useState({
    tache: 2, task: 'theme', provider: '', model: '', chunk: 20, limit: '',
    theme_id: '', api_key: '',
  })
  const [error, setError] = useState('')
  const qc = useQueryClient()

  const { data: caps } = useQuery({ queryKey: ['admin-llm'], queryFn: adminLlm })
  const { data: vocab } = useQuery({
    queryKey: ['admin-vocabulary', form.tache],
    queryFn: () => adminVocabulary({ tache: form.tache }),
  })
  const { data: jobs } = useQuery({
    queryKey: ['admin-jobs'],
    queryFn: adminJobs,
    // only while something is moving; a finished list does not need polling
    refetchInterval: (q) =>
      (q.state.data ?? []).some((j) => !DONE.includes(j.status)) ? 3000 : false,
  })

  const scope = {
    tache: form.tache, task: form.task,
    ...(form.theme_id ? { theme_id: form.theme_id } : {}),
    ...(form.limit ? { limit: form.limit } : {}),
  }
  const { data: preview } = useQuery({
    queryKey: ['admin-scope', scope],
    queryFn: () => adminScopePreview(scope),
  })

  const create = useMutation({
    mutationFn: () => adminCreateJob({
      tache: form.tache, task: form.task, provider: form.provider,
      model: form.model || undefined, chunk: Number(form.chunk),
      limit: form.limit ? Number(form.limit) : undefined,
      theme_id: form.theme_id ? Number(form.theme_id) : undefined,
      api_key: form.api_key.trim() || undefined,
    }),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-jobs'] }),
  })

  const providers = caps?.providers ?? []
  const set = (patch) => setForm((f) => ({ ...f, ...patch }))
  const chosen = providers.find((p) => p.name === form.provider)
  // a key from this form counts as much as one in the server's environment
  const haveKey = !!form.api_key.trim() || !!chosen?.configured

  return (
    <div className="space-y-4">
      <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-end gap-3">
          <TacheTabs tache={form.tache} onChange={(t) => set({ tache: t, theme_id: '' })} />
          <label className="text-sm">
            <span className="block text-xs text-slate-500">Task</span>
            <select value={form.task} onChange={(e) => set({ task: e.target.value })}
                    className="mt-1 rounded-md border border-slate-300 bg-white px-2 py-1">
              {TASKS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="text-sm">
            <span className="block text-xs text-slate-500">Provider</span>
            <select value={form.provider} onChange={(e) => set({ provider: e.target.value, model: '' })}
                    className="mt-1 rounded-md border border-slate-300 bg-white px-2 py-1">
              <option value="">choose…</option>
              {/* every provider is selectable now: a key can come from this
                  form, so one missing from the server is not disqualifying */}
              {providers.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}{p.configured ? ' — key on server' : ''}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="block text-xs text-slate-500">Model</span>
            <input
              value={form.model}
              onChange={(e) => set({ model: e.target.value })}
              placeholder={chosen?.default_model || 'default'}
              className="mt-1 w-48 rounded-md border border-slate-300 px-2 py-1"
            />
          </label>
          <label className="text-sm">
            <span className="block text-xs text-slate-500">Chunk</span>
            <input type="number" min="1" max="100" value={form.chunk}
                   onChange={(e) => set({ chunk: e.target.value })}
                   className="mt-1 w-20 rounded-md border border-slate-300 px-2 py-1" />
          </label>
          <label className="text-sm">
            <span className="block text-xs text-slate-500">Limit</span>
            <input type="number" min="1" value={form.limit} placeholder="all"
                   onChange={(e) => set({ limit: e.target.value })}
                   className="mt-1 w-20 rounded-md border border-slate-300 px-2 py-1" />
          </label>
          <label className="text-sm">
            <span className="block text-xs text-slate-500">
              API key{chosen && chosen.configured ? ' (server has one)' : ''}
            </span>
            {/* sent per run and used for that run only - never stored on the
                job, logged, or returned by any endpoint. type=password so it
                does not sit on screen or in an autofill history. */}
            <input
              type="password"
              value={form.api_key}
              onChange={(e) => set({ api_key: e.target.value })}
              autoComplete="off"
              placeholder={chosen?.configured ? 'leave blank to use the server key'
                                              : (chosen?.key_env?.[0] || 'sk-…')}
              className="mt-1 w-56 rounded-md border border-slate-300 px-2 py-1"
            />
          </label>
          {form.task === 'core_subject' && (
            <label className="text-sm">
              <span className="block text-xs text-slate-500">Theme</span>
              <select value={form.theme_id} onChange={(e) => set({ theme_id: e.target.value })}
                      className="mt-1 rounded-md border border-slate-300 bg-white px-2 py-1">
                <option value="">every theme</option>
                {(vocab?.themes ?? []).map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 text-sm">
          {/* the size of the run, before any of it is paid for */}
          <span className="text-slate-600">
            {preview
              ? `${preview.rows} question${preview.rows === 1 ? '' : 's'} · `
                + `${Math.ceil(preview.rows / Math.max(1, Number(form.chunk)))} request(s)`
              : '…'}
            {/* `preview?.rows === preview?.cap` looks safe and is not: before
                the query resolves both sides are undefined, the comparison is
                true, and reading preview.cap throws. Guard on the object. */}
            {preview && preview.rows === preview.cap && ` · capped at ${preview.cap}`}
          </span>
          <button
            onClick={() => create.mutate()}
            disabled={!form.provider || !haveKey || !preview?.rows || create.isPending}
            className="rounded-md bg-slate-900 px-3 py-1.5 font-medium text-white disabled:opacity-40"
          >
            {create.isPending ? 'Starting…' : 'Run'}
          </button>
          {form.provider && !haveKey && (
            <span className="text-amber-700">
              Paste an API key — this server has none for {form.provider}.
            </span>
          )}
        </div>
        {preview?.sample?.length > 0 && (
          <ul className="space-y-0.5 text-xs text-slate-400">
            {preview.sample.map((t, i) => <li key={i}>{t}…</li>)}
          </ul>
        )}
      </div>

      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      <ul className="space-y-2">
        {(jobs ?? []).map((j) => {
          const pct = j.total_chunks ? Math.round((j.done_chunks / j.total_chunks) * 100) : 0
          return (
            <li key={j.id} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                  j.status === 'running' ? 'bg-sky-100 text-sky-800'
                  : j.status === 'done' ? 'bg-emerald-100 text-emerald-800'
                  : j.status === 'queued' ? 'bg-slate-100 text-slate-700'
                  : 'bg-amber-100 text-amber-900'
                }`}>{j.status}</span>
                <strong>{j.task}</strong>
                {/* a scrape job has no model or chunk, and counts pages */}
                {j.kind === 'scrape' ? (
                  <>
                    <span className="text-slate-500">{j.provider}</span>
                    <span className="text-slate-500">
                      {j.done_chunks}/{j.total_chunks} pages · {j.answered} questions
                    </span>
                  </>
                ) : (
                  <>
                    <span className="text-slate-500">
                      {j.provider} · {j.model} · Task {j.tache} · chunk {j.chunk}
                    </span>
                    <span className="text-slate-500">
                      {j.done_chunks}/{j.total_chunks} requests · {j.answered} answers
                    </span>
                  </>
                )}
                {/* a comparison is read on Compare, a single run on Review -
                    both land in the same batch either way */}
                {j.batch_id && (
                  <Link
                    to={j.scope?.mode === 'compare'
                      ? `/admin/compare?batch=${j.batch_id}`
                      : `/admin/reviews?batch=${j.batch_id}`}
                    className="text-sky-700 hover:underline"
                  >
                    {j.scope?.mode === 'compare' ? 'compare →' : 'review →'}
                  </Link>
                )}
                {!DONE.includes(j.status) && (
                  <button
                    onClick={async () => { await adminCancelJob(j.id); qc.invalidateQueries({ queryKey: ['admin-jobs'] }) }}
                    className="ml-auto text-xs text-red-600 hover:underline"
                  >
                    cancel
                  </button>
                )}
              </div>
              {j.total_chunks > 0 && j.status === 'running' && (
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200">
                  <div className="h-full bg-sky-500 transition-all" style={{ width: `${pct}%` }} />
                </div>
              )}
              {j.error && <p className="mt-1 text-xs text-amber-800">{j.error}</p>}
            </li>
          )
        })}
      </ul>

      {jobs?.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
          No runs yet. A finished run lands in Review as a batch.
        </p>
      )}
    </div>
  )
}
