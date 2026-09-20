import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  adminBulkLabel, adminExport, adminQuestionIds, adminQuestions, adminSetLabels,
  adminVocabulary, invalidatePublic,
} from '../../lib/api'
import { TacheTabs } from './AdminLayout'
import RunJobDialog from './RunJobDialog'

const PER_PAGE = 50

export default function Labelling() {
  const [tache, setTache] = useState(2)
  const [filters, setFilters] = useState({
    unlabelled: 'theme', q: '', theme_id: '', core_subject_id: '', inconsistent: false,
  })
  const [page, setPage] = useState(1)
  const [picked, setPicked] = useState(() => new Set())
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const qc = useQueryClient()

  const params = { tache, page, per_page: PER_PAGE }
  if (filters.q) params.q = filters.q
  if (filters.theme_id) params.theme_id = filters.theme_id
  if (filters.core_subject_id) params.core_subject_id = filters.core_subject_id
  if (filters.unlabelled) params.unlabelled = filters.unlabelled
  if (filters.inconsistent) params.inconsistent = true
  const key = ['admin-questions', params]

  const { data: vocab } = useQuery({
    queryKey: ['admin-vocabulary', tache],
    queryFn: () => adminVocabulary({ tache }),
  })
  const { data, isPending } = useQuery({ queryKey: key, queryFn: () => adminQuestions(params) })

  // Patch the row in the cache instead of refetching the list.
  //
  // Two problems this solves. The selects are controlled by the cached row, so
  // without an optimistic update the control snaps back to its old value the
  // moment you change it and stays there until the round trip lands. And
  // refetching under the default "Missing theme" filter makes the row you just
  // labelled vanish - correct for the filter, but indistinguishable from the
  // edit having failed.
  //
  // The row now updates in place and stays put. It drops out of the list the
  // next time the filters or page change, which is when a list is expected to
  // change.
  const patchRow = (fId, patch) =>
    qc.setQueryData(key, (old) => old && {
      ...old,
      items: old.items.map((r) => (r.f_id === fId ? { ...r, ...patch } : r)),
    })

  const save = useMutation({
    mutationFn: ({ fId, body }) => adminSetLabels(fId, body),
    onMutate: async ({ fId, preview }) => {
      setError('')
      await qc.cancelQueries({ queryKey: key })
      const prev = qc.getQueryData(key)
      patchRow(fId, preview)
      return { prev }
    },
    onError: (e, _vars, ctx) => { qc.setQueryData(key, ctx?.prev); setError(e.message) },
    onSuccess: (row) => {
      patchRow(row.f_id, row)        // the server's version wins
      qc.invalidateQueries({ queryKey: ['admin-vocabulary'] })
      invalidatePublic(qc)
    },
  })

  const bulk = useMutation({
    mutationFn: (body) => adminBulkLabel(body),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
    onSuccess: (res, body) => {
      const t = themes.find((x) => x.id === body.theme_id)
      body.f_ids.forEach((fId) =>
        patchRow(fId, { theme_id: t?.id ?? null, theme: t?.name ?? null,
                        core_subject_id: null, core_subject: null }))
      if (res.skipped_wrong_theme) {
        setError(`${res.skipped_wrong_theme} question(s) were skipped: that core `
                 + `subject belongs to a different theme.`)
      }
      qc.invalidateQueries({ queryKey: ['admin-vocabulary'] })
      invalidatePublic(qc)
    },
  })

  const themes = vocab?.themes ?? []
  const subjectsFor = (themeId) =>
    themes.find((t) => t.id === themeId)?.core_subjects ?? []

  const setFilter = (patch) => { setFilters((f) => ({ ...f, ...patch })); setPage(1) }
  const toggle = (id) => setPicked((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  // this page's rows, not the whole filter: selecting 826 questions from a
  // button that shows 50 would be a promise the screen cannot back up
  const pageIds = (data?.items ?? []).map((r) => r.f_id)
  const allPicked = pageIds.length > 0 && pageIds.every((id) => picked.has(id))
  const toggleAll = () => setPicked((prev) => {
    const next = new Set(prev)
    pageIds.forEach((id) => (allPicked ? next.delete(id) : next.add(id)))
    return next
  })

  // the same filter the page is showing, every field, every matching row - not
  // capped, because an export costs nothing and a partial one is a trap. The
  // spreadsheet is built server-side and arrives named.
  const exportAll = useMutation({
    mutationFn: () => adminExport(params),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
  })

  // everything the filter matches, not just the page. Fetched on demand rather
  // than with the list: it is a few hundred integers nobody needs until they
  // ask for them.
  const selectMatching = useMutation({
    mutationFn: () => adminQuestionIds(params),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
    onSuccess: (res) => {
      setPicked(new Set(res.ids))
      if (res.capped) {
        setError(`Selected the first ${res.ids.length} of ${res.total} — that is `
                 + `the most one job may cover. Run these, then select again.`)
      }
    },
  })

  const pages = data ? Math.max(1, Math.ceil(data.total / PER_PAGE)) : 1

  return (
    <div className="space-y-4">
      {running && (
        <RunJobDialog
          tache={tache}
          // the job labels whichever field the filter is hunting for; with no
          // filter, the theme is the one everything else depends on
          task={filters.unlabelled || 'theme'}
          fIds={[...picked]}
          onClose={() => setRunning(false)}
        />
      )}
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
          onChange={(e) => setFilter({ theme_id: e.target.value, core_subject_id: '' })}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
        >
          <option value="">Any theme</option>
          {themes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select
          value={filters.core_subject_id}
          onChange={(e) => setFilter({ core_subject_id: e.target.value })}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
        >
          <option value="">Any core subject</option>
          {/* with a theme chosen, just its labels; otherwise every label,
              grouped, because the same name can exist under two themes and a
              flat list would show it twice with nothing to tell them apart */}
          {filters.theme_id
            ? subjectsFor(Number(filters.theme_id)).map((c) => (
                <option key={c.id} value={c.id}>{c.name} ({c.usage})</option>
              ))
            : themes.map((t) => (
                <optgroup key={t.id} label={t.name}>
                  {t.core_subjects.map((c) => (
                    <option key={c.id} value={c.id}>{c.name} ({c.usage})</option>
                  ))}
                </optgroup>
              ))}
        </select>
        <form onSubmit={(e) => { e.preventDefault(); setFilter({ q: new FormData(e.target).get('q') }) }}>
          <input name="q" defaultValue={filters.q} placeholder="Search text…"
                 className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
        </form>
        {/* questions whose core subject belongs to a different theme. No
            current endpoint can create one, but older loads did - four of them
            - and they are invisible without somewhere to look. */}
        <label className="flex items-center gap-1 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={filters.inconsistent}
            onChange={(e) => setFilter({ inconsistent: e.target.checked })}
          />
          mismatched theme
        </label>
        <span className="text-sm text-slate-500">
          {data ? `${data.total.toLocaleString()} question${data.total === 1 ? '' : 's'}` : ''}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={toggleAll}
            disabled={!pageIds.length}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
          >
            {allPicked ? 'Clear page' : `Select all ${pageIds.length}`}
          </button>
          {data && data.total > pageIds.length && (
            <button
              onClick={() => selectMatching.mutate()}
              disabled={selectMatching.isPending}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
            >
              {selectMatching.isPending
                ? 'Selecting…'
                : `Select all ${data.total.toLocaleString()} matching`}
            </button>
          )}
          <button
            onClick={() => exportAll.mutate()}
            disabled={!data?.total || exportAll.isPending}
            title="Download every matching question as a spreadsheet, all fields"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
          >
            {exportAll.isPending ? 'Preparing…' : 'Export .xlsx'}
          </button>
          <button
            onClick={() => setRunning(true)}
            disabled={!picked.size}
            title={picked.size ? '' : 'Select some questions first'}
            className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white
                       disabled:opacity-40"
          >
            Run a job{picked.size ? ` (${picked.size})` : ''}
          </button>
        </div>
      </div>

      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {/* bulk bar appears only with a selection, so it never competes for space */}
      {picked.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-sky-300
                        bg-sky-50 p-3 text-sm">
          <strong>{picked.size} selected</strong>
          {picked.size > pageIds.filter((id) => picked.has(id)).length && (
            <span className="text-slate-500">
              ({picked.size - pageIds.filter((id) => picked.has(id)).length} on other pages)
            </span>
          )}
          <span className="text-slate-600">assign theme:</span>
          <select
            defaultValue=""
            onChange={(e) => {
              if (!e.target.value) return
              bulk.mutate({ f_ids: [...picked], theme_id: Number(e.target.value) })
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
                      onChange={(e) => {
                        const id = e.target.value ? Number(e.target.value) : null
                        const t = themes.find((x) => x.id === id)
                        save.mutate({
                          fId: row.f_id,
                          body: { theme_id: id },
                          // the server clears the core subject when the theme
                          // moves, so the preview has to as well
                          preview: { theme_id: id, theme: t?.name ?? null,
                                     core_subject_id: null, core_subject: null },
                        })
                      }}
                      className="rounded border border-slate-300 bg-white px-2 py-1 text-xs"
                    >
                      <option value="">— theme —</option>
                      {themes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                    <select
                      value={row.core_subject_id ?? ''}
                      disabled={!row.theme_id}
                      onChange={(e) => {
                        const id = e.target.value ? Number(e.target.value) : null
                        const c = subjectsFor(row.theme_id).find((x) => x.id === id)
                        save.mutate({
                          fId: row.f_id,
                          body: { core_subject_id: id },
                          preview: { core_subject_id: id, core_subject: c?.name ?? null },
                        })
                      }}
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
                        const v = e.target.value
                        if (v !== (row.abstract ?? '')) {
                          save.mutate({ fId: row.f_id, body: { abstract: v },
                                        preview: { abstract: v.trim() || null } })
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
