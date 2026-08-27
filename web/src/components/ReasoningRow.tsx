/**
 * Adapted from DeepSeek Harness ReasoningRow.tsx.
 * MIT License, Copyright (c) 2026 DeepSeek.
 */

import { useState } from 'react'

import styles from './ReasoningRow.module.css'

const firstLine = (text: string): string => text.split('\n', 1)[0]
const latestLine = (text: string): string => text.trimEnd().split('\n').at(-1) ?? ''

export function ReasoningRow({
  text,
  running,
  defaultExpanded = false,
}: {
  text: string
  running: boolean
  defaultExpanded?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  if (text.length === 0) return null
  const summary = running ? latestLine(text) : firstLine(text)
  return (
    <section className={styles.root} data-state={running ? 'running' : 'settled'}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={expanded}
        aria-label={running ? 'Thinking, streaming' : 'Thinking'}
        onClick={() => { setExpanded(value => !value) }}
      >
        <span className={styles.icon} aria-hidden>✦</span>
        <span>Thinking</span>
        <span className={styles.summary}>{summary}</span>
        <span className={styles.chevron} aria-hidden>{expanded ? '⌃' : '⌄'}</span>
      </button>
      {expanded && <pre className={styles.body}>{text}</pre>}
    </section>
  )
}
