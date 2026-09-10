import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import type { ItemChoice } from '../api/http'
import type { ApprovalState } from '../state/conversation'
import styles from './ApprovalDialog.module.css'

export type ApprovalDecisions = Record<string, ItemChoice>

const CHOICES: ReadonlyArray<{ value: ItemChoice; label: string }> = [
  { value: 'allow_once', label: '本次允许' },
  { value: 'allow_always', label: '始终允许' },
  { value: 'reject', label: '拒绝' },
]

function newSubmissionId(): string {
  return crypto.randomUUID()
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
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    setDecisions({})
    setSubmissionId(newSubmissionId())
  }, [approval.approvalId])

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
      if (event.key === 'Escape' && !busy) {
        event.preventDefault()
        onClose(submissionId)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
    }
  }, [busy, onClose, submissionId])

  const canSubmit = approval.items.length > 0
    && approval.items.every(item => decisions[item.item_id] !== undefined)

  const choose = (itemId: string, choice: ItemChoice): void => {
    if (busy) return
    setDecisions(previous => ({ ...previous, [itemId]: choice }))
    setSubmissionId(newSubmissionId())
  }

  return createPortal(
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-labelledby="approval-title">
      <section className={styles.dialog}>
        <header>
          <span aria-hidden>⚠</span>
          <div>
            <h2 id="approval-title">权限申请</h2>
            <p>{approval.intentSummary}</p>
            <p className={styles.hint}>仅针对本次调用；逐项决定后提交。</p>
          </div>
        </header>
        <ul className={styles.items}>
          {approval.items.map((item, index) => (
            <li key={item.item_id} className={styles.item}>
              <fieldset>
                <legend>{item.display_name} · {item.action_label}</legend>
                <div className={styles.location}>{item.location}</div>
                <div className={styles.choices} role="radiogroup" aria-label={`${item.display_name}${item.action_label}`}>
                  {CHOICES.map(choice => (
                    <label key={choice.value} className={styles.choice}>
                      <input
                        type="radio"
                        name={`approval-item-${index}`}
                        checked={decisions[item.item_id] === choice.value}
                        disabled={busy}
                        onChange={() => { choose(item.item_id, choice.value) }}
                      />
                      {choice.label}
                    </label>
                  ))}
                </div>
              </fieldset>
            </li>
          ))}
        </ul>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <footer>
          <button ref={closeRef} type="button" disabled={busy} onClick={() => { onClose(submissionId) }}>关闭</button>
          <button
            className={styles.approve}
            type="button"
            disabled={busy || !canSubmit}
            onClick={() => { onSubmit(decisions, submissionId) }}
          >
            {busy ? '提交中…' : '提交决定'}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  )
}
