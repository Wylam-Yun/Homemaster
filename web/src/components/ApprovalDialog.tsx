/**
 * Adapted from Hermes Agent ConfirmDialog.tsx.
 * MIT License, Copyright (c) 2025 Nous Research.
 */

import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

import type { ApprovalState } from '../state/conversation'
import styles from './ApprovalDialog.module.css'

export function ApprovalDialog({
  approval,
  busy,
  onApprove,
  onReject,
}: {
  approval: ApprovalState
  busy: boolean
  onApprove: () => void
  onReject: () => void
}) {
  const rejectRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    rejectRef.current?.focus()
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && !busy) {
        event.preventDefault()
        onReject()
      }
    }
    document.addEventListener('keydown', onKey)
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      previous?.focus()
    }
  }, [busy, onReject])

  return createPortal(
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-labelledby="approval-title">
      <section className={styles.dialog}>
        <header>
          <span aria-hidden>⚠</span>
          <div>
            <h2 id="approval-title">Approve {approval.name}?</h2>
            <p>{approval.reason}</p>
          </div>
        </header>
        <dl><dt>Working directory</dt><dd>{approval.cwd || 'Not provided'}</dd></dl>
        <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
        <footer>
          <button ref={rejectRef} type="button" disabled={busy} onClick={onReject}>Reject</button>
          <button className={styles.approve} type="button" disabled={busy} onClick={onApprove}>{busy ? 'Waiting…' : 'Approve'}</button>
        </footer>
      </section>
    </div>,
    document.body,
  )
}
