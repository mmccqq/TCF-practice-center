import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { adminJobs, adminScrape } from '../../lib/api'

export default function Data() {
  const [error, setError] = useState('')
  const qc = useQueryClient()

  // the scrape job shows up here and in Runs; this polls only while it moves
  const { data: jobs } = useQuery({
    queryKey: ['admin-jobs'],
    queryFn: adminJobs,
    refetchInterval: (q) =>
      (q.state.data ?? []).some((j) => j.status === 'running' || j.status === 'queued')
        ? 2000 : false,
  })
  const scrape = (jobs ?? []).find((j) => j.kind === 'scrape')
  const busy = (jobs ?? []).some((j) => ['queued', 'running'].includes(j.status))

  const runScrape = useMutation({
    mutationFn: () => adminScrape(['reussir', 'formation']),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-jobs'] }),
  })

  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-lg border-2 border-sky-500 bg-white p-4">
        <div>
          <h3 className="font-semibold">Fetch new questions</h3>
          <p className="text-sm text-slate-600">
            Checks each source for months the bank does not have, downloads only
            those pages, parses them, and loads the questions — scrape, parse and
            backfill in one action. No HTML is stored: pages are parsed in memory,
            and the database is what remembers which months are already in.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={() => runScrape.mutate()}
            disabled={busy || runScrape.isPending}
            className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white
                       disabled:opacity-40"
          >
            {busy ? 'Running…' : 'Fetch and load'}
          </button>
          {scrape && (
            <span className="text-sm text-slate-600">
              last run: {scrape.status}
              {scrape.total_chunks > 0 &&
                ` · ${scrape.done_chunks}/${scrape.total_chunks} pages`}
              {scrape.answered > 0 && ` · ${scrape.answered} questions found`}
            </span>
          )}
        </div>
        {scrape?.error && scrape.status !== 'running' && (
          // for a scrape job this field holds the report, not a failure
          <pre className="whitespace-pre-wrap rounded-md border border-slate-300
                          bg-slate-50 p-3 text-xs text-slate-700">{scrape.error}</pre>
        )}
      </section>

      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    </div>
  )
}
