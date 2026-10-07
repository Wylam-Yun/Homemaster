import { useState } from 'react'

import type { ToolRecord } from '../state/projection'
import { ToolCallCard } from './ToolCallCard'
import styles from './ToolGroup.module.css'

const STATUS_ICON: Record<ToolRecord['status'], string> = {
  running: '●',
  completed: '✓',
  failed: '✕',
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
              <span className={styles.status} data-state={record.status} aria-hidden>{STATUS_ICON[record.status]}</span>
              <span className={styles.name}>{record.name}</span>
              <span className={styles.summary}>{record.argSummary}</span>
              <span className={styles.chevron} aria-hidden>{expanded ? '⌃' : '⌄'}</span>
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
