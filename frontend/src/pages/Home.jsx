import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { coreSetProgress, questionsMeta } from '../lib/api'
import { useAuth } from '../lib/auth'
import { ProgressBar } from '../lib/progress'

/** The flagship card. Full width and above the task grid, because working
 *  through the core set is the recommended path, not one option among three. */
function CoreSetCard({ progress }) {
  const done = progress?.done ?? 0
  const total = progress?.total ?? 0
  return (
    <Link
      to="/core-set"
      className="block rounded-xl border-2 border-sky-500 bg-white p-5 transition
                 hover:border-sky-600 hover:shadow-md"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-lg font-semibold">Core set</h3>
        <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800">
          Start here
        </span>
      </div>
      <p className="mt-1 max-w-xl text-sm text-slate-600">
        The subjects the exam keeps coming back to, ranked by how often they have
        actually been asked. Work through these and you have covered what really
        gets tested — instead of scrolling a thousand questions in date order.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {total > 0 ? (
          <>
            <ProgressBar done={done} total={total}
                         label={`${done} of ${total} core subjects practised`} />
            <span className="text-sm font-medium text-sky-700">
              {done > 0 ? 'Continue' : 'Start'} &rarr;
            </span>
          </>
        ) : (
          <span className="text-sm font-medium text-sky-700">Open the core set &rarr;</span>
        )}
      </div>
    </Link>
  )
}

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
  const { user } = useAuth()
  const { data } = useQuery({ queryKey: ['meta'], queryFn: questionsMeta })
  const counts = data?.counts

  // a small dedicated endpoint, not the core set itself: this is one line of
  // text and the full set is 27 KB gzipped
  const { data: progress } = useQuery({
    queryKey: ['core-progress', 2],
    queryFn: () => coreSetProgress({ tache: 2 }),
    enabled: !!user,
  })

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
          deepest. Model answers and flashcards are on the way.
        </p>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Recommended
        </h2>
        <CoreSetCard progress={progress} />
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Browse everything
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
