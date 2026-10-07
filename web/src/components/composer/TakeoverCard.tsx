import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'

import type { PendingQuestionState } from '../../state/conversation'
import styles from './TakeoverCard.module.css'

/**
 * Composer 接管卡：question.asked 到达时输入区换成这张问答专用卡，提交后走
 * POST /{id}/questions/{qid}/answer，原草稿挂起在父组件 state 里自动还原。
 */
export function QuestionCard({
  question,
  disabled = false,
  onSubmit,
}: {
  question: PendingQuestionState
  disabled?: boolean
  onSubmit: (questionId: string, text: string) => Promise<boolean> | boolean
}) {
  const [answer, setAnswer] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const composingRef = useRef(false)

  // A new question replaces any half-typed answer to the previous one.
  useEffect(() => {
    setAnswer('')
    setSubmitting(false)
    inputRef.current?.focus()
  }, [question.questionId])

  const submit = (): void => {
    const text = answer.trim()
    if (disabled || submitting || text.length === 0) return
    setSubmitting(true)
    void Promise.resolve(onSubmit(question.questionId, text)).then(
      accepted => { if (!accepted) setSubmitting(false) },
      () => { setSubmitting(false) },
    )
  }

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>): void => {
    if (composingRef.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (event.repeat) return
      submit()
    }
  }

  const canSubmit = !disabled && !submitting && answer.trim().length > 0

  return (
    <div className={styles.takeover} data-takeover="question">
      <div className={styles.takeoverLabel}>Agent 提问</div>
      <p className={styles.questionText}>{question.question}</p>
      <div className={styles.answerRow}>
        <input
          ref={inputRef}
          className={styles.answerInput}
          type="text"
          value={answer}
          disabled={disabled || submitting}
          placeholder="输入回答…"
          aria-label="回答 Agent 的问题"
          onChange={event => { setAnswer(event.target.value) }}
          onKeyDown={onKeyDown}
          onCompositionStart={() => { composingRef.current = true }}
          onCompositionEnd={() => { composingRef.current = false }}
        />
        <button
          type="button"
          className={styles.answerSubmit}
          disabled={!canSubmit}
          onClick={submit}
        >
          {submitting ? '提交中…' : '回答'}
        </button>
      </div>
      <small className={styles.takeoverHint}>Enter 提交 · 回答后恢复原草稿</small>
    </div>
  )
}

/**
 * 审批待决时同一接管位的只读提示卡——审批决策本身仍在转录里的审批卡
 * （ApprovalDialog）中完成，这里只解释输入区为何被接管。
 */
export function ApprovalWaitingCard({ itemCount }: { itemCount: number }) {
  return (
    <div className={styles.takeover} data-takeover="approval" role="status">
      <div className={styles.takeoverLabel}>等待审批</div>
      <p className={styles.questionText}>
        {itemCount > 0 ? `${itemCount} 项申请待决定` : '一项申请待决定'}——请在审批卡中逐项选择。
      </p>
      <small className={styles.takeoverHint}>审批期间消息输入暂停，草稿已保留</small>
    </div>
  )
}
