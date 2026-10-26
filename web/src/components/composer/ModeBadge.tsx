import type { UiMode } from '../../protocol/events'
import styles from './ModeBadge.module.css'

const MODE_LABEL: Record<UiMode, string> = { plan: '规划', act: '执行' }
const MODE_HINT: Record<UiMode, string> = {
  plan: '规划模式：先出计划，只读工具可用',
  act: '执行模式：正常执行',
}
const MODES: readonly UiMode[] = ['act', 'plan']

/** 规划/执行分段开关——当前模式高亮，点击另一档切换；服务端持久化并广播 session.mode_changed。 */
export function ModeBadge({
  mode,
  disabled = false,
  onToggle,
}: {
  mode: UiMode
  disabled?: boolean
  onToggle?: (next: UiMode) => void
}) {
  return (
    <div className={styles.switch} role="group" aria-label="运行模式">
      {MODES.map(value => (
        <button
          key={value}
          type="button"
          className={styles.option}
          data-mode={value}
          data-active={mode === value || undefined}
          aria-pressed={mode === value}
          disabled={disabled}
          title={MODE_HINT[value]}
          onClick={() => { if (value !== mode) onToggle?.(value) }}
        >
          {MODE_LABEL[value]}
        </button>
      ))}
    </div>
  )
}
