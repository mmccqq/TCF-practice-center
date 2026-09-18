import {
  useInfiniteQuery, useMutation, useQuery, useQueryClient,
} from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  listAttempts, listQuestions, markPracticed, questionsMeta, unmarkPracticed,
} from '../lib/api'
import { useAuth } from '../lib/auth'

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

function CheckIcon({ done }) {
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none"
         stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill={done ? 'currentColor' : 'none'} />
      <path d="M8 12.4l2.6 2.6L16 9.6" strokeLinecap="round" strokeLinejoin="round"
            stroke={done ? '#fff' : 'currentColor'} />
    </svg>
  )
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

  const { user } = useAuth()
  const qc = useQueryClient()

  // one flat list of practised f_ids for the whole task, fetched once, instead
  // of a request per card. Skipped entirely when nobody is signed in.
  const attemptsKey = ['attempts', tache]
  const { data: attempts } = useQuery({
    queryKey: attemptsKey,
    queryFn: () => listAttempts({ tache }),
    enabled: !!user,
  })
  const done = new Set(attempts ?? [])

  // optimistic: the tick flips immediately and rolls back if the request fails.
  // Because the key is f_id, every card for the same question flips together -
  // a question asked in twelve months shows twelve ticks from one click.
  const toggle = useMutation({
    mutationFn: ({ fId, next }) => (next ? markPracticed(fId) : unmarkPracticed(fId)),
    onMutate: async ({ fId, next }) => {
      await qc.cancelQueries({ queryKey: attemptsKey })
      const prev = qc.getQueryData(attemptsKey)
      qc.setQueryData(attemptsKey, (old = []) =>
        next ? [...old, fId] : old.filter((x) => x !== fId))
      return { prev }
    },
    onError: (_err, _vars, ctx) => qc.setQueryData(attemptsKey, ctx?.prev),
    onSettled: () => qc.invalidateQueries({ queryKey: attemptsKey }),
  })

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
            {/* "monthly entries", not "questions": a question asked in eight
                months is eight rows here. The home page advertises distinct
                questions (meta.counts), which is a smaller number. */}
            Showing {items.length.toLocaleString()} of {total.toLocaleString()}{' '}
            monthly entr{total === 1 ? 'y' : 'ies'}
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
                  {group.map((q) => {
                    const isDone = done.has(q.f_id)
                    return (
                    <li
                      key={q.id}
                      className={`flex items-start gap-3 rounded-lg border p-4 ${
                        isDone ? 'border-emerald-200 bg-emerald-50/40' : 'border-slate-200 bg-white'
                      }`}
                    >
                     <div className="min-w-0 flex-1">
                      {(q.theme || q.core_subject || q.month_sightings > 1
                        || q.months_seen > 1) && (
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
                          {/* two different numbers: how often it came up in
                              THIS month, and how many months it has appeared
                              in overall. The second one is the useful signal
                              and the old single table could not express it. */}
                          {q.month_sightings > 1 && (
                            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">
                              {q.month_sightings}&times; this month
                            </span>
                          )}
                          {q.months_seen > 1 && (
                            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-800">
                              asked in {q.months_seen} months
                            </span>
                          )}
                        </div>
                      )}
                      <p className="leading-relaxed">{q.text}</p>
                     </div>

                      {/* signed out, the tick is a link to sign in rather than a
                          dead control or a silent 401 */}
                      {user ? (
                        <button
                          type="button"
                          onClick={() => toggle.mutate({ fId: q.f_id, next: !isDone })}
                          aria-pressed={isDone}
                          aria-label={isDone ? 'Practised' : 'Mark as practised'}
                          title={isDone
                            ? 'Practised — click to undo. Covers every month this question appears in.'
                            : 'Mark as practised'}
                          className={`shrink-0 rounded-full p-0.5 transition ${
                            isDone ? 'text-emerald-600 hover:text-emerald-700'
                                   : 'text-slate-300 hover:text-slate-500'
                          }`}
                        >
                          <CheckIcon done={isDone} />
                        </button>
                      ) : (
                        <Link
                          to="/login"
                          title="Sign in to track what you have practised"
                          aria-label="Sign in to track your progress"
                          className="shrink-0 rounded-full p-0.5 text-slate-200 hover:text-slate-400"
                        >
                          <CheckIcon done={false} />
                        </Link>
                      )}
                    </li>
                    )
                  })}
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
