import type { UiMode } from '../../protocol/events'
import styles from './ModeBadge.module.css'

const MODE_LABEL: Record<UiMode, string> = { plan: '规划', act: '执行' }
const MODE_HINT: Record<UiMode, string> = {
  plan: '规划模式：先出计划，只读工具可用',
  act: '执行模式：正常执行',
}

/** Plan/act 徽标——点击在两种模式间切换，服务端持久化并广播 session.mode_changed。 */
export function ModeBadge({
  mode,
  disabled = false,
  onToggle,
}: {
  mode: UiMode
  disabled?: boolean
  onToggle?: (next: UiMode) => void
}) {
  const next: UiMode = mode === 'plan' ? 'act' : 'plan'
  return (
    <button
      type="button"
      className={styles.badge}
      data-mode={mode}
      aria-pressed={mode === 'plan'}
      disabled={disabled}
      title={`${MODE_HINT[mode]} · 点击切换到${MODE_LABEL[next]}模式`}
      onClick={() => { onToggle?.(next) }}
    >
      <span aria-hidden>{mode === 'plan' ? '▤' : '▶'}</span>
      {MODE_LABEL[mode]}
    </button>
  )
}
