import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { questionsMeta } from '../lib/api'

function SectionCard({ title, subtitle, count, to, disabled }) {
  const body = (
    <>
      <div className="flex items-baseline justify-between">
        <h3 className="font-semibold">{title}</h3>
        {disabled ? (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
            Coming soon
          </span>
        ) : (
          <span className="text-xs text-slate-500">
            {count != null ? `${count.toLocaleString()} questions` : '—'}
          </span>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-600">{subtitle}</p>
    </>
  )

  const base = 'block rounded-xl border p-5 text-left transition'
  if (disabled) {
    return (
      <div
        aria-disabled="true"
        className={`${base} cursor-not-allowed border-slate-200 bg-slate-50 opacity-70`}
      >
        {body}
      </div>
    )
  }
  return (
    <Link to={to} className={`${base} border-slate-200 bg-white hover:border-sky-400 hover:shadow-sm`}>
      {body}
    </Link>
  )
}

export default function Home() {
  const { data } = useQuery({ queryKey: ['meta'], queryFn: questionsMeta })
  const counts = data?.counts

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-3xl font-bold tracking-tight">
          Prepare for TCF Canada with real past questions
        </h1>
        <p className="mt-3 max-w-2xl text-slate-600">
          Most French practice is generic. This is an archive of questions that have actually
          appeared on the TCF Canada exam, collected from public practice sources and
          deduplicated across them, so you can see what really gets asked &mdash; and how often.
        </p>
        <p className="mt-2 max-w-2xl text-sm text-slate-500">
          Speaking Tasks 2 and 3 carry the most preparation value, so coverage there is
          deepest. Themes, high-frequency banks, model answers and flashcards are on the way.
        </p>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Expression orale &mdash; available now
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <SectionCard
            title="Speaking &mdash; Task 2"
            subtitle="Ask questions to gather information in an everyday situation."
            count={counts?.tache2}
            to="/speaking/task2"
          />
          <SectionCard
            title="Speaking &mdash; Task 3"
            subtitle="Give and defend an opinion on a general topic."
            count={counts?.tache3}
            to="/speaking/task3"
          />
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Other sections
        </h2>
        <div className="grid gap-4 sm:grid-cols-3">
          <SectionCard title="Compréhension orale" subtitle="Listening." disabled />
          <SectionCard title="Compréhension écrite" subtitle="Reading." disabled />
          <SectionCard title="Expression écrite" subtitle="Writing." disabled />
        </div>
      </section>
    </div>
  )
}
