import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import styles from './toast.module.css'

export type ToastAction = { label: string; onClick: () => void }
export type ToastOptions = {
  title: string
  description?: string
  action?: ToastAction
  /** ms before auto-dismiss; default 5000, pass <= 0 to pin the toast. */
  duration?: number
}
export type ToastApi = {
  /** Returns the toast id so callers can dismiss() programmatically. */
  show: (toast: ToastOptions) => string
  dismiss: (id: string) => void
}

type Toast = ToastOptions & { id: string; leaving: boolean }

const ToastContext = createContext<ToastApi | null>(null)

const EXIT_MS = 180
const DEFAULT_DURATION_MS = 5000

export function ToastProvider({ children }: { children: ReactNode }) {
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const [toasts, setToasts] = useState<Toast[]>([])

  const dismiss = useCallback((id: string) => {
    const timer = timers.current.get(id)
    if (timer !== undefined) clearTimeout(timer)
    setToasts(list => list.map(toast => (toast.id === id ? { ...toast, leaving: true } : toast)))
    // data-leaving drives the exit animation; the record is removed one beat later.
    timers.current.set(id, setTimeout(() => {
      timers.current.delete(id)
      setToasts(list => list.filter(toast => toast.id !== id))
    }, EXIT_MS))
  }, [])

  const show = useCallback((options: ToastOptions): string => {
    const id = crypto.randomUUID()
    setToasts(list => [...list, { ...options, id, leaving: false }])
    const duration = options.duration ?? DEFAULT_DURATION_MS
    if (duration > 0) {
      timers.current.set(id, setTimeout(() => { dismiss(id) }, duration))
    }
    return id
  }, [dismiss])

  useEffect(() => {
    const pending = timers.current
    return () => {
      for (const timer of pending.values()) clearTimeout(timer)
      pending.clear()
    }
  }, [])

  const api = useMemo<ToastApi>(() => ({ show, dismiss }), [show, dismiss])

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        <div className={styles.stack} aria-live="polite" aria-label="通知">
          {toasts.map(toast => (
            <div className={styles.toast} key={toast.id} role="status" data-leaving={toast.leaving || undefined}>
              <div className={styles.body}>
                <span className={styles.title}>{toast.title}</span>
                {toast.description !== undefined && <span className={styles.description}>{toast.description}</span>}
              </div>
              {toast.action !== undefined && (
                <button
                  type="button"
                  className={styles.action}
                  onClick={() => { toast.action?.onClick(); dismiss(toast.id) }}
                >
                  {toast.action.label}
                </button>
              )}
              <button type="button" className={styles.close} aria-label="关闭通知" onClick={() => { dismiss(toast.id) }}>✕</button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const api = useContext(ToastContext)
  if (api === null) throw new Error('useToast must be used inside a ToastProvider')
  return api
}

/** Null outside a ToastProvider — for components that can live without one. */
export function useToastOptional(): ToastApi | null {
  return useContext(ToastContext)
}

const TITLE_PREFIX = '● '

/** Minimal favicon/status signal: prefixes document.title with ● while running. */
export function useFaviconStatus(running: boolean): void {
  const baseTitleRef = useRef<string | null>(null)
  useEffect(() => {
    if (baseTitleRef.current === null) {
      baseTitleRef.current = document.title.startsWith(TITLE_PREFIX)
        ? document.title.slice(TITLE_PREFIX.length)
        : document.title
    }
    const base = baseTitleRef.current
    document.title = running ? `${TITLE_PREFIX}${base}` : base
    return () => {
      if (baseTitleRef.current !== null) document.title = baseTitleRef.current
    }
  }, [running])
}
