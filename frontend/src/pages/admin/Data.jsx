import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  adminExport, adminImport, adminJobs, adminScrape, adminVocabulary,
  invalidatePublic,
} from '../../lib/api'
import { readJsonl } from '../../lib/jsonl'
import { TacheTabs } from './AdminLayout'

function download(name, rows) {
  const blob = new Blob([rows.map((r) => JSON.stringify(r)).join('\n') + '\n'],
                        { type: 'application/x-ndjson' })
  const url = URL.createObjectURL(blob)
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  a.click()
  URL.revokeObjectURL(url)
}

export default function Data() {
  const [tache, setTache] = useState(2)
  const [unlabelled, setUnlabelled] = useState('theme')
  const [themeId, setThemeId] = useState('')
  const [rows, setRows] = useState(null)      // parsed scraper file
  const [report, setReport] = useState(null)
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

  const { data: vocab } = useQuery({
    queryKey: ['admin-vocabulary', tache],
    queryFn: () => adminVocabulary({ tache }),
  })

  const doExport = useMutation({
    mutationFn: () => adminExport({
      tache, unlabelled, ...(themeId ? { theme_id: themeId } : {}),
    }),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
    onSuccess: (data) => {
      if (!data.length) { setError('nothing matches those filters'); return }
      download(`questions_${data.length}_tache${tache}_no_${unlabelled}.jsonl`, data)
    },
  })

  const doImport = useMutation({
    mutationFn: (dryRun) => adminImport({ rows, dry_run: dryRun }),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
    onSuccess: (res) => {
      setReport(res)
      if (!res.dry_run) {
        qc.invalidateQueries({ queryKey: ['admin-questions'] })
        invalidatePublic(qc)
      }
    },
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

      <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <h3 className="font-semibold">Import scraper output</h3>
          <p className="text-sm text-slate-600">
            The <code>tache2.jsonl</code> / <code>tache3.jsonl</code> a scraper’s
            parse step writes. Loads the three question layers — raw sightings,
            deduplicated questions, and one row per month — the same collapse
            <code> backfill_questions.py</code> does. Re-importing the same file
            changes nothing.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="file" accept=".jsonl,.json,application/json"
            onChange={async (e) => {
              setReport(null); setError('')
              const file = e.target.files?.[0]
              if (!file) return
              const { rows: parsed, errors } = await readJsonl(file)
              if (errors.length) {
                setError(`could not parse line${errors.length > 1 ? 's' : ''} `
                         + errors.slice(0, 5).join(', '))
                setRows(null)
                return
              }
              setRows(parsed)
            }}
            className="text-sm"
          />
          {rows && (
            <>
              <span className="text-sm text-slate-600">{rows.length} rows</span>
              {/* the numbers before the write, always: this is the one action
                  that can reshape the whole bank */}
              <button
                onClick={() => doImport.mutate(true)}
                disabled={doImport.isPending}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
              >
                Dry run
              </button>
              <button
                onClick={() => doImport.mutate(false)}
                disabled={doImport.isPending || !report || report.dry_run === false}
                title={report ? '' : 'Run a dry run first'}
                className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium
                           text-white disabled:opacity-40"
              >
                {doImport.isPending ? 'Importing…' : 'Import'}
              </button>
            </>
          )}
        </div>
        {report && (
          <div className="rounded-md border border-slate-300 bg-slate-50 p-3 text-sm">
            <strong>{report.dry_run ? 'Dry run' : 'Imported'}</strong> ·{' '}
            {report.rows} usable rows
            {report.skipped > 0 && `, ${report.skipped} skipped`} ·{' '}
            {report.new_raw_questions} new sightings, {report.new_fingerprints} new
            questions, {report.new_list_questions} new monthly entries
            {report.totals && (
              <p className="mt-1 text-slate-600">
                now {report.totals.raw_questions} raw ·{' '}
                {report.totals.fingerprints} questions ·{' '}
                {report.totals.list_questions} monthly entries
              </p>
            )}
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <h3 className="font-semibold">Export questions</h3>
          <p className="text-sm text-slate-600">
            Shaped for <code>llm.py</code> — only needed if you want to run a model
            locally instead of from the Runs tab.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <TacheTabs tache={tache} onChange={(t) => { setTache(t); setThemeId('') }} />
          <select value={unlabelled} onChange={(e) => setUnlabelled(e.target.value)}
                  className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm">
            <option value="theme">Missing theme</option>
            <option value="abstract">Missing abstract</option>
            <option value="core_subject">Missing core subject</option>
          </select>
          <select value={themeId} onChange={(e) => setThemeId(e.target.value)}
                  className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm">
            <option value="">Any theme</option>
            {(vocab?.themes ?? []).map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
          <button onClick={() => doExport.mutate()} disabled={doExport.isPending}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50">
            {doExport.isPending ? 'Preparing…' : 'Download .jsonl'}
          </button>
        </div>
      </section>

      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    </div>
  )
}
