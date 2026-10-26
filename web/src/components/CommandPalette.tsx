import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { SessionSummary } from '../api/http'
import styles from './CommandPalette.module.css'

export type CommandPaletteAction = {
  id: string
  label: string
  hint?: string
  /** Extra lowercase search terms matched in addition to the label. */
  keywords?: string
  run: () => void
}

type FlatItem =
  | { kind: 'action'; action: CommandPaletteAction }
  | { kind: 'session'; session: SessionSummary }

type Props = {
  actions: CommandPaletteAction[]
  sessions: SessionSummary[]
  onSelectSession: (sessionId: string) => void
  onClose: () => void
}

/**
 * ⌘K / Ctrl+K 命令面板：固定命令（新建会话、切换页面、切换主题、运行自检）
 * 加上历史会话搜索跳转。↑↓ 选择、Enter 执行、Esc/点击遮罩关闭。
 */
export function CommandPalette({ actions, sessions, onSelectSession, onClose }: Props) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const normalized = query.trim().toLowerCase()
  const items = useMemo<FlatItem[]>(() => {
    const matchedActions = actions.filter(action => {
      if (normalized === '') return true
      return action.label.toLowerCase().includes(normalized)
        || (action.keywords ?? '').includes(normalized)
    })
    const matchedSessions = sessions
      .filter(session => normalized === ''
        || session.title.toLowerCase().includes(normalized)
        || session.session_id.toLowerCase().includes(normalized))
      .slice(0, 8)
    return [
      ...matchedActions.map(action => ({ kind: 'action' as const, action })),
      ...matchedSessions.map(session => ({ kind: 'session' as const, session })),
    ]
  }, [actions, sessions, normalized])

  useEffect(() => {
    setActive(0)
  }, [normalized])

  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-index="${active}"]`)
      ?.scrollIntoView?.({ block: 'nearest' })
  }, [active])

  const run = useCallback((item: FlatItem) => {
    onClose()
    if (item.kind === 'action') item.action.run()
    else onSelectSession(item.session.session_id)
  }, [onClose, onSelectSession])

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive(current => Math.min(current + 1, items.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive(current => Math.max(current - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const item = items[Math.min(active, items.length - 1)]
      if (item !== undefined) run(item)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }

  const firstSessionIndex = items.findIndex(item => item.kind === 'session')

  return (
    <div className={styles.backdrop} onClick={onClose} role="presentation">
      <div
        className={styles.palette}
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        onClick={event => { event.stopPropagation() }}
      >
        <input
          ref={inputRef}
          className={styles.input}
          value={query}
          onChange={event => { setQuery(event.target.value) }}
          onKeyDown={onKeyDown}
          placeholder="搜索命令或会话…"
          aria-label="搜索命令或会话"
        />
        <ul ref={listRef} className={styles.list} role="listbox" aria-label="候选">
          {items.length === 0 && <li className={styles.empty}>没有匹配的命令或会话</li>}
          {items.map((item, index) => (
            <li key={item.kind === 'action' ? `a-${item.action.id}` : `s-${item.session.session_id}`}>
              {index === firstSessionIndex && index > 0 && <div className={styles.section}>会话</div>}
              <button
                type="button"
                role="option"
                aria-selected={index === active}
                data-index={index}
                data-active={index === active || undefined}
                className={styles.item}
                onMouseEnter={() => { setActive(index) }}
                onClick={() => { run(item) }}
              >
                {item.kind === 'action' ? (
                  <>
                    <span className={styles.itemLabel}>{item.action.label}</span>
                    {item.action.hint !== undefined && <span className={styles.itemHint}>{item.action.hint}</span>}
                  </>
                ) : (
                  <>
                    <span className={styles.itemLabel}>{item.session.title}</span>
                    <span className={styles.itemHint}>{item.session.message_count} 条</span>
                  </>
                )}
              </button>
            </li>
          ))}
        </ul>
        <footer className={styles.footer}>
          <span><kbd>↑↓</kbd> 选择</span>
          <span><kbd>Enter</kbd> 执行</span>
          <span><kbd>Esc</kbd> 关闭</span>
        </footer>
      </div>
    </div>
  )
}
