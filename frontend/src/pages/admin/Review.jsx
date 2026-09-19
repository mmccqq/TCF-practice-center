import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  adminApplyBatch, adminBatch, adminBatches, adminCreateBatch, adminDecide,
  adminDeleteBatch,
} from '../../lib/api'
import { readJsonl } from '../../lib/jsonl'

function Upload({ onDone, onError }) {
  const [busy, setBusy] = useState(false)
  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-lg border border-slate-200 bg-white p-4"
      onSubmit={async (e) => {
        e.preventDefault()
        const form = new FormData(e.target)
        const file = form.get('file')
        if (!file?.size) return
        setBusy(true)
        onError('')
        try {
          const { rows, errors } = await readJsonl(file)
          if (errors.length) {
            throw new Error(`could not parse line${errors.length > 1 ? 's' : ''} `
                            + errors.slice(0, 5).join(', ')
                            + (errors.length > 5 ? `… (${errors.length} total)` : ''))
          }
          if (!rows.length) throw new Error('no rows in that file')
          const res = await adminCreateBatch({
            name: String(form.get('name') || file.name),
            tache: Number(form.get('tache')),
            rows,
          })
          e.target.reset()
          onDone(res)
        } catch (err) {
          onError(err.message)
        } finally {
          setBusy(false)
        }
      }}
    >
      <label className="text-sm">
        <span className="block text-xs text-slate-500">llm.py or compare.py output (.jsonl)</span>
        <input type="file" name="file" accept=".jsonl,.json,application/json"
               className="mt-1 block text-sm" />
      </label>
      <label className="text-sm">
        <span className="block text-xs text-slate-500">Name</span>
        <input name="name" placeholder="defaults to the filename"
               className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm" />
      </label>
      <label className="text-sm">
        <span className="block text-xs text-slate-500">Task</span>
        <select name="tache" defaultValue="2"
                className="mt-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm">
          <option value="2">Task 2</option>
          <option value="3">Task 3</option>
        </select>
      </label>
      <button disabled={busy}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50">
        {busy ? 'Reading…' : 'Create batch'}
      </button>
    </form>
  )
}

function Queue({ batchId, onError }) {
  const [onlyOpen, setOnlyOpen] = useState(false)
  const qc = useQueryClient()
  const key = ['admin-batch', batchId, onlyOpen]
  const { data, isPending } = useQuery({
    queryKey: key,
    queryFn: () => adminBatch(batchId, onlyOpen ? { undecided: true } : undefined),
  })
  const [applied, setApplied] = useState(null)

  const decide = useMutation({
    mutationFn: ({ itemId, decision }) => adminDecide(batchId, itemId, decision),
    onMutate: async ({ itemId, decision }) => {
      // optimistic, because adjudication is a rhythm - waiting for a round trip
      // between each click is what makes reviewing 200 items feel like work
      await qc.cancelQueries({ queryKey: key })
      const prev = qc.getQueryData(key)
      qc.setQueryData(key, (old) => old && ({
        ...old,
        items: old.items.map((i) => (i.id === itemId ? { ...i, decision } : i)),
      }))
      return { prev }
    },
    onError: (e, _v, ctx) => { qc.setQueryData(key, ctx?.prev); onError(e.message) },
    onSettled: () => qc.invalidateQueries({ queryKey: ['admin-batch', batchId] }),
  })

  const apply = useMutation({
    mutationFn: () => adminApplyBatch(batchId),
    onSuccess: (res) => {
      setApplied(res)
      qc.invalidateQueries({ queryKey: ['admin-batches'] })
      qc.invalidateQueries({ queryKey: ['admin-questions'] })
    },
    onError: (e) => onError(e.message),
  })

  if (isPending) return <p className="text-sm text-slate-500">Loading…</p>

  const decided = data.items.filter((i) => i.decision !== null).length

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200
                      bg-white p-3 text-sm">
        <strong>{data.name}</strong>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{data.field}</span>
        <span className="text-slate-500">{decided} of {data.items.length} decided</span>
        <label className="flex items-center gap-1 text-slate-600">
          <input type="checkbox" checked={onlyOpen}
                 onChange={(e) => setOnlyOpen(e.target.checked)} />
          undecided only
        </label>
        <button
          onClick={() => apply.mutate()}
          disabled={apply.isPending}
          className="ml-auto rounded-md bg-emerald-600 px-3 py-1.5 font-medium text-white
                     disabled:opacity-50"
        >
          {apply.isPending ? 'Applying…' : 'Apply decisions'}
        </button>
      </div>

      {applied && (
        <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm">
          <strong>{applied.applied} written</strong>, {applied.unchanged} already
          the same, {applied.skipped} skipped.
          {Object.keys(applied.unresolved).length > 0 && (
            <>
              <p className="mt-2 font-medium text-amber-800">
                Not in the vocabulary, so not written:
              </p>
              <ul className="mt-1 list-inside list-disc text-amber-800">
                {Object.entries(applied.unresolved).map(([k, n]) => (
                  <li key={k}>{k} — {n}</li>
                ))}
              </ul>
              <p className="mt-1 text-slate-600">
                Add them under Vocabulary, then apply again.
              </p>
            </>
          )}
        </div>
      )}

      <ol className="space-y-2">
        {data.items.map((item) => {
          const options = Object.entries(item.candidates)
          return (
            <li key={item.id}
                className={`rounded-lg border p-3 ${
                  item.decision !== null ? 'border-emerald-200 bg-emerald-50/40'
                                         : 'border-slate-200 bg-white'
                }`}>
              <p className="text-sm leading-relaxed">{item.text || <em>no text</em>}</p>
              <p className="mt-1 text-xs text-slate-400">
                {item.f_id ? `f_id ${item.f_id}` : (
                  <span className="text-red-600">
                    no question matches id {item.source_id} — cannot be applied
                  </span>
                )}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {options.map(([run, value]) => (
                  <button
                    key={run}
                    onClick={() => decide.mutate({ itemId: item.id, decision: value })}
                    className={`rounded px-2 py-1 text-xs transition ${
                      item.decision === value
                        ? 'bg-emerald-600 text-white'
                        : 'bg-slate-100 hover:bg-slate-200'
                    }`}
                  >
                    {value}
                    <span className="ml-1 opacity-60">{run}</span>
                  </button>
                ))}
                <input
                  defaultValue={item.decision && !options.some(([, v]) => v === item.decision)
                    ? item.decision : ''}
                  placeholder="or type a value…"
                  onBlur={(e) => {
                    const v = e.target.value.trim()
                    if (v && v !== item.decision) decide.mutate({ itemId: item.id, decision: v })
                  }}
                  className="rounded border border-slate-300 px-2 py-1 text-xs"
                />
                <button
                  onClick={() => decide.mutate({ itemId: item.id, decision: '' })}
                  className={`rounded px-2 py-1 text-xs ${
                    item.decision === '' ? 'bg-slate-700 text-white' : 'bg-slate-100 hover:bg-slate-200'
                  }`}
                  title="Leave this question as it is"
                >
                  skip
                </button>
                {item.decision !== null && (
                  <button
                    onClick={() => decide.mutate({ itemId: item.id, decision: null })}
                    className="text-xs text-slate-500 hover:underline"
                  >
                    undo
                  </button>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export default function Review() {
  const [open, setOpen] = useState(null)
  const [error, setError] = useState('')
  const qc = useQueryClient()
  const { data: batches } = useQuery({ queryKey: ['admin-batches'], queryFn: adminBatches })

  return (
    <div className="space-y-4">
      <Upload
        onError={setError}
        onDone={(res) => {
          qc.invalidateQueries({ queryKey: ['admin-batches'] })
          setOpen(res.id)
          if (res.unmatched) {
            setError(`${res.unmatched} row(s) name a question that is not in the `
                     + `database — they are listed but cannot be applied.`)
          }
        }}
      />

      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      <ul className="space-y-2">
        {(batches ?? []).map((b) => (
          <li key={b.id} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={() => setOpen(open === b.id ? null : b.id)}
                      className="font-medium text-sky-700 hover:underline">
                {b.name}
              </button>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{b.field}</span>
              <span className="text-xs text-slate-500">
                Task {b.tache} · {b.decided}/{b.items} decided
                {b.applied_at && ' · applied'}
              </span>
              <button
                onClick={async () => {
                  await adminDeleteBatch(b.id)
                  if (open === b.id) setOpen(null)
                  qc.invalidateQueries({ queryKey: ['admin-batches'] })
                }}
                className="ml-auto text-xs text-red-600 hover:underline"
              >
                delete
              </button>
            </div>
            {open === b.id && (
              <div className="mt-3"><Queue batchId={b.id} onError={setError} /></div>
            )}
          </li>
        ))}
      </ul>

      {batches?.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
          No batches yet. Upload an llm.py output above, or build one from the
          Compare tab.
        </p>
      )}
    </div>
  )
}
