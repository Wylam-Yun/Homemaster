import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import type { ItemChoice } from '../api/http'
import type { ApprovalItem } from '../protocol/events'
import type { ApprovalState } from '../state/conversation'
import styles from './ApprovalDialog.module.css'

export type ApprovalDecisions = Record<string, ItemChoice>

// once/deny are the only wired choices this batch; always stays as a disabled
// placeholder until the server protocol supports persistent grants here.
const CHOICES: ReadonlyArray<{ value: ItemChoice; label: string; disabled?: boolean; hint?: string }> = [
  { value: 'allow_once', label: '本次允许' },
  { value: 'allow_always', label: '始终允许', disabled: true, hint: 'requires server support' },
  { value: 'reject', label: '拒绝' },
]

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
  const sawBusyRef = useRef(false)
  const closeRef = useRef<HTMLButtonElement>(null)

  const locked = busy || latched

  useEffect(() => {
    setDecisions({})
    setSubmissionId(newSubmissionId())
    setLatched(false)
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
    setDecisions(previous => ({ ...previous, [itemId]: choice }))
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
            <p className={styles.hint}>仅针对本次调用；逐项决定后提交。</p>
          </div>
        </header>
        <ul className={styles.items}>
          {approval.items.map((item, index) => {
            const summary = summarizeApprovalItem(item)
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
                    <details className={styles.more}>
                      <summary>更多参数</summary>
                      <pre>{JSON.stringify(summary.rest, null, 2)}</pre>
                    </details>
                  </div>
                )}
                {summary.kind === 'generic' && (
                  <div className={styles.location}>{item.location}</div>
                )}
                <div className={styles.choices} role="radiogroup" aria-label={`${item.display_name}${item.action_label}`}>
                  {CHOICES.map(choice => (
                    <label
                      key={choice.value}
                      className={styles.choice}
                      data-disabled={choice.disabled || undefined}
                      title={choice.hint}
                    >
                      <input
                        type="radio"
                        name={`approval-item-${index}`}
                        checked={decisions[item.item_id] === choice.value}
                        disabled={locked || choice.disabled}
                        onChange={() => { choose(item.item_id, choice.value) }}
                      />
                      {choice.label}
                    </label>
                  ))}
                </div>
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
