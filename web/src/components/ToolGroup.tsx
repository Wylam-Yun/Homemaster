import { useState } from 'react'

import type { ToolRecord } from '../state/projection'
import { ToolCallCard, toolDisplayLabel } from './ToolCallCard'
import { IconCheck, IconChevronDown, IconChevronRight, IconClose } from './ui/icons'
import styles from './ToolGroup.module.css'

function StatusIcon({ status }: { status: ToolRecord['status'] }) {
  if (status === 'running') return <span className={styles.pulse} />
  if (status === 'completed') return <IconCheck size={12} />
  return <IconClose size={12} />
}

function formatDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined || ms < 0) return null
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function ToolGroup({
  tools,
  sessionId,
  defaultOpen = false,
  searchKeyPrefix,
}: {
  tools: ToolRecord[]
  sessionId?: string
  defaultOpen?: boolean
  searchKeyPrefix?: string
}) {
  const [openItems, setOpenItems] = useState<Record<string, boolean>>({})
  return (
    <div className={styles.group} aria-label="工具调用">
      {tools.map(record => {
        const expanded = openItems[record.toolCallId] ?? defaultOpen
        const toggle = () => {
          setOpenItems(items => ({ ...items, [record.toolCallId]: !expanded }))
        }
        const label = toolDisplayLabel(record.name)
        const duration = formatDuration(record.durationMs)
        return (
          <div
            className={styles.item}
            key={record.toolCallId}
            data-search-key={searchKeyPrefix === undefined ? undefined : `${searchKeyPrefix}:${record.toolCallId}`}
          >
            <button
              type="button"
              className={styles.row}
              aria-expanded={expanded}
              aria-label={`工具调用 ${record.name}`}
              onClick={toggle}
            >
              <span className={styles.status} data-state={record.status} aria-hidden><StatusIcon status={record.status} /></span>
              {label !== null && <span className={styles.friendlyLabel}>{label}</span>}
              <span className={styles.name}>{record.name}</span>
              <span className={styles.summary}>{record.argSummary}</span>
              {duration !== null && <span className={styles.duration}>{duration}</span>}
              <span className={styles.chevron} aria-hidden>{expanded ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}</span>
            </button>
            {expanded && (
              <div className={styles.detail}>
                <ToolCallCard tool={record.tool} sessionId={sessionId} defaultOpen={defaultOpen} />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
