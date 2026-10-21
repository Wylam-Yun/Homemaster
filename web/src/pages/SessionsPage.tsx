import type { SessionSummary } from '../api/http'
import { Link, chatHash } from '../router'
import styles from './SessionsPage.module.css'

export type SessionSignal = 'running' | 'awaiting' | 'done'

type Props = {
  sessions: SessionSummary[]
  sessionId: string | null
  signals: Record<string, SessionSignal>
  sessionTitle: (session: SessionSummary) => string
  formatTime: (value: string | null) => string
}

/**
 * `#/sessions` landing view — the full session list (the sidebar's sibling
 * surface) so flows that drop the selection (e.g. deleting the open session)
 * have somewhere meaningful to land.
 */
export function SessionsPage({ sessions, sessionId, signals, sessionTitle, formatTime }: Props) {
  return (
    <section className={styles.page} aria-labelledby="sessions-page-title">
      <div className={styles.headingRow}>
        <div>
          <p className={styles.eyebrow}>会话</p>
          <h1 id="sessions-page-title">全部会话</h1>
          <p>共 {sessions.length} 个会话，点击继续。</p>
        </div>
      </div>
      <div className={styles.list}>
        {sessions.length === 0 && <div className={styles.empty}>还没有会话</div>}
        {sessions.map(session => {
          const title = sessionTitle(session)
          const meta = [formatTime(session.updated_at), `${session.message_count} 条`]
            .filter(part => part.length > 0)
            .join(' · ')
          return (
            <Link
              key={session.session_id}
              to={chatHash(session.session_id)}
              className={styles.row}
              data-active={session.session_id === sessionId || undefined}
              aria-label={`打开会话 ${title}`}
            >
              <span className={styles.dot} data-status={signals[session.session_id] ?? 'idle'} aria-hidden="true" />
              <span className={styles.title}>{title}</span>
              <span className={styles.meta}>{meta.length > 0 ? meta : session.session_id.slice(0, 12)}</span>
            </Link>
          )
        })}
      </div>
    </section>
  )
}
