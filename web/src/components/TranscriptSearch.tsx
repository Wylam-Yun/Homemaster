import { useEffect, useMemo, useRef, useState } from 'react'

import styles from './TranscriptSearch.module.css'

export type TranscriptSearchEntry = {
  key: string
  text: string
}

export function TranscriptSearch({
  open,
  entries,
  onClose,
}: {
  open: boolean
  entries: TranscriptSearchEntry[]
  onClose: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)

  const matches = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    if (needle.length === 0) return []
    return entries.filter(entry => entry.text.toLocaleLowerCase().includes(needle))
  }, [entries, query])
  const active = matches.length === 0 ? undefined : matches[Math.min(index, matches.length - 1)]

  useEffect(() => {
    if (open) {
      setQuery('')
      setIndex(0)
      inputRef.current?.focus()
    }
  }, [open])

  // Esc closes even when focus left the input.
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => { window.removeEventListener('keydown', onKeyDown) }
  }, [open, onClose])

  useEffect(() => {
    if (!open) {
      document.querySelectorAll('[data-search-hit]').forEach(el => { el.removeAttribute('data-search-hit') })
      return
    }
    if (active === undefined) return
    const el = document.querySelector(`[data-search-key="${CSS.escape(active.key)}"]`)
    if (!(el instanceof HTMLElement)) return
    el.setAttribute('data-search-hit', 'true')
    el.scrollIntoView({ block: 'center' })
    return () => { el.removeAttribute('data-search-hit') }
  }, [open, active])

  if (!open) return null

  const step = (delta: number) => {
    if (matches.length === 0) return
    setIndex(value => (Math.min(value, matches.length - 1) + delta + matches.length) % matches.length)
  }

  return (
    <div className={styles.overlay} role="search">
      <input
        ref={inputRef}
        type="search"
        value={query}
        placeholder="搜索转录内容…"
        aria-label="搜索转录内容"
        onChange={event => { setQuery(event.target.value); setIndex(0) }}
        onKeyDown={event => {
          if (event.key === 'ArrowDown' || (event.key === 'Enter' && !event.shiftKey)) {
            event.preventDefault()
            step(1)
          } else if (event.key === 'ArrowUp' || (event.key === 'Enter' && event.shiftKey)) {
            event.preventDefault()
            step(-1)
          }
        }}
      />
      <span className={styles.count} aria-live="polite">
        {query.trim().length === 0 ? '' : `${matches.length === 0 ? 0 : Math.min(index, matches.length - 1) + 1}/${matches.length}`}
      </span>
      <button type="button" aria-label="上一处" onClick={() => { step(-1) }}>↑</button>
      <button type="button" aria-label="下一处" onClick={() => { step(1) }}>↓</button>
      <button type="button" aria-label="关闭搜索" onClick={onClose}>✕</button>
    </div>
  )
}
