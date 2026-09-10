import { useEffect, useState } from 'react'

import type { ManagedMemory, MemoryHistory } from '../api/http'
import styles from './MemoryDetailDialog.module.css'


type Props = {
  memory: ManagedMemory
  loadHistory: (memoryId: string) => Promise<MemoryHistory>
  onClose: () => void
  compileMemory?: (memoryId: string) => Promise<{ job_id: string; status: string }>
  compileStatus?: (jobId: string) => Promise<{ status: string; error?: string }>
}

export function MemoryDetailDialog({ memory, loadHistory, compileMemory, compileStatus, onClose }: Props) {
  const [history, setHistory] = useState<ManagedMemory[] | null>(null)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [compileState, setCompileState] = useState<string | null>(memory.compile_status ?? null)
  const [compileError, setCompileError] = useState<string | null>(null)

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', closeOnEscape)
    return () => { window.removeEventListener('keydown', closeOnEscape) }
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
        for (let attempt = 0; attempt < 20; attempt += 1) {
          if (job.status === 'succeeded' || job.status === 'failed') break
          await new Promise(resolve => window.setTimeout(resolve, 250))
          const status = await compileStatus(job.job_id)
          setCompileState(status.status)
          if (status.status === 'failed') { setCompileError(status.error ?? '编译失败'); break }
          if (status.status === 'succeeded') break
        }
      }
    } catch (error) {
      setCompileState('failed')
      setCompileError(error instanceof Error ? error.message : '编译任务提交失败')
    }
  }

  return (
    <div className={styles.backdrop} onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <section className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="memory-detail-title">
        <header>
          <div><p>只读详情</p><h2 id="memory-detail-title">记忆详情</h2></div>
          <button type="button" aria-label="关闭记忆详情" onClick={onClose}>×</button>
        </header>
        <div className={styles.body}>
          <div className={styles.statusLine}>
            <span data-status={memory.status}>{memory.status === 'active' ? '生效中' : '已归档'}</span>
            <span>{memory.memory_type_label}</span>
            {memory.outcome !== null && memory.outcome !== undefined && <span>{outcomeLabel(memory.outcome)}</span>}
            {memory.is_executable === true && <span>可执行</span>}
          </div>
          {memory.domain === 'alfworld' && <p>任务：{memory.goal_type ?? '未知'} · {memory.taskset_id ? `${memory.taskset_id} / 子任务 ${Number(memory.subtask_index ?? 0) + 1}` : (memory.episode_id ?? '未知 episode')}</p>}
          {canCompile && compileMemory && <button type="button" className={styles.compileButton} onClick={() => { void startCompile() }} disabled={compileState === 'queued' || compileState === 'running'}>编译为经验</button>}
          {compileState !== null && <p>编译状态：{compileState}{compileError !== null ? `，${compileError}` : ''}</p>}
          <p className={styles.memoryContent}>{memory.content}</p>
          <dl className={styles.metadata}>
            <Meta label="记忆 ID" value={memory.memory_id} />
            <Meta label="来源会话" value={memory.session_id ?? '未关联会话'} />
            <Meta label="创建时间" value={formatFullDate(memory.created_at)} />
            <Meta label="更新时间" value={formatFullDate(memory.updated_at)} />
            {memory.archived_at !== null && <Meta label="归档时间" value={formatFullDate(memory.archived_at)} />}
            {memory.archive_reason !== null && <Meta label="归档原因" value={reasonLabel(memory.archive_reason)} />}
          </dl>

          <section className={styles.section}>
            <h3>结构化信息</h3>
            {memory.structure_status === 'valid' && memory.record !== null && <pre>{JSON.stringify(memory.record, null, 2)}</pre>}
            {memory.structure_status === 'invalid' && <p className={styles.warning}>结构信息异常，已隐藏损坏的原始数据。</p>}
            {memory.structure_status === 'plain' && <p className={styles.muted}>这条记忆没有结构化信息。</p>}
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
                    <span /><div><strong>{version.status === 'active' ? '生效版本' : '归档版本'}</strong><time>{formatFullDate(version.updated_at ?? version.created_at)}</time><p>{version.content}</p></div>
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

function formatFullDate(value: string | null): string {
  if (value === null) return '未知'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return '未知'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium' }).format(date)
}

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    user_request: '用户请求',
    direct_structured_update: '结构化更新替换',
  }
  return labels[reason] ?? reason
}

function outcomeLabel(outcome: string): string {
  return ({ success: '成功', failure: '失败', unknown: '未知' } as Record<string, string>)[outcome] ?? outcome
}
