import type { FeedbackActionDiff } from '../api/http'
import { Badge, type BadgeVariant } from './ui/Badge'
import styles from './ActionDiffCard.module.css'

/** One memory mutation rendered as a before/after pair — shared by the
 * feedback runs page and the Dreaming page since both consume the same
 * backend diff shape. */
export type ActionDiffData = FeedbackActionDiff

const ACTION_LABEL: Record<string, string> = {
  add: '新增',
  update: '更新',
  delete: '删除',
  noop: '无操作',
}

const ACTION_VARIANT: Record<string, BadgeVariant> = {
  add: 'success',
  update: 'accent',
  delete: 'danger',
}

type Props = {
  action: ActionDiffData
  /** Column headers, e.g. 整理前/整理后 or 改动前/改动后. */
  beforeLabel: string
  afterLabel: string
  /** Optional provenance label rendered next to the memory id. */
  sessionLabel?: string
}

export function ActionDiffCard({ action, beforeLabel, afterLabel, sessionLabel }: Props) {
  return (
    <div className={styles.actionCard}>
      <div className={styles.actionTop}>
        <Badge variant={ACTION_VARIANT[action.action] ?? 'muted'}>
          {ACTION_LABEL[action.action] ?? action.action}
        </Badge>
        {action.memory_id !== null && <code className={styles.memoryId}>{action.memory_id.slice(0, 8)}…</code>}
        {sessionLabel !== undefined && <span className={styles.sessionLabel}>{sessionLabel}</span>}
      </div>
      {action.missing ? (
        <p className={styles.missingText}>该记忆已无法在存储中读取。</p>
      ) : (
        <div className={styles.diff}>
          <div className={styles.diffSide}>
            <span className={styles.diffLabel}>{beforeLabel}</span>
            <p>{action.before?.content ?? (action.action === 'add' ? '（新记忆）' : '（无旧版本）')}</p>
            {action.before !== null && action.before !== undefined && (
              <span className={styles.diffStatus}>{action.before.status}</span>
            )}
          </div>
          <div className={styles.diffArrow} aria-hidden="true">→</div>
          <div className={styles.diffSide}>
            <span className={styles.diffLabel}>{afterLabel}</span>
            <p>{action.after?.content ?? '—'}</p>
            {action.after !== null && action.after !== undefined && (
              <span className={styles.diffStatus}>{action.after.status}</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
