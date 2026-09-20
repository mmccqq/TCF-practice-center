import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  adminCreateSubject, adminCreateTheme, adminDeleteSubject, adminDeleteTheme,
  adminMergeSubject, adminRenameTheme, adminUpdateSubject, adminVocabulary,
  invalidatePublic,
} from '../../lib/api'
import { TacheTabs } from './AdminLayout'

export default function Vocabulary() {
  const [tache, setTache] = useState(2)
  const [error, setError] = useState('')
  const [newTheme, setNewTheme] = useState('')
  const [adding, setAdding] = useState(null)      // theme id currently adding to
  const [merging, setMerging] = useState(null)    // { cs, themeId }
  const qc = useQueryClient()
  const key = ['admin-vocabulary', tache]

  const { data, isPending } = useQuery({
    queryKey: key,
    queryFn: () => adminVocabulary({ tache }),
  })

  // every mutation refreshes the same list and surfaces the server's message -
  // the useful half of this tool is the refusals ("in use by 35 questions")
  const run = useMutation({
    mutationFn: ({ fn }) => fn(),
    onMutate: () => setError(''),
    onError: (e) => setError(e.message),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: key })
      invalidatePublic(qc)
    },
  })
  const act = (fn) => run.mutate({ fn })

  if (isPending) return <p className="text-sm text-slate-500">Loading…</p>

  const allSubjects = data.themes.flatMap((t) =>
    t.core_subjects.map((c) => ({ ...c, theme_id: t.id, theme: t.name })))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <TacheTabs tache={tache} onChange={setTache} />
        <span className="text-sm text-slate-500">
          {data.themes.length} themes · {allSubjects.length} core subjects
        </span>
        <form
          className="ml-auto flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (!newTheme.trim()) return
            act(() => adminCreateTheme({ tache, name: newTheme }))
            setNewTheme('')
          }}
        >
          <input
            value={newTheme}
            onChange={(e) => setNewTheme(e.target.value)}
            placeholder="New theme…"
            className="rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
          <button className="rounded-md bg-slate-900 px-3 py-1 text-sm text-white">Add</button>
        </form>
      </div>

      {error && (
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>
      )}

      <div className="space-y-3">
        {data.themes.map((t) => (
          <section key={t.id} className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <input
                defaultValue={t.name}
                onBlur={(e) => {
                  if (e.target.value.trim() && e.target.value !== t.name) {
                    act(() => adminRenameTheme(t.id, { name: e.target.value }))
                  }
                }}
                className="rounded border border-transparent px-1 font-semibold
                           hover:border-slate-300 focus:border-sky-500 focus:outline-none"
              />
              <span className="text-xs text-slate-400">
                {t.usage} question{t.usage === 1 ? '' : 's'} ·{' '}
                {t.core_subjects.length} subject{t.core_subjects.length === 1 ? '' : 's'}
              </span>
              <button
                onClick={() => setAdding(adding === t.id ? null : t.id)}
                className="ml-auto text-xs text-sky-700 hover:underline"
              >
                {adding === t.id ? 'cancel' : '+ core subject'}
              </button>
              <button
                onClick={() => act(() => adminDeleteTheme(t.id))}
                className="text-xs text-red-600 hover:underline"
              >
                delete
              </button>
            </div>

            {adding === t.id && (
              <form
                className="mt-2 flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault()
                  const name = new FormData(e.target).get('name')
                  if (String(name).trim()) {
                    act(() => adminCreateSubject({ theme_id: t.id, name }))
                    e.target.reset()
                  }
                }}
              >
                <input name="name" autoFocus placeholder="label…"
                       className="rounded-md border border-slate-300 px-2 py-1 text-sm" />
                <button className="rounded-md bg-slate-900 px-3 py-1 text-sm text-white">Add</button>
              </form>
            )}

            <ul className="mt-3 flex flex-wrap gap-1.5">
              {t.core_subjects.map((cs) => (
                <li key={cs.id}
                    className="group flex items-center gap-1 rounded bg-violet-50 px-2 py-1 text-sm">
                  <input
                    defaultValue={cs.name}
                    onBlur={(e) => {
                      if (e.target.value.trim() && e.target.value !== cs.name) {
                        act(() => adminUpdateSubject(cs.id, {
                          theme_id: t.id, name: e.target.value,
                        }))
                      }
                    }}
                    size={Math.max(cs.name.length, 6)}
                    className="bg-transparent text-violet-900 focus:outline-none"
                  />
                  <span className={cs.usage ? 'text-xs text-violet-500' : 'text-xs text-slate-400'}>
                    {cs.usage}
                  </span>
                  <button
                    title="Merge into another label under this theme"
                    onClick={() => setMerging({ cs, themeId: t.id })}
                    className="hidden text-xs text-slate-500 hover:text-slate-800 group-hover:inline"
                  >
                    merge
                  </button>
                  <button
                    title={cs.usage ? 'In use — merge it instead' : 'Delete'}
                    onClick={() => act(() => adminDeleteSubject(cs.id))}
                    className="hidden text-xs text-red-600 hover:underline group-hover:inline"
                  >
                    ×
                  </button>
                </li>
              ))}
              {t.core_subjects.length === 0 && (
                <li className="text-sm text-slate-400">no core subjects yet</li>
              )}
            </ul>
          </section>
        ))}
      </div>

      {merging && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
            <h3 className="font-semibold">Merge “{merging.cs.name}”</h3>
            <p className="mt-1 text-sm text-slate-600">
              Its {merging.cs.usage} question{merging.cs.usage === 1 ? '' : 's'} move to the
              label you pick, then “{merging.cs.name}” is deleted. Same theme only.
            </p>
            <select
              autoFocus
              defaultValue=""
              onChange={(e) => {
                if (!e.target.value) return
                act(() => adminMergeSubject(merging.cs.id, Number(e.target.value)))
                setMerging(null)
              }}
              className="mt-3 w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
            >
              <option value="">Merge into…</option>
              {allSubjects
                .filter((c) => c.theme_id === merging.themeId && c.id !== merging.cs.id)
                .map((c) => (
                  <option key={c.id} value={c.id}>{c.name} ({c.usage})</option>
                ))}
            </select>
            <button onClick={() => setMerging(null)}
                    className="mt-3 text-sm text-slate-600 hover:underline">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
