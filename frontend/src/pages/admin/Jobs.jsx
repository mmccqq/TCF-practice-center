import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { adminCancelJob, adminJobs } from '../../lib/api'

// Jobs are started from the Labelling tab, where the questions are chosen.
// This tab watches them: progress, timings, and where a finished one leads.
const DONE = ['done', 'failed', 'cancelled', 'interrupted']

/**
 * Parse a timestamp the API returned.
 *
 * Both databases store UTC, but only one says so: Postgres serialises
 * `2026-09-20T19:39:32+00:00` while SQLite gives `2026-09-20T19:39:32` with no
 * zone, which `new Date()` would read as local time. Assume UTC when the
 * marker is missing, or the same job would display hours apart depending on
 * which database served it.
 */
function parseUtc(iso) {
  if (!iso) return null
  const zoned = /[Zz]$|[+-]\d\d:?\d\d$/.test(iso)
  const d = new Date(zoned ? iso : `${iso}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

const clock = (d) =>
  d ? d.toLocaleString(undefined, { month: 'short', day: 'numeric',
                                    hour: '2-digit', minute: '2-digit' }) : ''

function elapsed(from, to) {
  if (!from) return ''
  const secs = Math.max(0, Math.round(((to ?? new Date()) - from) / 1000))
  if (secs < 60) return `${secs}s`
  const mins = Math.floor(secs / 60)
  return mins < 60 ? `${mins}m ${secs % 60}s`
                   : `${Math.floor(mins / 60)}h ${mins % 60}m`
}

export default function Jobs() {
  const qc = useQueryClient()
  const { data: jobs } = useQuery({
    queryKey: ['admin-jobs'],
    queryFn: adminJobs,
    // only while something is moving; a finished list does not need polling
    refetchInterval: (q) =>
      (q.state.data ?? []).some((j) => !DONE.includes(j.status)) ? 3000 : false,
  })

  return (
    <div className="space-y-4">
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
              {(() => {
                const started = parseUtc(j.created_at)
                const ended = parseUtc(j.finished_at)
                return (
                  <p className="mt-1 text-xs text-slate-400">
                    started {clock(started)}
                    {ended ? ` · finished ${clock(ended)} · took ${elapsed(started, ended)}`
                           // no end time yet, so count up from the start - the
                           // list is already polling, so this ticks on its own
                           : ` · running ${elapsed(started)}`}
                  </p>
                )
              })()}
              {j.error && (
                <pre className="mt-1 whitespace-pre-wrap text-xs text-amber-800">{j.error}</pre>
              )}
            </li>
          )
        })}
      </ul>

      {jobs?.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
          No runs yet. Start one from the Labelling tab: pick some questions,
          then “Run a job”.
        </p>
      )}
    </div>
  )
}
