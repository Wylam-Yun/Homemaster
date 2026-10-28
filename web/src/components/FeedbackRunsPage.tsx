import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { FeedbackRun, FeedbackRunDetail } from '../api/http'
import { Badge, type BadgeVariant } from './ui/Badge'
import { Button } from './ui/Button'
import { PageHeader } from './ui/PageHeader'
import styles from './FeedbackRunsPage.module.css'

/** Outbox states that still mean "in flight" — the detail poll keeps going
 * until the job settles to a terminal status. */
const TERMINAL = new Set(['completed', 'no_action', 'failed', 'stale'])

const STATUS_LABEL: Record<string, string> = {
  accepted: '已入队',
  processing: '整理中',
  deferred: '等待重试',
  outcome_unknown: '等待服务器',
  completed: '完成',
  no_action: '无更新',
  failed: '失败',
  stale: '已终止',
}

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
  loadRuns: () => Promise<{ runs: FeedbackRun[] }>
  loadDetail: (jobId: string) => Promise<FeedbackRunDetail>
  /** Sessions list for title lookup; unknown ids render the raw session id. */
  sessions?: { session_id: string; title: string }[]
  unavailable?: boolean
  pollMs?: number
}

function formatEpoch(epoch: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(epoch * 1000))
}

function formatDuration(start: number, end: number): string {
  const seconds = Math.max(0, Math.round(end - start))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m${seconds % 60}s`
}

export function FeedbackRunsPage({ loadRuns, loadDetail, sessions, unavailable = false, pollMs = 2000 }: Props) {
  const [runs, setRuns] = useState<FeedbackRun[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<FeedbackRunDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [expandedSessions, setExpandedSessions] = useState<Set<string>>(new Set())
  const generation = useRef(0)

  const titles = useMemo(() => {
    const map = new Map<string, string>()
    for (const session of sessions ?? []) {
      if (session.title.trim() !== '') map.set(session.session_id, session.title)
    }
    return map
  }, [sessions])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const result = await loadRuns()
      setError(null)
      setRuns(result.runs)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '整理记录读取失败')
    } finally {
      setLoading(false)
    }
  }, [loadRuns])

  useEffect(() => { void refresh() }, [refresh])

  const select = useCallback((jobId: string) => {
    setSelectedId(jobId)
    const generationId = ++generation.current
    setDetail(null)
    setDetailError(null)
    setExpandedSessions(new Set())
    void (async () => {
      for (;;) {
        try {
          const next = await loadDetail(jobId)
          if (generation.current !== generationId) return
          setDetail(next)
          if (TERMINAL.has(next.status)) return
        } catch (cause) {
          if (generation.current !== generationId) return
          setDetailError(cause instanceof Error ? cause.message : '详情读取失败')
          return
        }
        await new Promise(resolve => setTimeout(resolve, pollMs))
        if (generation.current !== generationId) return
      }
    })()
  }, [loadDetail, pollMs])

  useEffect(() => () => { generation.current += 1 }, [])

  const running = detail !== null && !TERMINAL.has(detail.status)

  return (
    <section className={styles.page} aria-labelledby="feedback-page-title">
      <PageHeader
        eyebrow="长期记忆"
        title="记忆整理"
        titleId="feedback-page-title"
        subtitle="隐性整理对最近对话的扫描记录与记忆改动前后对照。"
        actions={<Button onClick={() => { void refresh() }} disabled={loading}>{loading ? '加载中…' : '刷新'}</Button>}
      />

      {error !== null && (
        <div className={styles.error} role="alert">
          <div><strong>整理记录读取失败</strong><span>{error}</span></div>
          <button type="button" onClick={() => { void refresh() }}>重试</button>
        </div>
      )}

      {unavailable ? (
        <div className={styles.unavailable}>
          <div>
            <strong>记忆整理服务不可用</strong>
            <span>当前记忆后端未启用完整模式，无法读取整理记录。</span>
          </div>
        </div>
      ) : (
        <div className={styles.layout}>
          <div className={styles.runList} role="list" aria-label="整理记录">
            {runs !== null && runs.length === 0 && !loading && (
              <div className={styles.empty}>还没有整理记录。</div>
            )}
            {(runs ?? []).map(run => (
              <button
                type="button"
                key={run.job_id}
                className={styles.runCard}
                data-active={run.job_id === selectedId || undefined}
                onClick={() => { select(run.job_id) }}
              >
                <span className={styles.runTop}>
                  <Badge variant={run.trigger === 'manual' ? 'accent' : 'muted'}>
                    {run.trigger === 'manual' ? '手动' : '定时'}
                  </Badge>
                  <time>{formatEpoch(run.created_at)}</time>
                </span>
                <span className={styles.runMeta}>
                  <Badge variant={statusVariant(run.status)}>{STATUS_LABEL[run.status] ?? run.status}</Badge>
                  {run.actions_count > 0 && <span>{run.actions_count} 条改动</span>}
                  {TERMINAL.has(run.status) && <span>{formatDuration(run.created_at, run.updated_at)}</span>}
                </span>
              </button>
            ))}
          </div>

          <div className={styles.detail}>
            {selectedId === null ? (
              <div className={styles.empty}>选择左侧一次整理查看详情。</div>
            ) : detailError !== null ? (
              <div className={styles.error} role="alert"><div><strong>详情读取失败</strong><span>{detailError}</span></div></div>
            ) : detail === null ? (
              <div className={styles.empty}>加载中…</div>
            ) : (
              <>
                <div className={styles.detailHeader}>
                  <Badge variant={statusVariant(detail.status)}>
                    {STATUS_LABEL[detail.status] ?? detail.status}
                  </Badge>
                  <span className={styles.detailMeta}>
                    {formatEpoch(detail.created_at)}
                    {detail.window && (
                      <> · 窗口 {formatDateTime(detail.window.window_start)} → {formatDateTime(detail.window.window_end)}</>
                    )}
                    {TERMINAL.has(detail.status) && <> · 耗时 {formatDuration(detail.created_at, detail.updated_at)}</>}
                    {running && <> · 轮询中…</>}
                  </span>
                </div>
                {detail.error !== null && detail.error !== undefined && (
                  <div className={styles.error} role="alert"><div><strong>任务错误</strong><span>{detail.error}</span></div></div>
                )}

                <h2 className={styles.sectionTitle}>扫描的 Sessions（{detail.sessions.length}）</h2>
                <p className={styles.sectionHint}>本窗口内被消费的对话记录，按 session 聚合。</p>
                {detail.sessions.length === 0 ? (
                  <div className={styles.empty}>该窗口内没有被消费的对话记录。</div>
                ) : (
                  <div className={styles.groups}>
                    {detail.sessions.map(session => {
                      const expanded = expandedSessions.has(session.session_id)
                      return (
                        <div key={session.session_id} className={styles.group}>
                          <button
                            type="button"
                            className={styles.groupHeader}
                            aria-expanded={expanded}
                            onClick={() => {
                              setExpandedSessions(prev => {
                                const next = new Set(prev)
                                if (next.has(session.session_id)) next.delete(session.session_id)
                                else next.add(session.session_id)
                                return next
                              })
                            }}
                          >
                            <span className={styles.chevron} aria-hidden="true">›</span>
                            <span className={styles.groupTitle}>
                              <strong>{titles.get(session.session_id) ?? session.session_id}</strong>
                              <small>{session.session_id}</small>
                            </span>
                            <span className={styles.groupCount}>{session.records_count} 轮</span>
                          </button>
                          {expanded && (
                            <div className={styles.excerpts}>
                              {session.excerpts.map(excerpt => (
                                <div key={excerpt.add_record_id} className={styles.excerpt}>
                                  <time>{excerpt.submitted_at !== null ? formatDateTime(excerpt.submitted_at) : ''}</time>
                                  <p>{excerpt.preview !== '' ? excerpt.preview : '（无对话节选）'}</p>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}

                <h2 className={styles.sectionTitle}>记忆改动（{detail.actions.length}）</h2>
                {detail.actions.length === 0 ? (
                  <div className={styles.empty}>本次没有产生记忆改动。</div>
                ) : (
                  <div className={styles.groups}>
                    {detail.actions.map(action => (
                      <div key={action.action_id} className={styles.actionCard}>
                        <div className={styles.actionTop}>
                          <Badge variant={ACTION_VARIANT[action.action] ?? 'muted'}>
                            {ACTION_LABEL[action.action] ?? action.action}
                          </Badge>
                          {action.memory_id !== null && <code className={styles.memoryId}>{action.memory_id.slice(0, 8)}…</code>}
                          {action.memory_session_id !== null && (
                            <span className={styles.detailMeta}>记忆来自 {titles.get(action.memory_session_id) ?? action.memory_session_id}</span>
                          )}
                        </div>
                        {action.missing ? (
                          <p className={styles.missingText}>该记忆已无法在存储中读取。</p>
                        ) : (
                          <div className={styles.diff}>
                            <div className={styles.diffSide}>
                              <span className={styles.diffLabel}>整理前</span>
                              <p>{action.before?.content ?? (action.action === 'add' ? '（新记忆）' : '（无旧版本）')}</p>
                              {action.before !== null && action.before !== undefined && (
                                <span className={styles.diffStatus}>{action.before.status}</span>
                              )}
                            </div>
                            <div className={styles.diffArrow} aria-hidden="true">→</div>
                            <div className={styles.diffSide}>
                              <span className={styles.diffLabel}>整理后</span>
                              <p>{action.after?.content ?? '—'}</p>
                              {action.after !== null && action.after !== undefined && (
                                <span className={styles.diffStatus}>{action.after.status}</span>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

function statusVariant(status: string): BadgeVariant {
  if (status === 'completed') return 'success'
  if (status === 'failed' || status === 'stale') return 'danger'
  if (status === 'no_action') return 'muted'
  return 'warning'
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date)
}
