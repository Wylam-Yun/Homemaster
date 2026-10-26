import { useMemo, useState } from 'react'

import type { ArchiveStatus, SessionSummary } from '../api/http'
import { Badge } from '../components/ui/Badge'
import { PageHeader } from '../components/ui/PageHeader'
import { Link, chatHash } from '../router'
import styles from './SessionsPage.module.css'

export type SessionSignal = 'running' | 'awaiting' | 'done'

type SortKey = 'updated' | 'title' | 'count'

type Props = {
  sessions: SessionSummary[]
  sessionId: string | null
  signals: Record<string, SessionSignal>
  sessionTitle: (session: SessionSummary) => string
  formatTime: (value: string | null) => string
  archiveStates?: Record<string, ArchiveStatus>
  archiveBadgeText?: (status: ArchiveStatus | undefined) => string | null
  /** Shared rename state with the sidebar so both surfaces stay in sync. */
  renamingId: string | null
  renameDraft: string
  onRenameDraft(value: string): void
  onStartRename(session: SessionSummary): void
  onCommitRename(session: SessionSummary): void
  onCancelRename(): void
  onArchive(session: SessionSummary): void
  onDelete(session: SessionSummary): void
  archivingIds: ReadonlySet<string>
  /** Archive is only meaningful when the memory backend runs in full mode. */
  canArchive?: boolean
}

const SORT_LABELS: Record<SortKey, string> = {
  updated: '最近更新',
  title: '名称',
  count: '消息数',
}

/**
 * `#/sessions` management view — search, sort and per-session actions on top
 * of the same summaries the sidebar lists.
 */
export function SessionsPage({
  sessions,
  sessionId,
  signals,
  sessionTitle,
  formatTime,
  archiveStates,
  archiveBadgeText,
  renamingId,
  renameDraft,
  onRenameDraft,
  onStartRename,
  onCommitRename,
  onCancelRename,
  onArchive,
  onDelete,
  archivingIds,
  canArchive = false,
}: Props) {
  const [query, setQuery] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('updated')

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const filtered = needle === ''
      ? sessions
      : sessions.filter(session => sessionTitle(session).toLowerCase().includes(needle))
    const ordered = [...filtered]
    if (sortKey === 'title') {
      ordered.sort((a, b) => sessionTitle(a).localeCompare(sessionTitle(b)))
    } else if (sortKey === 'count') {
      ordered.sort((a, b) => b.message_count - a.message_count)
    } else {
      ordered.sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
    }
    return ordered
  }, [query, sessions, sessionTitle, sortKey])

  return (
    <section className={styles.page} aria-labelledby="sessions-page-title">
      <PageHeader
        eyebrow="会话"
        title="会话管理"
        titleId="sessions-page-title"
        subtitle={`共 ${sessions.length} 个会话。可搜索、排序，并直接归档、重命名或删除。`}
      />

      <div className={styles.toolbar}>
        <input
          type="search"
          className={styles.search}
          value={query}
          placeholder="按标题搜索会话…"
          aria-label="搜索会话"
          onChange={event => { setQuery(event.target.value) }}
        />
        <label className={styles.sortField}>
          <span>排序</span>
          <select
            value={sortKey}
            aria-label="排序方式"
            onChange={event => { setSortKey(event.target.value as SortKey) }}
          >
            {(Object.keys(SORT_LABELS) as SortKey[]).map(key => (
              <option key={key} value={key}>{SORT_LABELS[key]}</option>
            ))}
          </select>
        </label>
      </div>

      <div className={styles.list}>
        {sessions.length === 0 && <div className={styles.empty}>还没有会话</div>}
        {sessions.length > 0 && visible.length === 0 && (
          <div className={styles.empty}>没有匹配的会话</div>
        )}
        {visible.map(session => {
          const title = sessionTitle(session)
          const renaming = renamingId === session.session_id
          const archiveState = archiveStates?.[session.session_id]
          const badge = archiveBadgeText?.(archiveState)
          const badgeVariant = archiveState?.state === 'failed'
            ? 'warning'
            : archiveState?.state === 'completed'
              ? 'success'
              : 'muted'
          const archiving = archivingIds.has(session.session_id)
          return (
            <article
              key={session.session_id}
              className={styles.row}
              data-active={session.session_id === sessionId || undefined}
            >
              <span className={styles.dot} data-status={signals[session.session_id] ?? 'idle'} aria-hidden="true" />
              <div className={styles.main}>
                {renaming ? (
                  <input
                    className={styles.rename}
                    type="text"
                    value={renameDraft}
                    aria-label={`重命名会话 ${title}`}
                    autoFocus
                    onChange={event => { onRenameDraft(event.target.value) }}
                    onBlur={() => { onCommitRename(session) }}
                    onKeyDown={event => {
                      if (event.key === 'Enter') {
                        event.preventDefault()
                        onCommitRename(session)
                      } else if (event.key === 'Escape') {
                        event.preventDefault()
                        onCancelRename()
                      }
                    }}
                  />
                ) : (
                  <span className={styles.title}>{title}</span>
                )}
                <span className={styles.meta}>
                  {[formatTime(session.updated_at), `${session.message_count} 条`]
                    .filter(part => part.length > 0)
                    .join(' · ')}
                  {badge !== null && badge !== undefined && (
                    <Badge variant={badgeVariant}>{badge}</Badge>
                  )}
                </span>
              </div>
              <div className={styles.actions}>
                <Link to={chatHash(session.session_id)} className={styles.action} aria-label={`打开会话 ${title}`}>
                  打开
                </Link>
                <button
                  type="button"
                  className={styles.action}
                  aria-label={`重命名会话 ${title}`}
                  onClick={() => { onStartRename(session) }}
                >重命名</button>
                {canArchive && (
                  <button
                    type="button"
                    className={styles.action}
                    aria-label={`归档会话 ${title}`}
                    disabled={archiving}
                    onClick={() => { onArchive(session) }}
                  >{archiving ? '归档中…' : '归档'}</button>
                )}
                <button
                  type="button"
                  className={`${styles.action} ${styles.danger}`}
                  aria-label={`删除会话 ${title}`}
                  onClick={() => { onDelete(session) }}
                >删除</button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
