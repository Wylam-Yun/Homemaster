import type { SessionSummary } from '../api/http'
import styles from './WelcomePanel.module.css'

const SUGGESTIONS: Array<{ title: string; prompt: string }> = [
  { title: '拿取物品', prompt: '去卧室把桌上的杯子拿过来' },
  { title: '巡视检查', prompt: '巡视客厅，报告有没有遗落的物品' },
  { title: '整理归位', prompt: '把沙发上的书放回书架' },
]

export function WelcomePanel({
  recentSessions,
  sessionTitle,
  onSuggestion,
  onContinue,
}: {
  recentSessions: SessionSummary[]
  sessionTitle: (session: SessionSummary) => string
  onSuggestion: (text: string) => void
  onContinue: (sessionId: string) => void
}) {
  return (
    <div className={styles.welcome}>
      <span className={styles.spark} aria-hidden>✦</span>
      <h1>What should we work on?</h1>
      <p>Can reason, use local tools, and ask before dangerous operations.</p>
      <div className={styles.suggestions} aria-label="建议任务">
        {SUGGESTIONS.map(suggestion => (
          <button
            type="button"
            key={suggestion.prompt}
            className={styles.suggestion}
            onClick={() => { onSuggestion(suggestion.prompt) }}
          >
            <strong>{suggestion.title}</strong>
            <span>{suggestion.prompt}</span>
          </button>
        ))}
      </div>
      {recentSessions.length > 0 && (
        <div className={styles.recent}>
          <h2>继续会话</h2>
          {recentSessions.map(session => (
            <button
              type="button"
              key={session.session_id}
              className={styles.recentRow}
              onClick={() => { onContinue(session.session_id) }}
            >
              <span>{sessionTitle(session)}</span>
              <small>继续</small>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
