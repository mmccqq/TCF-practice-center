import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  addBookmark, getProgress, markPracticed, removeBookmark, unmarkPracticed,
} from './api'
import { useAuth } from './auth'

/**
 * A done/total bar.
 *
 * `compact` is the inline version that fits in a section heading; the default
 * is the wider one for a page header. Renders nothing at total 0 rather than a
 * divide-by-zero or an empty bar implying there is something to do.
 */
export function ProgressBar({ done, total, compact = false, label }) {
  if (!total) return null
  const pct = Math.round((done / total) * 100)
  return (
    <span className={`flex items-center gap-2 ${compact ? 'text-xs' : 'text-sm'}`}>
      <span
        role="progressbar"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label={label || `${done} of ${total} practised`}
        className={`overflow-hidden rounded-full bg-slate-200 ${
          compact ? 'h-1.5 w-20' : 'h-2 w-40'
        }`}
      >
        <span
          className="block h-full rounded-full bg-emerald-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className={done === total ? 'font-medium text-emerald-700' : 'text-slate-500'}>
        {done}/{total}
      </span>
    </span>
  )
}

const PIECES = 18
const CONFETTI_COLORS = ['#f59e0b', '#10b981', '#38bdf8', '#a78bfa', '#fb7185', '#facc15']

/** One burst of paper. Positioned over its parent, which must be `relative`. */
function Confetti() {
  // fixed for the life of this burst, or every re-render would reshuffle the
  // scraps mid-flight
  const pieces = useMemo(
    () => Array.from({ length: PIECES }, (_, i) => ({
      angle: (360 / PIECES) * i + (Math.random() * 24 - 12),
      distance: 34 + Math.random() * 32,
      spin: Math.random() * 540 - 270,
      delay: Math.random() * 60,
      color: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
    })),
    [],
  )
  return (
    <span aria-hidden="true" className="pointer-events-none absolute inset-0">
      {pieces.map((p, i) => (
        <span
          key={i}
          className="confetti-piece"
          style={{
            '--angle': `${p.angle}deg`,
            '--distance': `${p.distance}px`,
            '--spin': `${p.spin}deg`,
            animationDelay: `${p.delay}ms`,
            background: p.color,
          }}
        />
      ))}
    </span>
  )
}

/**
 * The bookmark star and the practised tick for one question.
 *
 * Lives here because all three pages render this same pair, and a fourth copy
 * of the JSX is where the three would start to disagree.
 *
 * Marking something practised fires a confetti burst; un-marking does not,
 * because undoing is not an achievement.
 */
export function Marks({ user, fId, practiced, bookmarked, togglePracticed,
                        toggleBookmarked, small = false }) {
  const isDone = practiced.has(fId)
  const isSaved = bookmarked.has(fId)
  // a counter, not a boolean: the key restarts the animation when the same
  // button is clicked again before the previous burst has finished
  const [burst, setBurst] = useState(0)

  useEffect(() => {
    if (!burst) return undefined
    const t = setTimeout(() => setBurst(0), 1000)
    return () => clearTimeout(t)
  }, [burst])

  if (!user) {
    return (
      <Link
        to="/login"
        title="Sign in to bookmark questions and track your progress"
        aria-label="Sign in to track your progress"
        className={`flex shrink-0 gap-1 rounded-full p-0.5 text-slate-200
                    hover:text-slate-400 ${small ? 'scale-75' : ''}`}
      >
        <StarIcon done={false} />
        <CheckIcon done={false} />
      </Link>
    )
  }

  return (
    <div className={`flex shrink-0 items-center gap-1 ${small ? 'scale-75' : ''}`}>
      <button
        type="button"
        onClick={() => toggleBookmarked.mutate({ fId, next: !isSaved })}
        aria-pressed={isSaved}
        aria-label={isSaved ? 'Bookmarked' : 'Bookmark this question'}
        title={isSaved ? 'Bookmarked — click to remove' : 'Bookmark'}
        className={`rounded-full p-0.5 transition ${
          isSaved ? 'text-amber-500 hover:text-amber-600'
                  : 'text-slate-300 hover:text-slate-500'
        }`}
      >
        <StarIcon done={isSaved} />
      </button>
      <button
        type="button"
        onClick={() => {
          if (!isDone) setBurst((n) => n + 1)
          togglePracticed.mutate({ fId, next: !isDone })
        }}
        aria-pressed={isDone}
        aria-label={isDone ? 'Practised' : 'Mark as practised'}
        title={isDone ? 'Practised — click to undo' : 'Mark as practised'}
        className={`relative rounded-full p-0.5 transition ${
          isDone ? 'text-emerald-600 hover:text-emerald-700'
                 : 'text-slate-300 hover:text-slate-500'
        }`}
      >
        <CheckIcon done={isDone} />
        {burst > 0 && <Confetti key={burst} />}
      </button>
    </div>
  )
}

export function CheckIcon({ done }) {
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none"
         stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill={done ? 'currentColor' : 'none'} />
      <path d="M8 12.4l2.6 2.6L16 9.6" strokeLinecap="round" strokeLinejoin="round"
            stroke={done ? '#fff' : 'currentColor'} />
    </svg>
  )
}

export function StarIcon({ done }) {
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6" fill={done ? 'currentColor' : 'none'}
         stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M12 3.6l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.5 9.8l5.9-.9z"
            strokeLinejoin="round" />
    </svg>
  )
}

/**
 * The two id sets for one task, plus toggles for each.
 *
 * One request rather than one per card: the API returns flat f_id lists, so a
 * page of 25 cards costs nothing extra. Both toggles are optimistic and share
 * the same cache entry, which is what makes twelve cards of a recurring
 * question flip together from one click.
 *
 * A bookmark toggle also invalidates the bookmarks list, wherever it was
 * triggered from. Without that, starring a question on the list page leaves
 * ['bookmarks'] holding a cached result that staleTime (60s) considers fresh,
 * so opening the bookmarks page shows yesterday's answer until it expires or
 * the tab is reloaded.
 */
export function useProgress(tache) {
  const { user } = useAuth()
  const qc = useQueryClient()
  const key = ['progress', tache]

  const { data } = useQuery({
    queryKey: key,
    queryFn: () => getProgress(tache ? { tache } : {}),
    enabled: !!user,
  })

  const practiced = new Set(data?.practiced ?? [])
  const bookmarked = new Set(data?.bookmarked ?? [])

  return {
    user,
    practiced,
    bookmarked,
    togglePracticed: useToggle(key, 'practiced', markPracticed, unmarkPracticed),
    toggleBookmarked: useToggle(key, 'bookmarked', addBookmark, removeBookmark),
  }
}

// a hook, so it is called at the top level of useProgress rather than inside a
// closure - two calls, always in the same order, which is what React requires
function useToggle(key, field, on, off) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ fId, next }) => (next ? on(fId) : off(fId)),
    onMutate: async ({ fId, next }) => {
      await qc.cancelQueries({ queryKey: key })
      const prev = qc.getQueryData(key)
      qc.setQueryData(key, (old) => {
        const base = old ?? { practiced: [], bookmarked: [] }
        const list = next
          ? [...base[field], fId]
          : base[field].filter((x) => x !== fId)
        return { ...base, [field]: list }
      })
      return { prev }
    },
    onError: (_e, _v, ctx) => qc.setQueryData(key, ctx?.prev),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: key })
      // prefix match, so both tasks' bookmark lists are refreshed. Practising
      // does not affect membership, so it does not need this - the bookmarks
      // page reads the tick state from the id sets above, not from its rows.
      if (field === 'bookmarked') qc.invalidateQueries({ queryKey: ['bookmarks'] })
    },
  })
}
