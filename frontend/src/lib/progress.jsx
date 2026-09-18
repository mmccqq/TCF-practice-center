import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addBookmark, getProgress, markPracticed, removeBookmark, unmarkPracticed,
} from './api'
import { useAuth } from './auth'

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
 * `extraInvalidate` lets a caller refresh a second query - the bookmarks page
 * has to re-fetch its rows when one is un-bookmarked.
 */
export function useProgress(tache, { extraInvalidate } = {}) {
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
    togglePracticed: useToggle(key, 'practiced', markPracticed, unmarkPracticed,
                               extraInvalidate),
    toggleBookmarked: useToggle(key, 'bookmarked', addBookmark, removeBookmark,
                                extraInvalidate),
  }
}

// a hook, so it is called at the top level of useProgress rather than inside a
// closure - two calls, always in the same order, which is what React requires
function useToggle(key, field, on, off, extraInvalidate) {
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
      if (extraInvalidate) qc.invalidateQueries({ queryKey: extraInvalidate })
    },
  })
}
