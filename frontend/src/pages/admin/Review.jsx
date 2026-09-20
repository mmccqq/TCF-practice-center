import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  adminApplyBatch, adminBatch, adminBatches, adminCreateBatch,
  adminCreateSubject, adminCreateTheme, adminDecide, adminDecideAll,
  adminDeleteBatch, adminVocabulary, invalidatePublic,
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
  const [bulk, setBulk] = useState(null)

  // the whole vocabulary, so a reviewer can choose a label neither model
  // proposed - which is the common case when both are near-misses of an
  // established one
  const { data: vocab } = useQuery({
    queryKey: ['admin-vocabulary', data?.tache],
    queryFn: () => adminVocabulary({ tache: data.tache }),
    enabled: !!data,
  })

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

  // one request, not one per item: a 200-row abstract file is the common case
  // and clicking through it to accept every answer is not review, it is typing
  const acceptAll = useMutation({
    mutationFn: (run) => adminDecideAll(batchId, run ? { run } : {}),
    onSuccess: (res) => {
      setBulk(res)
      qc.invalidateQueries({ queryKey: ['admin-batch', batchId] })
      qc.invalidateQueries({ queryKey: ['admin-batches'] })
    },
    onError: (e) => onError(e.message),
  })

  // create the missing label and apply again, without leaving the queue. The
  // vocabulary stays a deliberate act - this is still a click per label - it
  // just stops being a trip to another tab and back.
  const addAndRetry = useMutation({
    mutationFn: async (u) => {
      if (u.field === 'theme') {
        await adminCreateTheme({ tache: data.tache, name: u.value })
      } else {
        await adminCreateSubject({ theme_id: u.theme_id, name: u.value })
      }
      return adminApplyBatch(batchId)
    },
    onSuccess: (res) => {
      setApplied(res)
      qc.invalidateQueries({ queryKey: ['admin-vocabulary'] })
      qc.invalidateQueries({ queryKey: ['admin-batches'] })
      invalidatePublic(qc)
    },
    onError: (e) => onError(e.message),
  })

  const apply = useMutation({
    mutationFn: () => adminApplyBatch(batchId),
    onSuccess: (res) => {
      setApplied(res)
      qc.invalidateQueries({ queryKey: ['admin-batches'] })
      qc.invalidateQueries({ queryKey: ['admin-questions'] })
      invalidatePublic(qc)
    },
    onError: (e) => onError(e.message),
  })

  if (isPending) return <p className="text-sm text-slate-500">Loading…</p>

  const decided = data.items.filter((i) => i.decision !== null).length
  // every run name appearing anywhere in the batch; one means "accept all" is
  // unambiguous, several means the reviewer has to say whose answer to take
  const runs = [...new Set(data.items.flatMap((i) => Object.keys(i.candidates)))]
  const remaining = data.items.length - decided

  // themes are scoped by task, core subjects by the question's own theme -
  // offering another theme's labels would let an inconsistent pair be chosen
  const choicesFor = (item) => {
    if (!vocab) return []
    if (data.field === 'theme') return vocab.themes
    return vocab.themes.find((t) => t.id === item.theme_id)?.core_subjects ?? []
  }

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
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {remaining > 0 && (
            runs.length <= 1 ? (
              <button
                onClick={() => acceptAll.mutate(null)}
                disabled={acceptAll.isPending}
                className="rounded-md border border-slate-300 px-3 py-1.5 font-medium
                           disabled:opacity-50"
              >
                {acceptAll.isPending ? 'Accepting…' : `Accept all ${remaining} remaining`}
              </button>
            ) : (
              <select
                defaultValue=""
                onChange={(e) => { if (e.target.value) acceptAll.mutate(e.target.value) }}
                className="rounded-md border border-slate-300 bg-white px-2 py-1.5"
              >
                <option value="">Accept all {remaining} from…</option>
                {runs.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            )
          )}
          <button
            onClick={() => apply.mutate()}
            disabled={apply.isPending}
            className="rounded-md bg-emerald-600 px-3 py-1.5 font-medium text-white
                       disabled:opacity-50"
          >
            {apply.isPending ? 'Applying…' : 'Apply decisions'}
          </button>
        </div>
      </div>

      {bulk && (
        <p className="rounded-lg border border-slate-300 bg-slate-50 p-3 text-sm">
          Accepted {bulk.decided}.
          {bulk.left_for_you > 0 && (
            <> {bulk.left_for_you} still need you — those have more than one answer.</>
          )}
          {bulk.already_decided > 0 && (
            <> {bulk.already_decided} you had already decided were left alone.</>
          )}
          {' '}Nothing is written until you press Apply.
        </p>
      )}

      {applied && (
        <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm">
          <strong>{applied.applied} written</strong>, {applied.unchanged} already
          the same, {applied.skipped} skipped.
          {applied.unresolved.length > 0 && (
            <>
              <p className="mt-2 font-medium text-amber-800">
                Not in the vocabulary, so not written:
              </p>
              <ul className="mt-1 space-y-1">
                {applied.unresolved.map((u) => (
                  <li key={`${u.field}-${u.value}-${u.theme_id}`}
                      className="flex flex-wrap items-center gap-2 text-amber-900">
                    <strong>{u.value}</strong>
                    <span className="text-xs">
                      {u.field}
                      {u.theme && ` under ${u.theme}`} · {u.count} question
                      {u.count === 1 ? '' : 's'}
                    </span>
                    {u.why ? (
                      // nothing to add it under - the reviewer has to give the
                      // question a theme first, which is a different tool
                      <span className="text-xs italic">{u.why}</span>
                    ) : (
                      <button
                        onClick={() => addAndRetry.mutate(u)}
                        disabled={addAndRetry.isPending}
                        className="rounded border border-amber-400 bg-white px-2 py-0.5
                                   text-xs font-medium hover:bg-amber-100 disabled:opacity-50"
                      >
                        {addAndRetry.isPending ? 'Adding…' : 'Add and apply'}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
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
              {/* abstract first: it is English and one line, so a queue can be
                  read at speed, with the French underneath for the judgement
                  call. review.html put them in this order for the same reason. */}
              {item.abstract && (
                <p className="text-sm font-medium text-slate-800">{item.abstract}</p>
              )}
              <p className={`text-sm leading-relaxed ${item.abstract ? 'mt-0.5 text-slate-600' : ''}`}>
                {item.text || <em>no text</em>}
              </p>
              <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                {item.theme && (
                  <span className="rounded bg-sky-100 px-1.5 py-0.5 text-sky-800">
                    {item.theme}
                  </span>
                )}
                {item.f_id ? `f_id ${item.f_id}` : (
                  <span className="text-red-600">
                    no question matches id {item.source_id} — cannot be applied
                  </span>
                )}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {options.map(([run, value]) => {
                  // how many questions already use this label. `new` is the
                  // important one: it says this answer would widen the
                  // vocabulary rather than reuse it, which is usually the wrong
                  // choice when a sibling label is sitting at 10.
                  const used = item.usage?.[value]
                  const chosen = item.decision === value
                  return (
                    <button
                      key={run}
                      onClick={() => decide.mutate({ itemId: item.id, decision: value })}
                      className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition ${
                        chosen ? 'bg-emerald-600 text-white' : 'bg-slate-100 hover:bg-slate-200'
                      }`}
                    >
                      <span>{value}</span>
                      {used === undefined ? null : used === null ? (
                        <span className={`rounded px-1 ${
                          chosen ? 'bg-white/25' : 'bg-amber-200 text-amber-900'
                        }`}>new</span>
                      ) : (
                        <span className={chosen ? 'opacity-80' : 'text-slate-500'}>{used}</span>
                      )}
                      <span className="opacity-60">{run}</span>
                    </button>
                  )
                })}
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
                {data.field !== 'abstract' && choicesFor(item).length > 0 && (
                  <select
                    value=""
                    onChange={(e) => {
                      if (e.target.value) {
                        decide.mutate({ itemId: item.id, decision: e.target.value })
                      }
                    }}
                    className="rounded border border-slate-300 bg-white px-2 py-1 text-xs"
                  >
                    <option value="">
                      or pick from {item.theme ?? `Task ${data.tache}`}…
                    </option>
                    {choicesFor(item).map((c) => (
                      <option key={c.id} value={c.name}>
                        {c.name} ({c.usage})
                      </option>
                    ))}
                  </select>
                )}
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
