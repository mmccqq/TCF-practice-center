import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../../lib/auth'

const TABS = [
  { to: '/admin/data', label: 'Data' },
  { to: '/admin/jobs', label: 'Runs' },
  { to: '/admin/vocabulary', label: 'Vocabulary' },
  { to: '/admin/questions', label: 'Labelling' },
  { to: '/admin/reviews', label: 'Review' },
  { to: '/admin/compare', label: 'Compare' },
]

/** Shell for the admin tools. Also the client-side gate - the real one is
 *  `current_admin` on the server; this only decides what to draw. */
export default function AdminLayout() {
  const { user, loading } = useAuth()

  if (loading) return <p className="text-sm text-slate-500">Loading…</p>
  if (!user?.is_admin) {
    return (
      <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
        This area is for administrators.
      </p>
    )
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Admin</h1>
        <p className="mt-1 text-sm text-slate-600">
          Vocabulary, labelling and review. Changes here are live — they write
          straight to the question bank.
        </p>
      </div>
      <nav className="flex flex-wrap gap-1 border-b border-slate-200 pb-2">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            className={({ isActive }) =>
              `rounded-md px-3 py-1.5 text-sm font-medium transition ${
                isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
              }`
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}

/** Task 2 / Task 3 switch, shared by the admin pages. */
export function TacheTabs({ tache, onChange }) {
  return (
    <div className="flex gap-1">
      {[2, 3].map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
            t === tache ? 'bg-sky-600 text-white' : 'text-slate-600 hover:bg-slate-200'
          }`}
        >
          Task {t}
        </button>
      ))}
    </div>
  )
}
