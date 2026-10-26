import { useEffect, useRef, useState } from 'react'

import type { ManagedMemory, MemoryHistory } from '../api/http'
import styles from './MemoryDetailDialog.module.css'
import {
  archiveReasonLabel,
  classificationLabel,
  compileStateLabel,
  failureReasonLabel,
  formatFullDate,
  humanizeGoal,
  shortEpisode,
  subtaskLabel,
  summarizeMemory,
  typeLabel,
} from './memorySummary'


type Props = {
  memory: ManagedMemory
  loadHistory: (memoryId: string) => Promise<MemoryHistory>
  onClose: () => void
  compileMemory?: (memoryId: string) => Promise<{ job_id: string; status: string }>
  compileStatus?: (jobId: string) => Promise<{ status: string; error?: string }>
  onUpdate?: (memoryId: string, content: string) => Promise<void>
  onDelete?: (memoryId: string) => Promise<void>
}

export function MemoryDetailDialog({
  memory,
  loadHistory,
  compileMemory,
  compileStatus,
  onUpdate,
  onDelete,
  onClose,
}: Props) {
  const [history, setHistory] = useState<ManagedMemory[] | null>(null)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [compileState, setCompileState] = useState<string | null>(memory.compile_status ?? null)
  const [compileError, setCompileError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [editDraft, setEditDraft] = useState(memory.content)
  const [saving, setSaving] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    setEditing(false)
    setEditDraft(memory.content)
    setConfirmDelete(false)
    setActionError(null)
  }, [memory])

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', closeOnEscape, true)
    return () => {
      window.removeEventListener('keydown', closeOnEscape, true)
      document.body.style.overflow = overflow
      previous?.focus()
    }
  }, [onClose])

  useEffect(() => {
    let current = true
    setHistory(null)
    setHistoryError(null)
    if (!memory.has_history) {
      setHistory([])
      return () => { current = false }
    }
    void loadHistory(memory.memory_id)
      .then(result => { if (current) setHistory(result.versions) })
      .catch(error => {
        if (current) setHistoryError(error instanceof Error ? error.message : '版本历史加载失败')
      })
    return () => { current = false }
  }, [loadHistory, memory])

  const canCompile = memory.domain === 'alfworld' && memory.source_trajectory_id == null && memory.memory_type === 'trajectory'
  const startCompile = async () => {
    if (!compileMemory) return
    setCompileError(null)
    setCompileState('queued')
    try {
      const job = await compileMemory(memory.memory_id)
      setCompileState(job.status)
      if (compileStatus) {
        // Poll on the freshly fetched status (not the enqueue-time one) for up
        // to ~60s; a stalled loop previously left the UI stuck on 编译中.
        let latest = job.status
        let failure: string | null = null
        for (let attempt = 0; attempt < 120 && latest !== 'succeeded' && latest !== 'failed'; attempt += 1) {
          await new Promise(resolve => window.setTimeout(resolve, 500))
          const status = await compileStatus(job.job_id)
          latest = status.status
          setCompileState(latest)
          if (latest === 'failed') failure = status.error ?? '编译失败'
        }
        if (failure !== null) setCompileError(failure)
        else if (latest !== 'succeeded') setCompileError('编译仍在后台进行，稍后可在历史版本中查看结果。')
      }
    } catch (error) {
      setCompileState('failed')
      setCompileError(error instanceof Error ? error.message : '编译任务提交失败')
    }
  }

  const handleSaveEdit = async () => {
    if (!onUpdate) return
    const trimmed = editDraft.trim()
    if (!trimmed) {
      setActionError('记忆内容不能为空。')
      return
    }
    setSaving(true)
    setActionError(null)
    try {
      await onUpdate(memory.memory_id, trimmed)
      setEditing(false)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '更新记忆失败')
    } finally {
      setSaving(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (!onDelete) return
    setDeleting(true)
    setActionError(null)
    try {
      await onDelete(memory.memory_id)
      onClose()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '删除记忆失败')
      setDeleting(false)
    }
  }

  const summary = summarizeMemory(memory)
  const record = memory.record
  const classification = memory.classification ?? recordString(record, 'classification')
  const goalSource = memory.goal_type ?? recordString(record, 'goal_type')
  const goalText = humanizeGoal(goalSource)
  const episodeText = shortEpisode(memory.episode_id ?? recordString(record, 'episode_id'))
  const failureReason = recordString(record, 'failure_reason')
  const stepIndex = recordStepIndex(record)
  const showOutcome = memory.outcome === 'success' || memory.outcome === 'failure'
  const isParsedJson = summary.rawJson !== undefined && summary.rawJson !== null
  const canMutate = memory.status === 'active' && (onUpdate !== undefined || onDelete !== undefined)

  return (
    <div className={styles.backdrop} onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <section className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="memory-detail-title">
        <header>
          <div><p>记忆详情</p><h2 id="memory-detail-title">记忆详情</h2></div>
          <button ref={closeRef} type="button" aria-label="关闭记忆详情" onClick={onClose}>×</button>
        </header>
        <div className={styles.body}>
          <div className={styles.statusLine}>
            <span data-status={memory.status}>{memory.status === 'active' ? '生效中' : '历史版本'}</span>
            <span>{typeLabel(memory.memory_type)}</span>
            {showOutcome && <span>{memory.outcome === 'success' ? '成功' : '失败'}</span>}
            {memory.is_executable === true && <span>可执行经验</span>}
            {canMutate && (
              <div className={styles.headerActions}>
                {onUpdate !== undefined && memory.structure_status === 'plain' && !editing && (
                  <button
                    type="button"
                    className={styles.editButton}
                    onClick={() => { setEditing(true); setConfirmDelete(false); setEditDraft(memory.content) }}
                  >
                    编辑
                  </button>
                )}
                {onDelete !== undefined && (
                  <button
                    type="button"
                    className={styles.deleteButton}
                    onClick={() => { setConfirmDelete(current => !current); setEditing(false) }}
                  >
                    删除
                  </button>
                )}
              </div>
            )}
          </div>

          {actionError !== null && <p className={styles.warning} role="alert">{actionError}</p>}

          {confirmDelete && (
            <div className={styles.confirmStrip} role="alert">
              <span>确认删除（归档）这条记忆？删除后将不再被对话自动检索。</span>
              <button type="button" className={styles.dangerBtn} disabled={deleting} onClick={() => { void handleConfirmDelete() }}>
                {deleting ? '删除中…' : '确认删除'}
              </button>
              <button type="button" onClick={() => { setConfirmDelete(false) }}>取消</button>
            </div>
          )}

          {editing ? (
            <div className={styles.editBox}>
              <label>
                <span>编辑记忆内容</span>
                <textarea
                  rows={5}
                  value={editDraft}
                  aria-label="编辑记忆内容"
                  onChange={event => { setEditDraft(event.target.value) }}
                />
              </label>
              <div className={styles.editActions}>
                <button type="button" className={styles.saveBtn} disabled={saving} onClick={() => { void handleSaveEdit() }}>
                  {saving ? '保存中…' : '保存修改'}
                </button>
                <button type="button" onClick={() => { setEditing(false); setEditDraft(memory.content) }}>取消</button>
              </div>
            </div>
          ) : (
            <p className={styles.summaryLead}><span aria-hidden="true">{summary.icon} </span>{summary.title}</p>
          )}

          {canCompile && compileMemory && <button type="button" className={styles.compileButton} onClick={() => { void startCompile() }} disabled={compileState === 'queued' || compileState === 'running'}>编译为经验</button>}
          {compileState !== null && <p>编译状态：{compileStateLabel(compileState)}{compileError !== null ? `，${compileError}` : ''}</p>}

          {!editing && memory.structure_status !== 'valid' && !isParsedJson && (
            <p className={styles.memoryContent}>{memory.content}</p>
          )}

          {summary.fields !== undefined && summary.fields.length > 0 && (
            <dl className={styles.metadata}>
              {summary.fields.map(field => (
                <Meta key={field.label} label={field.label} value={field.value} />
              ))}
            </dl>
          )}

          <dl className={styles.metadata}>
            <Meta label="记忆 ID" value={memory.memory_id} />
            <Meta label="来源会话" value={memory.session_id ?? '未关联会话'} />
            <Meta label="创建时间" value={formatFullDate(memory.created_at)} />
            <Meta label="更新时间" value={formatFullDate(memory.updated_at)} />
            {memory.archived_at !== null && <Meta label="归档时间" value={formatFullDate(memory.archived_at)} />}
            {memory.archive_reason !== null && <Meta label="归档原因" value={archiveReasonLabel(memory.archive_reason)} />}
          </dl>

          <section className={styles.section}>
            <h3>结构化信息</h3>
            {memory.structure_status === 'valid' && memory.record !== null && (
              <>
                <dl className={styles.metadata}>
                  <Meta label="任务" value={goalText ?? '未知'} />
                  {classification !== null && <Meta label="结果" value={classificationLabel(classification)} />}
                  {failureReason !== null && classification !== 'agent_success' && (
                    <Meta label="失败原因" value={failureReasonLabel(failureReason)} />
                  )}
                  {episodeText !== null && <Meta label="Episode" value={episodeText} />}
                  {memory.taskset_id !== null && memory.taskset_id !== undefined && (
                    <Meta label="任务集" value={subtaskLabel(memory.taskset_id, memory.subtask_index)} />
                  )}
                  {stepIndex !== null && <Meta label="环境步数" value={String(stepIndex)} />}
                </dl>
                <details className={styles.rawFold}>
                  <summary>原始 JSON（调试用）</summary>
                  <pre>{JSON.stringify(memory.record, null, 2)}</pre>
                </details>
              </>
            )}
            {memory.structure_status !== 'valid' && isParsedJson && (
              <details className={styles.rawFold}>
                <summary>原始 JSON（调试用）</summary>
                <pre>{JSON.stringify(summary.rawJson, null, 2)}</pre>
              </details>
            )}
            {memory.structure_status === 'invalid' && <p className={styles.warning}>结构信息异常，已隐藏损坏的原始数据。</p>}
            {memory.structure_status === 'plain' && !isParsedJson && <p className={styles.muted}>这条记忆没有额外的机器结构化字段。</p>}
          </section>

          <section className={styles.section}>
            <h3>版本历史</h3>
            {historyError !== null && <p className={styles.warning}>{historyError}</p>}
            {history === null && historyError === null && <p className={styles.muted}>正在读取版本…</p>}
            {history !== null && history.length === 0 && <p className={styles.muted}>没有更早的版本。</p>}
            {history !== null && history.length > 0 && (
              <ol className={styles.timeline}>
                {history.map((version, index) => (
                  <li key={`${version.memory_id}:${index}`}>
                    <span /><div><strong>{version.status === 'active' ? '生效版本' : '归档版本'}</strong><time>{formatFullDate(version.updated_at ?? version.created_at)}</time><p>{summarizeMemory(version).title}</p></div>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      </section>
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>
}

function recordString(record: Record<string, unknown> | null, key: string): string | null {
  if (record === null) return null
  const value = record[key]
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function recordStepIndex(record: Record<string, unknown> | null): number | null {
  if (record === null) return null
  const state = record['final_environment_state']
  if (typeof state !== 'object' || state === null) return null
  const stepIndex = (state as Record<string, unknown>)['step_index']
  return typeof stepIndex === 'number' ? stepIndex : null
}


