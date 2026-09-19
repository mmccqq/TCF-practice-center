import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import heroImage from '../assets/hero-toronto.jpg'
import { coreSetProgress, questionsMeta } from '../lib/api'
import { useAuth } from '../lib/auth'
import { ProgressBar } from '../lib/progress'

/** The flagship card. Full width and above the task grid, because working
 *  through the oral core set is the recommended path, not one option among three. */
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
        <h3 className="text-lg font-semibold">Oral core set</h3>
        {/* sized against the heading beside it, not against the small chips
            used elsewhere - this is the card's call to action */}
        <span className="rounded-full bg-sky-100 px-3.5 py-1 text-base font-semibold text-sky-800">
          Start here
        </span>
      </div>
      <p className="mt-1 max-w-xl text-sm text-slate-600">
        Prepare the necessary questions to save hundreds of hours practicing a thousand questions.
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
          <span className="text-sm font-medium text-sky-700">Open the oral core set &rarr;</span>
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

  // a small dedicated endpoint, not the oral core set itself: this is one line of
  // text and the full set is 27 KB gzipped
  const { data: progress } = useQuery({
    queryKey: ['core-progress', 2],
    queryFn: () => coreSetProgress({ tache: 2 }),
    enabled: !!user,
  })

  return (
    <div className="space-y-10">
      <section className="relative overflow-hidden rounded-xl">
        {/* The photo sits in its own layer rather than on the section, so the
            blur cannot soften the text above it. Scaled past the edges because
            a blur samples beyond its own bounds and would otherwise leave a
            pale fringe around the card. */}
        <div
          aria-hidden="true"
          className="absolute inset-0 scale-105 bg-cover bg-center blur-xs"
          style={{ backgroundImage: `url(${heroImage})` }}
        />
        {/* the photo is bright in places, so the text needs its own contrast
            rather than relying on the picture staying dark */}
        <div aria-hidden="true" className="absolute inset-0 bg-slate-900/55" />

        <div className="relative px-6 py-14 text-center sm:py-20">
          <h1 className="text-4xl font-bold tracking-tight text-white sm:text-5xl">
            TCF practice
          </h1>
          <p className="mt-4 flex flex-wrap items-center justify-center gap-x-3 gap-y-1
                        text-base font-medium text-sky-100 sm:text-lg">
            <span>Core sets</span>
            <span aria-hidden="true" className="text-sky-300/70">&middot;</span>
            <span>Supportive materials</span>
            <span aria-hidden="true" className="text-sky-300/70">&middot;</span>
            <span>Ace CLB&nbsp;7</span>
          </p>
        </div>
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
