import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { listBookmarks } from '../lib/api'
import { Marks, useProgress } from '../lib/progress'

const TABS = [
  { tache: 2, label: 'Task 2' },
  { tache: 3, label: 'Task 3' },
]

function whenBookmarked(iso) {
  if (!iso) return ''
  // "2026-09-18T19:08:33" -> "18 Sep 2026", no date library
  const [y, m, d] = iso.slice(0, 10).split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${Number(d)} ${months[Number(m) - 1] ?? m} ${y}`
}

export default function Bookmarks() {
  const [tache, setTache] = useState(2)
  const bookmarksKey = ['bookmarks', tache]

  // toggling a bookmark invalidates ['bookmarks'] from inside useProgress, so
  // un-starring a row here drops it from the list without extra wiring
  const { user, practiced, bookmarked, togglePracticed, toggleBookmarked } =
    useProgress(tache)

  const { data, isPending, isError, error } = useQuery({
    queryKey: bookmarksKey,
    queryFn: () => listBookmarks({ tache }),
    enabled: !!user,
  })

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold tracking-tight">Oral bookmarks</h1>
        <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
          <Link to="/login" className="font-medium text-sky-700 hover:underline">Sign in</Link>
          {' '}to save questions and come back to them.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Oral bookmarks</h1>
        <p className="mt-1 text-sm text-slate-600">
          Questions you saved, most recent first. A bookmark is on the question, so
          it covers every month that question has appeared in.
        </p>
      </div>

      <div className="flex gap-1">
        {TABS.map((t) => (
          <button
            key={t.tache}
            onClick={() => setTache(t.tache)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
              t.tache === tache ? 'bg-slate-900 text-white'
                                : 'text-slate-600 hover:bg-slate-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {isError && (
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">
          Could not load bookmarks: {error.message}
        </p>
      )}

      {isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : data.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
          Nothing saved for Task {tache} yet. Use the{' '}
          <span className="text-amber-500">★</span> on any question to save it.
        </p>
      ) : (
        <>
          <p className="text-sm text-slate-500">
            {data.length} bookmark{data.length === 1 ? '' : 's'}
          </p>
          <ol className="space-y-3">
            {data.map((q) => {
              // read the live sets, not the row's own flags: an optimistic
              // toggle updates those immediately, while this list only
              // refreshes once the request settles
              const isDone = practiced.has(q.f_id)
              return (
                <li
                  key={q.f_id}
                  className={`flex items-start gap-3 rounded-lg border p-4 ${
                    isDone ? 'border-emerald-200 bg-emerald-50/40' : 'border-slate-200 bg-white'
                  }`}
                >
                  <div className="min-w-0 flex-1">
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
                      {q.months_seen > 1 && (
                        <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-800">
                          asked in {q.months_seen} months
                        </span>
                      )}
                      <span className="text-slate-400">
                        saved {whenBookmarked(q.bookmarked_at)}
                      </span>
                    </div>
                    <p className="leading-relaxed">{q.text}</p>
                  </div>

                  <Marks
                    user={user} fId={q.f_id}
                    practiced={practiced} bookmarked={bookmarked}
                    togglePracticed={togglePracticed}
                    toggleBookmarked={toggleBookmarked}
                  />
                </li>
              )
            })}
          </ol>
        </>
      )}
    </div>
  )
}
