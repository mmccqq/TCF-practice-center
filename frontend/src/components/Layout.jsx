import { Link, NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../lib/auth'

function NavItem({ to, children }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `rounded-md px-3 py-1.5 text-sm font-medium transition ${
          isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
        }`
      }
    >
      {children}
    </NavLink>
  )
}

export default function Layout() {
  const { user, signOut } = useAuth()
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
          <Link to="/" className="font-semibold tracking-tight">
            TCF<span className="text-sky-600">·</span>Practice
          </Link>
          <nav className="flex items-center gap-1">
            <NavItem to="/speaking/task2">Task&nbsp;2</NavItem>
            <NavItem to="/speaking/task3">Task&nbsp;3</NavItem>
          </nav>
          <div className="ml-auto flex items-center gap-2 text-sm">
            {user ? (
              <>
                <span className="text-slate-500">{user.display_name || user.email}</span>
                <button
                  onClick={signOut}
                  className="rounded-md border border-slate-300 px-3 py-1.5 font-medium hover:bg-slate-50"
                >
                  Sign out
                </button>
              </>
            ) : (
              <>
                <Link to="/login" className="rounded-md px-3 py-1.5 font-medium hover:bg-slate-100">
                  Log in
                </Link>
                <Link
                  to="/signup"
                  className="rounded-md bg-slate-900 px-3 py-1.5 font-medium text-white hover:bg-slate-800"
                >
                  Sign up
                </Link>
              </>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-8">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-5xl px-4 pb-10 text-xs text-slate-400">
        Questions archived from public TCF Canada practice sources. Personal study project.
      </footer>
    </div>
  )
}
