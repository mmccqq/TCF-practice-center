import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { frequentSubjects } from '../lib/api'
import { loginHref } from '../lib/auth'
import { Marks, ProgressBar, useProgress } from '../lib/progress'

const MIN_QUESTIONS = 2

// stable, URL-safe id per theme, for the "next unfinished" jump
const themeAnchor = (name) =>
  'theme-' + name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

export default function Frequent() {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  // which cards are expanded, by core subject name. Local state rather than the
  // URL: it is a reading aid, not something worth linking to.
  const [open, setOpen] = useState(() => new Set())
  const tache = params.get('tache') === '3' ? 3 : 2

  const { user, practiced, bookmarked, togglePracticed, toggleBookmarked } =
    useProgress(tache)

  const { data, isPending, isError, error } = useQuery({
    queryKey: ['frequent', tache],
    queryFn: () => frequentSubjects({ tache, min_questions: MIN_QUESTIONS }),
  })

  // one subject = one card = one unit of progress, counted on its
  // representative. The denominator is what is on screen, so ticking every card
  // in a theme fills that theme's bar - which is the point of the page.
  // Counting all the questions behind each subject instead would leave the bar
  // at a fraction after the user had visibly finished everything shown.
  const themeProgress = new Map()
  let doneAll = 0
  let totalAll = 0
  for (const t of data?.themes ?? []) {
    const done = t.subjects.filter((s) => practiced.has(s.f_id)).length
    themeProgress.set(t.theme, { done, total: t.subjects.length })
    doneAll += done
    totalAll += t.subjects.length
  }

  // the first theme with anything left in it, in the page's own order - so
  // "continue" always lands on the highest-value unfinished work
  const nextTheme = (data?.themes ?? []).find(
    (t) => (themeProgress.get(t.theme)?.done ?? 0) < t.subjects.length)?.theme

  const toggleOpen = (name) => setOpen((prev) => {
    const next = new Set(prev)
    next.has(name) ? next.delete(name) : next.add(name)
    return next
  })

  const setTache = (t) => {
    const next = new URLSearchParams(params)
    next.set('tache', String(t))
    setParams(next)
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Oral core set</h1>
        {/* the shared styling lives on the wrapper, so the paragraphs cannot
            drift apart and space-y controls the gap in one place */}
        <div className="mt-2 max-w-2xl space-y-2 text-sm text-slate-600">
          <p>
            The subjects the exam keeps coming back to, but this core set mean to save you.
          </p>
          <p>
            Tick a card to mark that subject done; finish every card in a theme and
            the theme is done. Good luck!
          </p>
        </div>
      </div>

      <div className="flex gap-1">
        {[2, 3].map((t) => (
          <button
            key={t}
            onClick={() => setTache(t)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
              t === tache ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-200'
            }`}
          >
            Task {t}
          </button>
        ))}
      </div>

      {isError && (
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">
          Could not load: {error.message}
        </p>
      )}

      {isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : data.themes.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
          Task {tache} has no labelled questions yet, so there is nothing to group.
          {' '}{data.total.toLocaleString()} questions are waiting on a labelling run.
        </p>
      ) : (
        <>
          {/* the page can only see labelled questions, and saying so is the
              difference between "this is the bank" and "this is part of it" */}
          <p className="text-sm text-slate-500">
            Browse all questions on the{' '}
            <Link to={`/speaking/task${tache}`} className="text-sky-700 hover:underline">
              Task {tache} list
            </Link>.
          </p>

          {user ? (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border
                            border-slate-200 bg-white p-4">
              <span className="text-sm font-medium">Your progress</span>
              <ProgressBar done={doneAll} total={totalAll}
                           label={`${doneAll} of ${totalAll} core subjects practised`} />
              <span className="text-sm text-slate-500">
                {totalAll ? Math.round((doneAll / totalAll) * 100) : 0}% of the core
                subjects
              </span>
              {/* daily use means picking up where you stopped, not re-finding
                  your place by scrolling past themes you have finished */}
              {nextTheme ? (
                <a
                  href={`#${themeAnchor(nextTheme)}`}
                  className="ml-auto rounded-md bg-sky-600 px-3 py-1.5 text-sm
                             font-medium text-white transition hover:bg-sky-700"
                >
                  {doneAll > 0 ? 'Continue' : 'Start'} with {nextTheme} &rarr;
                </a>
              ) : (
                <span className="ml-auto rounded-md bg-emerald-100 px-3 py-1.5 text-sm
                                 font-medium text-emerald-800">
                  All {totalAll} subjects done
                </span>
              )}
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-slate-300 p-3 text-sm text-slate-500">
              <Link to={loginHref(location)} className="font-medium text-sky-700 hover:underline">Sign in</Link>
              {' '}to track which of these you have practised.
            </p>
          )}

          <div className="space-y-6">
            {data.themes.map((t) => (
              <section key={t.theme} id={themeAnchor(t.theme)}
                       className="scroll-mt-12">
                <h2 className="sticky top-0 z-10 flex items-baseline gap-2 border-b
                               border-slate-200 bg-slate-50/90 py-1.5 text-sm
                               font-semibold text-slate-700 backdrop-blur">
                  {t.theme}
                  <span className="font-normal text-slate-400">
                    {t.subjects.length} subject{t.subjects.length === 1 ? '' : 's'}
                  </span>
                  {user && (
                    <span className="ml-auto flex items-center gap-2">
                      {themeProgress.get(t.theme)?.done === t.subjects.length && (
                        <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs
                                         font-medium text-emerald-800">
                          done
                        </span>
                      )}
                      <ProgressBar compact
                                   done={themeProgress.get(t.theme)?.done ?? 0}
                                   total={themeProgress.get(t.theme)?.total ?? 0}
                                   label={`${t.theme}: core subjects practised`} />
                    </span>
                  )}
                </h2>

                <ol className="mt-3 space-y-3">
                  {t.subjects.map((s) => {
                    const isDone = practiced.has(s.f_id)
                    const isOpen = open.has(s.core_subject)
                    return (
                      <li
                        key={s.core_subject}
                        className={`flex items-start gap-3 rounded-lg border p-4 ${
                          isDone ? 'border-emerald-200 bg-emerald-50/40'
                                 : 'border-slate-200 bg-white'
                        }`}
                      >
                        <div className="min-w-0 flex-1">
                          <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs">
                            <span className="rounded bg-violet-100 px-1.5 py-0.5 font-medium text-violet-800">
                              {s.core_subject}
                            </span>
                            {/* months, not scraped reports: two sources filing
                                the same sitting is one exam, and 18% of
                                month-entries have more than one report */}
                            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">
                              came up in {s.months_seen} month
                              {s.months_seen === 1 ? '' : 's'}
                            </span>

                          </div>
                          <p className="leading-relaxed">{s.questions[0].text}</p>

                          {s.question_count > 1 && (
                            <button
                              type="button"
                              onClick={() => toggleOpen(s.core_subject)}
                              aria-expanded={isOpen}
                              className="mt-2 text-xs text-sky-700 hover:underline"
                            >
                              {isOpen ? 'Hide' : `Show ${s.question_count - 1} more`}{' '}
                              question{s.question_count - 1 === 1 ? '' : 's'} on this subject
                            </button>
                          )}

                          {isOpen && (
                            // the rest of the subject, in place. Each one is
                            // tickable, but only the representative above counts
                            // towards this page's progress - see the note on
                            // themeProgress.
                            <ol className="mt-3 space-y-2 border-l-2 border-slate-200 pl-3">
                              {s.questions.slice(1).map((q) => (
                                <li key={q.f_id} className="flex items-start gap-2">
                                  <div className="min-w-0 flex-1">
                                    <p className="text-sm leading-relaxed text-slate-700">
                                      {q.text}
                                    </p>
                                    <p className="mt-0.5 text-xs text-slate-400">
                                      seen in {q.months_seen} month
                                      {q.months_seen === 1 ? '' : 's'}
                                      {q.last_seen && ` · last ${q.last_seen}`}
                                    </p>
                                  </div>
                                  <Marks
                                    user={user} fId={q.f_id} small
                                    practiced={practiced} bookmarked={bookmarked}
                                    togglePracticed={togglePracticed}
                                    toggleBookmarked={toggleBookmarked}
                                  />
                                </li>
                              ))}
                            </ol>
                          )}
                        </div>

                        <Marks
                          user={user} fId={s.f_id}
                          practiced={practiced} bookmarked={bookmarked}
                          togglePracticed={togglePracticed}
                          toggleBookmarked={toggleBookmarked}
                        />
                      </li>
                    )
                  })}
                </ol>
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
