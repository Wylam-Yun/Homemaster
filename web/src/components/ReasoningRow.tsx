/**
 * Adapted from DeepSeek Harness ReasoningRow.tsx.
 * MIT License, Copyright (c) 2026 DeepSeek.
 */

import { useEffect, useRef, useState } from 'react'

import styles from './ReasoningRow.module.css'

const firstLine = (text: string): string => text.split('\n', 1)[0]
const latestLine = (text: string): string => text.trimEnd().split('\n').at(-1) ?? ''

function settledLabel(durationMs: number | null): string {
  if (durationMs === null) return 'Thought'
  const seconds = Math.max(1, Math.round(durationMs / 1000))
  return `Thought for ${seconds}s`
}

export function ReasoningRow({
  text,
  running,
  defaultExpanded = false,
  searchKey,
}: {
  text: string
  running: boolean
  defaultExpanded?: boolean
  searchKey?: string
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const startedAtRef = useRef<number | null>(null)
  const [durationMs, setDurationMs] = useState<number | null>(null)
  useEffect(() => {
    if (running) {
      if (startedAtRef.current === null) startedAtRef.current = Date.now()
      return
    }
    if (startedAtRef.current !== null && durationMs === null) {
      setDurationMs(Date.now() - startedAtRef.current)
    }
  }, [running, durationMs])
  if (text.length === 0) return null
  const summary = running ? latestLine(text) : firstLine(text)
  return (
    <section className={styles.root} data-state={running ? 'running' : 'settled'} data-search-key={searchKey}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={expanded}
        aria-label={running ? 'Thinking, streaming' : 'Thinking'}
        onClick={() => { setExpanded(value => !value) }}
      >
        <span className={styles.icon} aria-hidden>✦</span>
        <span>{running ? 'Thinking' : settledLabel(durationMs)}</span>
        <span className={styles.summary}>{summary}</span>
        <span className={styles.chevron} aria-hidden>{expanded ? '⌃' : '⌄'}</span>
      </button>
      {expanded && <pre className={styles.body}>{text}</pre>}
    </section>
  )
}
