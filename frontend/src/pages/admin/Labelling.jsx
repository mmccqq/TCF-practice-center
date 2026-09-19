import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  adminBulkLabel, adminQuestions, adminSetLabels, adminVocabulary,
} from '../../lib/api'
import { TacheTabs } from './AdminLayout'

const PER_PAGE = 50

export default function Labelling() {
  const [tache, setTache] = useState(2)
  const [filters, setFilters] = useState({ unlabelled: 'theme', q: '', theme_id: '' })
  const [page, setPage] = useState(1)
  const [picked, setPicked] = useState(() => new Set())
  const [error, setError] = useState('')
  const qc = useQueryClient()

  const params = { tache, page, per_page: PER_PAGE }
  if (filters.q) params.q = filters.q
  if (filters.theme_id) params.theme_id = filters.theme_id
  if (filters.unlabelled) params.unlabelled = filters.unlabelled
  const key = ['admin-questions', params]

  const { data: vocab } = useQuery({
    queryKey: ['admin-vocabulary', tache],
    queryFn: () => adminVocabulary({ tache }),
  })
  const { data, isPending } = useQuery({ queryKey: key, queryFn: () => adminQuestions(params) })

  const run = useMutation({
    mutationFn: ({ fn }) => fn(),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-questions'] })
      qc.invalidateQueries({ queryKey: ['admin-vocabulary'] })
    },
  })
  const act = (fn) => run.mutate({ fn })

  const themes = vocab?.themes ?? []
  const subjectsFor = (themeId) =>
    themes.find((t) => t.id === themeId)?.core_subjects ?? []

  const setFilter = (patch) => { setFilters((f) => ({ ...f, ...patch })); setPage(1) }
  const toggle = (id) => setPicked((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const pages = data ? Math.max(1, Math.ceil(data.total / PER_PAGE)) : 1

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <TacheTabs tache={tache} onChange={(t) => { setTache(t); setPage(1); setPicked(new Set()) }} />
        <select
          value={filters.unlabelled}
          onChange={(e) => setFilter({ unlabelled: e.target.value })}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
        >
          <option value="">Everything</option>
          <option value="theme">Missing theme</option>
          <option value="core_subject">Missing core subject</option>
          <option value="abstract">Missing abstract</option>
        </select>
        <select
          value={filters.theme_id}
          onChange={(e) => setFilter({ theme_id: e.target.value })}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
        >
          <option value="">Any theme</option>
          {themes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <form onSubmit={(e) => { e.preventDefault(); setFilter({ q: new FormData(e.target).get('q') }) }}>
          <input name="q" defaultValue={filters.q} placeholder="Search text…"
                 className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
        </form>
        <span className="text-sm text-slate-500">
          {data ? `${data.total.toLocaleString()} question${data.total === 1 ? '' : 's'}` : ''}
        </span>
      </div>

      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {/* bulk bar appears only with a selection, so it never competes for space */}
      {picked.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-sky-300
                        bg-sky-50 p-3 text-sm">
          <strong>{picked.size} selected</strong>
          <span className="text-slate-600">assign theme:</span>
          <select
            defaultValue=""
            onChange={(e) => {
              if (!e.target.value) return
              act(() => adminBulkLabel({ f_ids: [...picked], theme_id: Number(e.target.value) }))
              setPicked(new Set())
              e.target.value = ''
            }}
            className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
          >
            <option value="">choose…</option>
            {themes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <button onClick={() => setPicked(new Set())}
                  className="ml-auto text-slate-600 hover:underline">clear</button>
        </div>
      )}

      {isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : (
        <>
          <ol className="space-y-2">
            {data.items.map((row) => (
              <li key={row.f_id}
                  className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-3">
                <input
                  type="checkbox"
                  checked={picked.has(row.f_id)}
                  onChange={() => toggle(row.f_id)}
                  className="mt-1"
                  aria-label={`Select question ${row.f_id}`}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-relaxed">{row.text}</p>
                  <p className="mt-1 text-xs text-slate-400">
                    f_id {row.f_id} · {row.months_seen} month{row.months_seen === 1 ? '' : 's'}
                    {row.last_seen && ` · last ${row.last_seen}`}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <select
                      value={row.theme_id ?? ''}
                      onChange={(e) => act(() => adminSetLabels(row.f_id, {
                        theme_id: e.target.value ? Number(e.target.value) : null,
                      }))}
                      className="rounded border border-slate-300 bg-white px-2 py-1 text-xs"
                    >
                      <option value="">— theme —</option>
                      {themes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                    <select
                      value={row.core_subject_id ?? ''}
                      disabled={!row.theme_id}
                      onChange={(e) => act(() => adminSetLabels(row.f_id, {
                        core_subject_id: e.target.value ? Number(e.target.value) : null,
                      }))}
                      className="rounded border border-slate-300 bg-white px-2 py-1 text-xs
                                 disabled:bg-slate-50 disabled:text-slate-400"
                    >
                      {/* a core subject belongs to a theme, so there is nothing
                          to choose from until the theme is set */}
                      <option value="">{row.theme_id ? '— core subject —' : 'set a theme first'}</option>
                      {subjectsFor(row.theme_id).map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </select>
                    <input
                      defaultValue={row.abstract ?? ''}
                      placeholder="abstract…"
                      onBlur={(e) => {
                        if (e.target.value !== (row.abstract ?? '')) {
                          act(() => adminSetLabels(row.f_id, { abstract: e.target.value }))
                        }
                      }}
                      className="min-w-48 flex-1 rounded border border-slate-300 px-2 py-1 text-xs"
                    />
                  </div>
                </div>
              </li>
            ))}
          </ol>

          {data.items.length === 0 && (
            <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
              Nothing matches those filters.
            </p>
          )}

          {pages > 1 && (
            <div className="flex items-center justify-between pt-2 text-sm">
              <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
                      className="rounded-md border border-slate-300 px-3 py-1.5 disabled:opacity-40">
                &larr; Previous
              </button>
              <span className="text-slate-500">Page {page} of {pages}</span>
              <button disabled={page >= pages} onClick={() => setPage((p) => p + 1)}
                      className="rounded-md border border-slate-300 px-3 py-1.5 disabled:opacity-40">
                Next &rarr;
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
