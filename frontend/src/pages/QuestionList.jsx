import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { listQuestions, questionsMeta } from '../lib/api'

const PER_PAGE = 25

const SORT_LABELS = {
  date_desc: 'Newest first',
  date_asc: 'Oldest first',
  frequency: 'Most frequent',
}

function periodLabel(period) {
  // "2026-08" -> "Aug 2026", without pulling in a date library
  const [y, m] = period.split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${months[Number(m) - 1] ?? m} ${y}`
}

export default function QuestionList({ tache }) {
  const [params, setParams] = useSearchParams()
  const page = Math.max(1, Number(params.get('page')) || 1)
  const sort = SORT_LABELS[params.get('sort')] ? params.get('sort') : 'date_desc'
  const source = params.get('source') || ''
  const urlQ = params.get('q') || ''

  // local state so typing does not fire a request per keystroke
  const [draft, setDraft] = useState(urlQ)
  useEffect(() => setDraft(urlQ), [urlQ])

  const update = (patch, { resetPage = true } = {}) => {
    const next = new URLSearchParams(params)
    Object.entries(patch).forEach(([k, v]) => {
      if (v) next.set(k, v)
      else next.delete(k)
    })
    if (resetPage) next.delete('page')
    setParams(next)
  }

  const { data: meta } = useQuery({ queryKey: ['meta'], queryFn: questionsMeta })

  const query = { tache, page, per_page: PER_PAGE, sort }
  if (urlQ) query.q = urlQ
  if (source) query.source = source

  const { data, isPending, isError, error, isFetching } = useQuery({
    queryKey: ['questions', query],
    queryFn: () => listQuestions(query),
    // keep the old page visible while the next one loads, instead of flashing
    // an empty list on every pagination click
    placeholderData: keepPreviousData,
  })

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          Expression orale &mdash; Task {tache}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          {tache === 2
            ? 'You ask the questions: gather information in an everyday situation.'
            : 'You give an opinion and justify it on a general topic.'}
        </p>
      </div>

      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => { e.preventDefault(); update({ q: draft }) }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Search the question text…"
          className="min-w-56 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm
                     focus:border-sky-500 focus:outline-none"
        />
        <select
          value={sort}
          onChange={(e) => update({ sort: e.target.value })}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          {Object.entries(SORT_LABELS).map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <select
          value={source}
          onChange={(e) => update({ source: e.target.value })}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">All sources</option>
          {(meta?.sources ?? []).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <button className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800">
          Search
        </button>
      </form>

      {isError && (
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">
          Could not load questions: {error.message}
        </p>
      )}

      {isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : data ? (
        <>
          <p className="text-sm text-slate-500">
            {data.total.toLocaleString()} question{data.total === 1 ? '' : 's'}
            {isFetching && <span className="ml-2 text-slate-400">updating…</span>}
          </p>

          <ol className="space-y-3">
            {data.items.map((q) => (
              <li key={q.id} className="rounded-lg border border-slate-200 bg-white p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-700">
                    {periodLabel(q.period)}
                  </span>
                  <span>{q.source}</span>
                  {q.occurrences > 1 && (
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">
                      seen {q.occurrences}&times;
                    </span>
                  )}
                  <span className="ml-auto font-mono text-[11px] text-slate-400">{q.id}</span>
                </div>
                <p className="leading-relaxed">{q.text}</p>
              </li>
            ))}
          </ol>

          {data.items.length === 0 && (
            <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
              No questions match those filters.
            </p>
          )}

          {data.pages > 1 && (
            <div className="flex items-center justify-between pt-2">
              <button
                disabled={page <= 1}
                onClick={() => update({ page: String(page - 1) }, { resetPage: false })}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
              >
                &larr; Previous
              </button>
              <span className="text-sm text-slate-500">
                Page {data.page} of {data.pages}
              </span>
              <button
                disabled={page >= data.pages}
                onClick={() => update({ page: String(page + 1) }, { resetPage: false })}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
              >
                Next &rarr;
              </button>
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}
