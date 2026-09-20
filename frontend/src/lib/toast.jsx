import { createContext, useCallback, useContext, useEffect, useState } from 'react'

const ToastContext = createContext(() => {})

/**
 * Short confirmations that do not need dismissing.
 *
 * Used where an action's result appears somewhere else - starting a job leaves
 * you on the page you were working on, so something has to say it worked and
 * where to look.
 */
export function ToastProvider({ children }) {
  const [items, setItems] = useState([])

  const push = useCallback((message, tone = 'info') => {
    const id = Math.random().toString(36).slice(2)
    setItems((xs) => [...xs, { id, message, tone }])
    return id
  }, [])

  useEffect(() => {
    if (!items.length) return undefined
    const t = setTimeout(() => setItems((xs) => xs.slice(1)), 5000)
    return () => clearTimeout(t)
  }, [items])

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`pointer-events-auto max-w-sm rounded-lg px-4 py-3 text-sm
                        shadow-lg ${
              t.tone === 'error' ? 'bg-red-600 text-white' : 'bg-slate-900 text-white'
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)
