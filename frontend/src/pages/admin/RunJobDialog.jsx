import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { adminCreateJob, adminLlm } from '../../lib/api'
import { useToast } from '../../lib/toast'

/**
 * Start a labelling run over a chosen set of questions.
 *
 * Two modes, because they answer different questions:
 *
 *   review    one model, then you accept or correct it
 *   compare   two models, and you only adjudicate where they split
 *
 * Both produce ONE batch. A comparison's batch holds the agreed answers too,
 * so applying it writes everything in a single pass.
 */
export default function RunJobDialog({ tache, task, fIds, onClose }) {
  const toast = useToast()
  const { data: caps } = useQuery({ queryKey: ['admin-llm'], queryFn: adminLlm })
  const providers = caps?.providers ?? []

  const [mode, setMode] = useState('review')
  const [chunk, setChunk] = useState(20)
  const [runs, setRuns] = useState([
    { provider: '', model: '', api_key: '' },
    { provider: '', model: '', api_key: '' },
  ])
  const [error, setError] = useState('')

  const used = mode === 'compare' ? runs : runs.slice(0, 1)
  const ready = used.every((r) => {
    const p = providers.find((x) => x.name === r.provider)
    return p && (p.configured || r.api_key.trim())
  })

  const create = useMutation({
    mutationFn: () => adminCreateJob({
      tache, task, mode, chunk: Number(chunk), f_ids: fIds,
      runs: used.map((r) => ({
        provider: r.provider,
        model: r.model.trim() || undefined,
        api_key: r.api_key.trim() || undefined,
      })),
    }),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
    onSuccess: () => {
      toast('Job created — see it in the Runs tab')
      onClose()
    },
  })

  const setRun = (i, patch) =>
    setRuns((rs) => rs.map((r, n) => (n === i ? { ...r, ...patch } : r)))

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto
                    bg-slate-900/40 p-4 pt-16">
      <div className="w-full max-w-lg space-y-4 rounded-lg bg-white p-5 shadow-xl">
        <div>
          <h3 className="font-semibold">Run a job on {fIds.length} question
            {fIds.length === 1 ? '' : 's'}</h3>
          <p className="mt-1 text-sm text-slate-600">
            Task {tache} · {task}
          </p>
        </div>

        <div className="flex gap-2">
          {[
            ['review', 'Review', 'one model — accept or correct its answers'],
            ['compare', 'Compare', 'two models — adjudicate only where they split'],
          ].map(([value, label, hint]) => (
            <button
              key={value}
              onClick={() => setMode(value)}
              className={`flex-1 rounded-lg border p-3 text-left transition ${
                mode === value ? 'border-sky-500 bg-sky-50' : 'border-slate-200 hover:bg-slate-50'
              }`}
            >
              <span className="block font-medium">{label}</span>
              <span className="block text-xs text-slate-600">{hint}</span>
            </button>
          ))}
        </div>

        {used.map((r, i) => (
          <div key={i} className="space-y-2 rounded-md border border-slate-200 p-3">
            {mode === 'compare' && (
              <p className="text-xs font-medium text-slate-500">Model {i + 1}</p>
            )}
            <div className="flex flex-wrap gap-2">
              <select
                value={r.provider}
                onChange={(e) => setRun(i, { provider: e.target.value, model: '' })}
                className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
              >
                <option value="">provider…</option>
                {providers.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}{p.configured ? ' — key on server' : ''}
                  </option>
                ))}
              </select>
              <input
                value={r.model}
                onChange={(e) => setRun(i, { model: e.target.value })}
                placeholder={providers.find((p) => p.name === r.provider)?.default_model || 'model'}
                className="w-44 rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
              <input
                type="password"
                autoComplete="off"
                value={r.api_key}
                onChange={(e) => setRun(i, { api_key: e.target.value })}
                placeholder={providers.find((p) => p.name === r.provider)?.configured
                  ? 'server key' : 'API key'}
                className="w-40 rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
            </div>
          </div>
        ))}

        <label className="flex items-center gap-2 text-sm">
          Chunk
          <input type="number" min="1" max="100" value={chunk}
                 onChange={(e) => setChunk(e.target.value)}
                 className="w-20 rounded-md border border-slate-300 px-2 py-1" />
          <span className="text-xs text-slate-500">
            {Math.ceil(fIds.length / Math.max(1, Number(chunk))) * used.length} request(s)
          </span>
        </label>

        {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

        <div className="flex items-center gap-2">
          <button
            onClick={() => create.mutate()}
            disabled={!ready || !fIds.length || create.isPending}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white
                       disabled:opacity-40"
          >
            {create.isPending ? 'Starting…' : 'Start'}
          </button>
          <button onClick={onClose} className="text-sm text-slate-600 hover:underline">
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
