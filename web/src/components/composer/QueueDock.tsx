import styles from './QueueDock.module.css'

export type QueuedPrompt = {
  id: string
  text: string
}

function preview(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length <= 48 ? flat : `${flat.slice(0, 48)}…`
}

/**
 * QueueDock：busy 时输入不丢——消息排进本地队列，转录下方一行可预览/删除，
 * 回合终态由父级按序自动发送。
 */
export function QueueDock({
  items,
  onRemove,
  onClear,
}: {
  items: QueuedPrompt[]
  onRemove: (id: string) => void
  onClear: () => void
}) {
  if (items.length === 0) return null
  return (
    <div className={styles.dock} aria-label="排队消息">
      <span className={styles.count}>排队 {items.length} 条</span>
      <ol className={styles.list}>
        {items.map((item, index) => (
          <li key={item.id} className={styles.item}>
            <span className={styles.order} aria-hidden>{index + 1}.</span>
            <span className={styles.preview} title={item.text}>{preview(item.text)}</span>
            <button
              type="button"
              className={styles.remove}
              aria-label={`移除排队消息 ${index + 1}`}
              onClick={() => { onRemove(item.id) }}
            >
              ✕
            </button>
          </li>
        ))}
      </ol>
      <button type="button" className={styles.clear} onClick={onClear}>清空</button>
    </div>
  )
}
