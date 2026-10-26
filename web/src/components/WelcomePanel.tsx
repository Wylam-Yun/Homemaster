import type { MetaInfo, ProviderProfile, SessionSummary } from '../api/http'
import styles from './WelcomePanel.module.css'

const SUGGESTIONS: Array<{ tag: string; title: string; prompt: string }> = [
  { tag: '家居任务', title: '拿取物品', prompt: '去卧室把桌上的杯子拿过来' },
  { tag: '家居任务', title: '巡视检查', prompt: '巡视客厅，报告有没有遗落的物品' },
  { tag: '文件与检索', title: '整理文件', prompt: '帮我整理下载目录里的文件，按类型归类' },
  { tag: '长期记忆', title: '回忆偏好', prompt: '你还记得我上次让你记住的偏好吗？' },
  { tag: '技能与规划', title: '查看能力清单', prompt: '帮我看看你现在有哪些工具和技能，能帮我做什么事情？' },
]

export function WelcomePanel({
  recentSessions,
  providers,
  meta,
  memoryCount,
  sessionTitle,
  onSuggestion,
  onContinue,
}: {
  recentSessions: SessionSummary[]
  providers: ProviderProfile[]
  meta: MetaInfo | null
  memoryCount: number | undefined
  sessionTitle: (session: SessionSummary) => string
  onSuggestion: (text: string) => void
  onContinue: (sessionId: string) => void
}) {
  const defaultProvider = providers.find(item => item.is_default === true)
    ?? providers.find(item => item.kind !== 'embedding' && item.api_key_configured)
  return (
    <div className={styles.welcome}>
      <div className={styles.heroBadge}>HomeMaster 智能助手</div>
      <h1>今天想让我帮你做什么？</h1>
      <p>支持多步推理、调用本地工具与技能扩展、跨对话长期记忆；涉及敏感操作时会先征得你的同意。</p>
      <div className={styles.statusStrip} aria-label="当前运行状态">
        <span className={styles.statusItem}>
          <small>对话模型</small>
          <strong>{defaultProvider !== undefined ? `${defaultProvider.name} · ${defaultProvider.model || '默认'}` : '未配置'}</strong>
        </span>
        <span className={styles.statusItem}>
          <small>记忆档位</small>
          <strong>{meta === null ? '检测中…' : meta.memory_mode === 'full' ? '完整图谱记忆' : '轻量文件记忆'}</strong>
        </span>
        {memoryCount !== undefined && memoryCount > 0 && (
          <span className={styles.statusItem}>
            <small>长期记忆</small>
            <strong>{memoryCount} 条</strong>
          </span>
        )}
      </div>
      <div className={styles.suggestions} aria-label="建议任务">
        {SUGGESTIONS.map(suggestion => (
          <button
            type="button"
            key={suggestion.prompt}
            className={styles.suggestion}
            onClick={() => { onSuggestion(suggestion.prompt) }}
          >
            <small className={styles.tag}>{suggestion.tag}</small>
            <strong>{suggestion.title}</strong>
            <span>{suggestion.prompt}</span>
          </button>
        ))}
      </div>
      {recentSessions.length > 0 && (
        <div className={styles.recent}>
          <h2>继续最近的对话</h2>
          {recentSessions.map(session => (
            <button
              type="button"
              key={session.session_id}
              className={styles.recentRow}
              onClick={() => { onContinue(session.session_id) }}
            >
              <span>{sessionTitle(session)}</span>
              <small>继续对话 →</small>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
