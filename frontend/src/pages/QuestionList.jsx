import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { listQuestions, questionsMeta } from '../lib/api'

const PER_PAGE = 25

// the only ordering the list offers: newest month first, id descending within
// it. The API still accepts date_asc/frequency; nothing here asks for them.
const SORT = 'date_desc'

function periodLabel(period) {
  // "2026-08" -> "Aug 2026", without pulling in a date library
  const [y, m] = period.split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${months[Number(m) - 1] ?? m} ${y}`
}

function groupByPeriod(items) {
  // [[period, items], ...] in the order the periods first appear. The API
  // returns rows newest first, so that is one contiguous run per month, newest
  // month at the top. Grouping runs over every page loaded so far, so a month
  // spanning a page boundary stays one section and the next page extends it
  // instead of repeating its heading.
  const groups = new Map()
  for (const q of items) {
    if (!groups.has(q.period)) groups.set(q.period, [])
    groups.get(q.period).push(q)
  }
  return [...groups]
}

export default function QuestionList({ tache }) {
  const [params, setParams] = useSearchParams()
  const urlQ = params.get('q') || ''
  const theme = params.get('theme') || ''

  // local state so typing does not fire a request per keystroke
  const [draft, setDraft] = useState(urlQ)
  useEffect(() => setDraft(urlQ), [urlQ])

  const update = (patch) => {
    const next = new URLSearchParams(params)
    Object.entries(patch).forEach(([k, v]) => {
      if (v) next.set(k, v)
      else next.delete(k)
    })
    setParams(next)
  }

  // the theme list is per task: Task 2 and Task 3 do not share a vocabulary
  const { data: meta } = useQuery({
    queryKey: ['meta', tache],
    queryFn: () => questionsMeta({ tache }),
  })

  const query = { tache, per_page: PER_PAGE, sort: SORT }
  if (urlQ) query.q = urlQ
  if (theme) query.theme = theme

  const {
    data, isPending, isError, error, isFetching,
    fetchNextPage, hasNextPage, isFetchingNextPage,
  } = useInfiniteQuery({
    // the page number is deliberately not in the key: all pages of one filter
    // share a cache entry, and changing a filter starts a new one from page 1
    queryKey: ['questions', query],
    queryFn: ({ pageParam }) => listQuestions({ ...query, page: pageParam }),
    initialPageParam: 1,
    // undefined means "no more", which is what hasNextPage reads
    getNextPageParam: (last) => (last.page < last.pages ? last.page + 1 : undefined),
  })

  const items = data ? data.pages.flatMap((p) => p.items) : []
  const total = data?.pages[0]?.total ?? 0

  // load the next page when the sentinel below the list comes into view
  const sentinel = useRef(null)
  useEffect(() => {
    const node = sentinel.current
    if (!node || !hasNextPage) return
    const io = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) fetchNextPage() },
      // fire before the sentinel is actually on screen, so the next page is
      // usually there by the time the user reaches the bottom
      { rootMargin: '400px' },
    )
    io.observe(node)
    return () => io.disconnect()
    // isFetchingNextPage re-runs this after each load: if the sentinel is still
    // in view (a short page on a tall screen) the new observer fires again
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

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
        <button className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800">
          Search
        </button>
        {/* hidden until something is labelled: an "All themes" dropdown with
            nothing in it is a dead control (Task 3 has no labels yet) */}
        {(meta?.themes?.length > 0 || theme) && (
          <select
            value={theme}
            onChange={(e) => update({ theme: e.target.value })}
            className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            <option value="">All themes</option>
            {(meta?.themes ?? []).map((t) => (
              <option key={t.name} value={t.name}>{t.name} ({t.count})</option>
            ))}
            {/* a theme from the URL that the list does not have, so the select
                never silently shows "All themes" while filtering by something */}
            {theme && !(meta?.themes ?? []).some((t) => t.name === theme) && (
              <option value={theme}>{theme}</option>
            )}
          </select>
        )}
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
            Showing {items.length.toLocaleString()} of {total.toLocaleString()}{' '}
            question{total === 1 ? '' : 's'}
            {isFetching && !isFetchingNextPage && (
              <span className="ml-2 text-slate-400">updating…</span>
            )}
          </p>

          <div className="space-y-6">
            {groupByPeriod(items).map(([period, group]) => (
              <section key={period}>
                <h2 className="sticky top-0 z-10 flex items-baseline gap-2 border-b
                               border-slate-200 bg-slate-50/90 py-1.5 text-sm
                               font-semibold text-slate-700 backdrop-blur">
                  {periodLabel(period)}
                  <span className="font-normal text-slate-400">
                    {group.length} question{group.length === 1 ? '' : 's'}
                  </span>
                </h2>

                <ol className="mt-3 space-y-3">
                  {group.map((q) => (
                    <li key={q.id} className="rounded-lg border border-slate-200 bg-white p-4">
                      {(q.theme || q.core_subject || q.occurrences > 1) && (
                        <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs">
                          {q.theme && (
                            <span className="rounded bg-sky-100 px-1.5 py-0.5 font-medium text-sky-800">
                              {q.theme}
                            </span>
                          )}
                          {q.core_subject && (
                            <span className="rounded bg-violet-100 px-1.5 py-0.5 text-violet-800">
                              {q.core_subject}
                            </span>
                          )}
                          {q.occurrences > 1 && (
                            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">
                              seen {q.occurrences}&times;
                            </span>
                          )}
                        </div>
                      )}
                      <p className="leading-relaxed">{q.text}</p>
                    </li>
                  ))}
                </ol>
              </section>
            ))}
          </div>

          {items.length === 0 && (
            <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
              No questions match those filters.
            </p>
          )}

          {/* also a real button: the observer covers scrolling, this covers
              keyboard users and the case where it never fires */}
          <div ref={sentinel} className="pt-2 text-center">
            {hasNextPage ? (
              <button
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm disabled:opacity-40"
              >
                {isFetchingNextPage ? 'Loading…' : 'Load more'}
              </button>
            ) : items.length > 0 ? (
              <p className="text-sm text-slate-400">End of the list</p>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  )
}
