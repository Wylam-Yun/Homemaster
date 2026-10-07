import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import type { ItemChoice } from '../api/http'
import type { ApprovalItem } from '../protocol/events'
import type { ApprovalState } from '../state/conversation'
import styles from './ApprovalDialog.module.css'

export type ApprovalDecisions = Record<string, ItemChoice>

// 审批卡 2.0：once/session/always/deny 四档。always 需要二次确认后才写入决定；
// session 走协议扩展出的 allow_session 值（服务端 grant 语义按其范围执行）。
const CHOICES: ReadonlyArray<{ value: ItemChoice; label: string; hint?: string }> = [
  { value: 'allow_once', label: '本次允许' },
  { value: 'allow_session', label: '本会话允许', hint: '当前会话内对同一申请不再询问' },
  { value: 'allow_always', label: '始终允许', hint: '写入长期授权，需二次确认' },
  { value: 'reject', label: '拒绝' },
]

/** Keys that are identity/routing data, never shown in the metadata block. */
const INTERNAL_ITEM_KEYS = new Set(['item_id', 'approval_id', 'request_id', 'session_id', 'run_id'])

function newSubmissionId(): string {
  return crypto.randomUUID()
}

export type ApprovalItemSummary =
  | { kind: 'shell'; command: string }
  | { kind: 'file'; path: string; rest: Record<string, unknown> }
  | { kind: 'generic' }

/**
 * Parameter summary for one approval item. The approval payload carries semantic
 * fields (display_name/location/action_label) rather than raw tool args, so the
 * tool name is approximated by the action label; shell-like actions render the
 * location/display name as a `$` command, file-like actions lead with the path
 * and fold the remaining fields, everything else keeps the existing display.
 */
export function summarizeApprovalItem(item: ApprovalItem): ApprovalItemSummary {
  const name = `${item.action_label} ${item.display_name}`.toLowerCase()
  if (/shell|exec|bash/.test(name)) {
    return { kind: 'shell', command: item.location || item.display_name }
  }
  if (/file|write|edit/.test(name)) {
    const rest: Record<string, unknown> = { display_name: item.display_name, action_label: item.action_label }
    return { kind: 'file', path: item.location || item.display_name, rest }
  }
  return { kind: 'generic' }
}

/** Full per-item metadata minus internal identity keys, for the <details> block. */
export function approvalItemMetadata(item: ApprovalItem): Record<string, unknown> {
  return Object.fromEntries(Object.entries(item).filter(([key]) => !INTERNAL_ITEM_KEYS.has(key)))
}

function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

/** Seconds until the approval deadline; null when the timestamp is missing/invalid. */
function useApprovalCountdown(expiresAt: string): number | null {
  const target = Date.parse(expiresAt)
  const read = (): number | null =>
    Number.isNaN(target) ? null : Math.max(0, Math.round((target - Date.now()) / 1000))
  const [left, setLeft] = useState<number | null>(read)
  useEffect(() => {
    if (Number.isNaN(target)) {
      setLeft(null)
      return
    }
    const timer = setInterval(() => { setLeft(read()) }, 1000)
    return () => { clearInterval(timer) }
  }, [expiresAt])
  return left
}

export function ApprovalDialog({
  approval,
  busy,
  error,
  onSubmit,
  onClose,
}: {
  approval: ApprovalState
  busy: boolean
  error?: string | null
  onSubmit: (decisions: ApprovalDecisions, submissionId: string) => void
  onClose: (submissionId: string) => void
}) {
  const [decisions, setDecisions] = useState<ApprovalDecisions>({})
  const [submissionId, setSubmissionId] = useState<string>(newSubmissionId)
  // Local latch: the first click locks every control so a double-click (or a
  // slow parent re-render that delays `busy`) can never resubmit. Cleared when
  // the parent's busy cycle completes (retry stays possible after a failure)
  // or when a different approval request arrives.
  const [latched, setLatched] = useState(false)
  // 始终允许 二次确认：第一次选择只 arm（radio 呈现选中态但不写入决定），
  // 行内确认条确认后才落 decision；选别的档或取消都会解除 arm。
  const [alwaysArmed, setAlwaysArmed] = useState<string | null>(null)
  const sawBusyRef = useRef(false)
  const closeRef = useRef<HTMLButtonElement>(null)

  const locked = busy || latched
  const countdown = useApprovalCountdown(approval.expiresAt)

  useEffect(() => {
    setDecisions({})
    setSubmissionId(newSubmissionId())
    setLatched(false)
    setAlwaysArmed(null)
    sawBusyRef.current = false
  }, [approval.approvalId])

  useEffect(() => {
    if (busy) {
      sawBusyRef.current = true
      return
    }
    if (sawBusyRef.current) {
      sawBusyRef.current = false
      setLatched(false)
    }
  }, [busy])

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = overflow
      previous?.focus()
    }
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && !locked) {
        event.preventDefault()
        setLatched(true)
        onClose(submissionId)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
    }
  }, [locked, onClose, submissionId])

  const canSubmit = approval.items.length > 0
    && approval.items.every(item => decisions[item.item_id] !== undefined)

  const choose = (itemId: string, choice: ItemChoice): void => {
    if (locked) return
    if (choice === 'allow_always' && decisions[itemId] !== 'allow_always') {
      // First click arms the confirmation strip instead of writing the decision.
      setAlwaysArmed(itemId)
      return
    }
    setAlwaysArmed(previous => (previous === itemId ? null : previous))
    setDecisions(previous => ({ ...previous, [itemId]: choice }))
    setSubmissionId(newSubmissionId())
  }

  const confirmAlways = (itemId: string): void => {
    if (locked) return
    setAlwaysArmed(null)
    setDecisions(previous => ({ ...previous, [itemId]: 'allow_always' }))
    setSubmissionId(newSubmissionId())
  }

  const submit = (): void => {
    if (locked || !canSubmit) return
    setLatched(true)
    onSubmit(decisions, submissionId)
  }

  const close = (): void => {
    if (locked) return
    setLatched(true)
    onClose(submissionId)
  }

  return createPortal(
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-labelledby="approval-title">
      <section className={styles.dialog}>
        <header>
          <span aria-hidden>⚠</span>
          <div>
            <h2 id="approval-title">权限申请</h2>
            <p>{approval.intentSummary}</p>
            <p className={styles.hint}>
              逐项决定后提交；「本会话」在当前会话内复用，「始终允许」写入长期授权。
              {countdown !== null && (
                countdown > 0
                  ? <span className={styles.countdown}>剩余 {formatCountdown(countdown)}</span>
                  : <span className={styles.countdown} data-expired>等待服务端到期回收…</span>
              )}
            </p>
          </div>
        </header>
        <ul className={styles.items}>
          {approval.items.map((item, index) => {
            const summary = summarizeApprovalItem(item)
            const metadata = approvalItemMetadata(item)
            return (
            <li key={item.item_id} className={styles.item}>
              <fieldset>
                <legend>{item.display_name} · {item.action_label}</legend>
                {summary.kind === 'shell' && (
                  <pre className={styles.command}>$ {summary.command}</pre>
                )}
                {summary.kind === 'file' && (
                  <div className={styles.file}>
                    <code className={styles.path}>{summary.path}</code>
                  </div>
                )}
                {summary.kind === 'generic' && (
                  <div className={styles.location}>{item.location}</div>
                )}
                <details className={styles.meta}>
                  <summary>参数详情</summary>
                  <pre>{JSON.stringify(metadata, null, 2)}</pre>
                </details>
                <div className={styles.choices} role="radiogroup" aria-label={`${item.display_name}${item.action_label}`}>
                  {CHOICES.map(choice => (
                    <label
                      key={choice.value}
                      className={styles.choice}
                      title={choice.hint}
                    >
                      <input
                        type="radio"
                        name={`approval-item-${index}`}
                        checked={
                          decisions[item.item_id] === choice.value
                          || (choice.value === 'allow_always' && alwaysArmed === item.item_id)
                        }
                        disabled={locked}
                        onChange={() => { choose(item.item_id, choice.value) }}
                      />
                      {choice.label}
                    </label>
                  ))}
                </div>
                {alwaysArmed === item.item_id && (
                  <div className={styles.alwaysConfirm}>
                    <span>将长期记住「{item.display_name}」的「{item.action_label}」权限？</span>
                    <button type="button" className={styles.confirmAlways} onClick={() => { confirmAlways(item.item_id) }}>
                      确认始终允许
                    </button>
                    <button type="button" onClick={() => { setAlwaysArmed(null) }}>取消</button>
                  </div>
                )}
              </fieldset>
            </li>
            )
          })}
        </ul>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <footer>
          <button ref={closeRef} type="button" disabled={locked} onClick={close}>关闭</button>
          <button
            className={styles.approve}
            type="button"
            disabled={locked || !canSubmit}
            onClick={submit}
          >
            {locked ? '提交中…' : '提交决定'}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  )
}
