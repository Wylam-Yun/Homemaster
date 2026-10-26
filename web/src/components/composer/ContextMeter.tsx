import type { SessionStatusInfo } from '../../api/http'
import type { Usage } from '../../protocol/events'
import { IconRefresh } from '../ui/icons'
import styles from './ContextMeter.module.css'

// The status endpoint carries no context-window size yet; the ring's
// denominator falls back to this estimate until a compaction event (or a
// future status field) reveals the real window.
export const FALLBACK_CONTEXT_TOKENS = 200_000

function contextTokens(usage: Usage | null, status?: SessionStatusInfo | null): number {
  if (usage !== null) {
    // Per-request prompt size is the real window occupancy; the *_tokens fields
    // are session-cumulative billing counters, kept as fallback for older
    // servers that predate the context_tokens field.
    const context = usage.context_tokens
    if (typeof context === 'number' && Number.isFinite(context)) {
      return Math.max(0, context)
    }
    if (typeof usage.total_tokens === 'number' && Number.isFinite(usage.total_tokens)) {
      return Math.max(0, usage.total_tokens)
    }
    const input = typeof usage.input_tokens === 'number' ? usage.input_tokens : 0
    const output = typeof usage.output_tokens === 'number' ? usage.output_tokens : 0
    if (input + output > 0) return Math.max(0, input + output)
  }
  if (status !== null && status !== undefined && typeof status.context_tokens === 'number' && Number.isFinite(status.context_tokens)) {
    return Math.max(0, status.context_tokens)
  }
  return 0
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
  disabled = false,
  compacting = false,
  onCompact,
}: {
  usage: Usage | null
  status: SessionStatusInfo | null
  disabled?: boolean
  compacting?: boolean
  onCompact?: () => void
}) {
  const tokens = contextTokens(usage, status)
  const limit = FALLBACK_CONTEXT_TOKENS
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
          title={compacting ? '正在压缩对话上下文，请稍候…' : '手动触发上下文压缩'}
          disabled={disabled || compacting}
          data-compacting={compacting || undefined}
          onClick={onCompact}
        >
          {compacting ? <><IconRefresh size={12} className={styles.spin} /> 压缩中…</> : '压缩'}
        </button>
      )}
    </div>
  )
}
