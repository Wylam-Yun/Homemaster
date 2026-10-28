import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { DreamingSessionDetail, DreamingSessionRow } from '../api/http'
import { ActionDiffCard } from './ActionDiffCard'
import { Badge, type BadgeVariant } from './ui/Badge'
import { Button } from './ui/Button'
import { PageHeader } from './ui/PageHeader'
import styles from './DreamingPage.module.css'

const STATE_LABEL: Record<string, string> = {
  unarchived: '未归档',
  processing: '正在归档',
  completed: '已归档',
  failed: '失败',
}

const STATE_ORDER = ['processing', 'failed', 'completed', 'unarchived'] as const

function stateVariant(state: string): BadgeVariant {
  if (state === 'completed') return 'success'
  if (state === 'failed') return 'danger'
  if (state === 'processing') return 'warning'
  return 'muted'
}

function formatEpoch(epoch: number | null): string {
  if (epoch === null) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(epoch * 1000))
}

type Props = {
  loadSessions: () => Promise<{ sessions: DreamingSessionRow[] }>
  loadDetail: (sessionId: string) => Promise<DreamingSessionDetail>
  unavailable?: boolean
  pollMs?: number
}

export function DreamingPage({ loadSessions, loadDetail, unavailable = false, pollMs = 2000 }: Props) {
  const [rows, setRows] = useState<DreamingSessionRow[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<DreamingSessionDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [states, setStates] = useState<Set<string>>(new Set(STATE_ORDER))
  const generation = useRef(0)

  const refresh = useCallback(async () => {
    try {
      const result = await loadSessions()
      setRows(result.sessions)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [loadSessions])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const select = useCallback(
    (sessionId: string) => {
      setSelectedId(sessionId)
      setDetail(null)
      setDetailError(null)
      const generationId = ++generation.current
      const poll = async (): Promise<void> => {
        try {
          const next = await loadDetail(sessionId)
          if (generation.current !== generationId) return
          setDetail(next)
          if (next.archive_state === 'processing') {
            window.setTimeout(() => { void poll() }, pollMs)
          }
        } catch (err) {
          if (generation.current !== generationId) return
          setDetailError(err instanceof Error ? err.message : String(err))
        }
      }
      void poll()
    },
    [loadDetail, pollMs],
  )

  const visibleRows = useMemo(
    () => (rows ?? []).filter(row => states.has(row.archive_state)),
    [rows, states],
  )

  const toggleState = (state: string): void => {
    setStates(prev => {
      const next = new Set(prev)
      if (next.has(state)) next.delete(state)
      else next.add(state)
      return next
    })
  }

  return (
    <section className={styles.page} aria-labelledby="dreaming-page-title">
      <PageHeader
        eyebrow="长期记忆"
        title="Dreaming"
        titleId="dreaming-page-title"
        subtitle="每个 session 的归档与 dreaming 产出：种子对话与记忆改动前后对照。"
        actions={<Button onClick={() => { void refresh() }} disabled={loading}>{loading ? '加载中…' : '刷新'}</Button>}
      />

      {error !== null && (
        <div className={styles.error} role="alert">
          <div><strong>会话列表读取失败</strong><span>{error}</span></div>
          <button type="button" onClick={() => { void refresh() }}>重试</button>
        </div>
      )}

      {unavailable ? (
        <div className={styles.unavailable}>
          <div>
            <strong>Dreaming 观测不可用</strong>
            <span>当前记忆后端未启用完整模式，无法读取归档与 dreaming 记录。</span>
          </div>
        </div>
      ) : (
        <div className={styles.layout}>
          <div className={styles.runList} role="list" aria-label="会话归档状态">
            <div className={styles.stateFilters}>
              {STATE_ORDER.map(state => (
                <label key={state} className={styles.listFilter}>
                  <input
                    type="checkbox"
                    checked={states.has(state)}
                    onChange={() => { toggleState(state) }}
                  />
                  {STATE_LABEL[state]}
                </label>
              ))}
            </div>
            {rows !== null && visibleRows.length === 0 && !loading && (
              <div className={styles.empty}>没有匹配状态的会话。</div>
            )}
            {visibleRows.map(row => (
              <button
                type="button"
                key={row.session_id}
                className={styles.runCard}
                data-active={row.session_id === selectedId || undefined}
                onClick={() => { select(row.session_id) }}
              >
                <span className={styles.runTop}>
                  <strong className={styles.sessionTitle}>{row.title || row.session_id}</strong>
                  {row.archive_count > 1 && <span className={styles.countChip}>×{row.archive_count}</span>}
                </span>
                <span className={styles.runMeta}>
                  <Badge variant={stateVariant(row.archive_state)}>
                    {STATE_LABEL[row.archive_state] ?? row.archive_state}
                  </Badge>
                  {row.last_archive_at !== null && <time>{formatEpoch(row.last_archive_at)}</time>}
                </span>
              </button>
            ))}
          </div>

          <div className={styles.detail}>
            {selectedId === null ? (
              <div className={styles.empty}>选择左侧一个会话查看 dreaming 详情。</div>
            ) : detailError !== null ? (
              <div className={styles.error} role="alert"><div><strong>详情读取失败</strong><span>{detailError}</span></div></div>
            ) : detail === null ? (
              <div className={styles.empty}>加载中…</div>
            ) : (
              <>
                <div className={styles.detailHeader}>
                  <Badge variant={stateVariant(detail.archive_state)}>
                    {STATE_LABEL[detail.archive_state] ?? detail.archive_state}
                  </Badge>
                  <span className={styles.detailMeta}>{detail.session_id}</span>
                </div>

                {detail.session_excerpts.length > 0 && (
                  <>
                    <h2 className={styles.sectionTitle}>会话节选</h2>
                    <div className={styles.excerpts}>
                      {detail.session_excerpts.map(excerpt => (
                        <div key={excerpt.turn_id} className={styles.excerpt}>
                          <p>{excerpt.preview ?? '（无对话节选）'}</p>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {detail.archives.length === 0 ? (
                  <div className={styles.empty}>该会话尚未归档——归档并 dreaming 完成后会出现在这里。</div>
                ) : (
                  detail.archives.map(archive => (
                    <div key={archive.archive_id} className={styles.archiveCard}>
                      <div className={styles.archiveHeader}>
                        <Badge variant={stateVariant(archive.state)}>
                          {STATE_LABEL[archive.state] ?? archive.state}
                        </Badge>
                        <span className={styles.detailMeta}>
                          turns {archive.turn_range[0]}–{archive.turn_range[1]} · {formatEpoch(archive.created_at)}
                        </span>
                        {archive.job !== null && (
                          <span className={styles.detailMeta}>
                            重试 {archive.job.attempts}
                            {archive.job.extracted_count !== null && <> · 产出 {archive.job.extracted_count} 条</>}
                          </span>
                        )}
                      </div>
                      {archive.job !== null && archive.job.error !== null && (
                        <div className={styles.error} role="alert"><div><strong>任务错误</strong><span>{archive.job.error}</span></div></div>
                      )}
                      {archive.job !== null && (archive.job.raw_verified === false || archive.job.missing_ids.length > 0) && (
                        <div className={styles.error} role="alert">
                          <div>
                            <strong>读回校验失败</strong>
                            <span>{archive.job.missing_ids.length > 0 ? `缺失 ${archive.job.missing_ids.length} 条记忆` : '原始读回未通过'}</span>
                          </div>
                        </div>
                      )}

                      {archive.seeds.length > 0 && (
                        <>
                          <h3 className={styles.subTitle}>种子（{archive.seeds.length}）</h3>
                          <div className={styles.excerpts}>
                            {archive.seeds.map(seed => (
                              <div key={seed.operation_id} className={styles.excerpt}>
                                {seed.missing ? (
                                  <p className={styles.missing}>种子任务已无法定位（{seed.operation_id}）。</p>
                                ) : (
                                  <p>{seed.preview ?? '（无对话节选）'}</p>
                                )}
                              </div>
                            ))}
                          </div>
                        </>
                      )}

                      <h3 className={styles.subTitle}>记忆改动</h3>
                      {archive.diffs_unavailable ? (
                        <div className={styles.empty}>记忆读回服务不可用，改动内容无法展示。</div>
                      ) : (archive.actions ?? []).length === 0 ? (
                        <div className={styles.empty}>本次 dreaming 没有产生记忆改动。</div>
                      ) : (
                        <div className={styles.groups}>
                          {(archive.actions ?? []).map(action => (
                            <ActionDiffCard
                              key={action.action_id}
                              action={action}
                              beforeLabel="改动前"
                              afterLabel="改动后"
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
