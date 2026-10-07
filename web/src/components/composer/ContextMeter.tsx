import type { SessionStatusInfo } from '../../api/http'
import type { Usage } from '../../protocol/events'
import styles from './ContextMeter.module.css'

// The status endpoint carries no context-window size yet; the ring's
// denominator falls back to this estimate until a compaction event (or a
// future status field) reveals the real window.
export const FALLBACK_CONTEXT_TOKENS = 200_000

function totalTokens(usage: Usage | null): number {
  if (usage === null) return 0
  if (typeof usage.total_tokens === 'number' && Number.isFinite(usage.total_tokens)) {
    return Math.max(0, usage.total_tokens)
  }
  const input = typeof usage.input_tokens === 'number' ? usage.input_tokens : 0
  const output = typeof usage.output_tokens === 'number' ? usage.output_tokens : 0
  return Math.max(0, input + output)
}

function formatTokens(tokens: number): string {
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(tokens >= 100_000 ? 0 : 1)}k`
  return String(tokens)
}

/**
 * 上下文用量环：最新回合的 token 用量 + hover 明细（generation/revision/
 * task_status），右侧 compact 按钮手动触发服务端压缩。
 */
export function ContextMeter({
  usage,
  status,
  contextLimit,
  disabled = false,
  onCompact,
}: {
  usage: Usage | null
  status: SessionStatusInfo | null
  /** Observed context ceiling (e.g. the last compaction's before_tokens). */
  contextLimit?: number | null
  disabled?: boolean
  onCompact?: () => void
}) {
  const tokens = totalTokens(usage)
  const limit = contextLimit !== undefined && contextLimit !== null && contextLimit > 0
    ? contextLimit
    : FALLBACK_CONTEXT_TOKENS
  const fraction = Math.min(1, tokens / limit)
  const percent = Math.round(fraction * 100)

  const radius = 8
  const circumference = 2 * Math.PI * radius
  const detail = [
    `上下文用量 ${tokens.toLocaleString()} / ~${limit.toLocaleString()} tokens`,
    `generation ${status?.generation ?? '—'} · revision ${status?.revision ?? '—'}`,
    `task ${status?.task_status ?? '—'} · session ${status?.status ?? '—'}`,
  ].join('\n')

  return (
    <div className={styles.meter} title={detail}>
      <svg
        className={styles.ring}
        viewBox="0 0 20 20"
        role="img"
        aria-label={`上下文用量 ${percent}%`}
      >
        <circle className={styles.ringTrack} cx="10" cy="10" r={radius} />
        <circle
          className={styles.ringFill}
          cx="10"
          cy="10"
          r={radius}
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - fraction)}
          data-level={percent >= 90 ? 'high' : percent >= 70 ? 'mid' : 'low'}
        />
      </svg>
      <span className={styles.readout}>{formatTokens(tokens)}</span>
      {onCompact !== undefined && (
        <button
          type="button"
          className={styles.compact}
          aria-label="压缩上下文"
          title="手动触发上下文压缩"
          disabled={disabled}
          onClick={onCompact}
        >
          ⇓
        </button>
      )}
    </div>
  )
}
