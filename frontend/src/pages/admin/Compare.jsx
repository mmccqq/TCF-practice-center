import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { adminCompare, adminCreateBatch } from '../../lib/api'
import { readJsonl } from '../../lib/jsonl'
import { TacheTabs } from './AdminLayout'

export default function Compare() {
  const [tache, setTache] = useState(2)
  const [runs, setRuns] = useState({})      // name -> rows
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const qc = useQueryClient()

  const addFiles = async (fileList) => {
    setError('')
    const next = { ...runs }
    for (const file of fileList) {
      const { rows, errors } = await readJsonl(file)
      if (errors.length) {
        setError(`${file.name}: could not parse line${errors.length > 1 ? 's' : ''} `
                 + errors.slice(0, 5).join(', '))
        continue
      }
      // the run's name is the model if llm.py recorded one, else the filename -
      // "gpt-5.6 vs deepseek" reads better than two paths
      next[rows.find((r) => r.model)?.model || file.name.replace(/\.jsonl?$/, '')] = rows
    }
    setRuns(next)
    setResult(null)
  }

  const compare = useMutation({
    mutationFn: () => adminCompare({ runs }),
    onSuccess: setResult,
    onError: (e) => setError(e.message),
  })

  const toBatch = useMutation({
    mutationFn: () => adminCreateBatch({
      name: `disagreements: ${result.runs.join(' vs ')}`,
      tache,
      field: result.field,
      rows: result.rows,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-batches'] })
      setError('')
    },
    onError: (e) => setError(e.message),
  })

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <p className="text-sm text-slate-600">
          Load two or more runs of the same task over the same questions. Where every
          run agrees, the answer is usually right and not worth your time; where they
          split is what a reviewer is for.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input
            type="file" multiple accept=".jsonl,.json,application/json"
            onChange={(e) => addFiles([...e.target.files])}
            className="text-sm"
          />
          <TacheTabs tache={tache} onChange={setTache} />
        </div>
        {Object.keys(runs).length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            {Object.entries(runs).map(([name, rows]) => (
              <span key={name} className="flex items-center gap-1 rounded bg-slate-100 px-2 py-1">
                {name} <span className="text-slate-400">{rows.length}</span>
                <button
                  onClick={() => { const n = { ...runs }; delete n[name]; setRuns(n); setResult(null) }}
                  className="text-red-600"
                >×</button>
              </span>
            ))}
            <button
              onClick={() => compare.mutate()}
              disabled={Object.keys(runs).length < 2 || compare.isPending}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-white disabled:opacity-40"
            >
              {compare.isPending ? 'Comparing…' : 'Compare'}
            </button>
          </div>
        )}
      </div>

      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {result && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-4 rounded-lg border border-slate-200
                          bg-white p-4">
            <div>
              <p className="text-3xl font-bold">{Math.round(result.agreement * 100)}%</p>
              <p className="text-xs text-slate-500">agreement on {result.field}</p>
            </div>
            <div className="text-sm text-slate-600">
              <p>{result.compared.toLocaleString()} questions in every run</p>
              <p>{result.agreed} agreed · <strong>{result.disagreed} disagreed</strong></p>
              {result.only_in_some > 0 && (
                <p className="text-amber-700">
                  {result.only_in_some} appear in some runs but not all — excluded
                </p>
              )}
            </div>
            {result.disagreed > 0 && (
              <button
                onClick={() => toBatch.mutate()}
                disabled={toBatch.isPending || toBatch.isSuccess}
                className="ml-auto rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium
                           text-white disabled:opacity-50"
              >
                {toBatch.isSuccess ? 'Sent to Review ✓'
                  : toBatch.isPending ? 'Creating…'
                  : `Send ${result.disagreed} to Review`}
              </button>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <h3 className="text-sm font-semibold">What they disagree about</h3>
              <p className="text-xs text-slate-500">
                each pair of answers the runs split on, and how often
              </p>
              <ul className="mt-2 space-y-1 text-sm">
                {result.label_pairs.slice(0, 12).map((p) => (
                  <li key={p.pair} className="flex gap-2">
                    <span className="w-8 shrink-0 text-right text-slate-400">{p.count}</span>
                    <span>{p.pair}</span>
                  </li>
                ))}
              </ul>
            </section>
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <h3 className="text-sm font-semibold">Most contested labels</h3>
              <p className="text-xs text-slate-500">
                How many disagreements each label took part in. A label near the top
                is one the models keep choosing where another model chooses something
                else — usually its boundary against a neighbour is unclear, which is a
                prompt or vocabulary fix rather than a per-question one.
              </p>
              {/* all-1s is not a ranking, it is noise: with few disagreements every
                  label appears once and the order means nothing */}
              {result.contested_labels[0]?.count === 1 ? (
                <p className="mt-2 text-sm text-slate-500">
                  Every label here appears once — {result.disagreed} disagreement
                  {result.disagreed === 1 ? '' : 's'} is too few to show a pattern.
                </p>
              ) : (
                <ul className="mt-2 space-y-1 text-sm">
                  {result.contested_labels.slice(0, 12).map((l) => (
                    <li key={l.label} className="flex gap-2">
                      <span className="w-8 shrink-0 text-right text-slate-400">{l.count}</span>
                      <span>{l.label}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
