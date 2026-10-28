import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import type {
  SkillEvolutionDetail,
  SkillEvolutionRow,
  SkillVersionContent,
} from '../api/http'
import { diffLines } from '../utils/lineDiff'
import { Badge, type BadgeVariant } from './ui/Badge'
import { Button } from './ui/Button'
import { PageHeader } from './ui/PageHeader'
import styles from './SkillEvolutionPage.module.css'

const SUCCESS_STATES = new Set([
  'completed', 'no_action', 'success', 'sealed', 'consumed', 'observed', 'active',
])
const DANGER_STATES = new Set(['failed', 'error', 'outcome_unknown', 'canceled'])
const WARNING_STATES = new Set([
  'draft', 'pending', 'accepted', 'processing', 'running', 'open', 'created', 'missing_result',
])

function statusVariant(status: string | null): BadgeVariant {
  if (status !== null && SUCCESS_STATES.has(status)) return 'success'
  if (status !== null && DANGER_STATES.has(status)) return 'danger'
  if (status !== null && WARNING_STATES.has(status)) return 'warning'
  return 'muted'
}

function formatEpoch(epoch: number | null): string {
  if (epoch === null) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(epoch * 1000))
}

type Props = {
  loadOverview: () => Promise<{ skills: SkillEvolutionRow[] }>
  loadDetail: (cloudSkillId: string) => Promise<SkillEvolutionDetail>
  loadContent: (cloudSkillId: string, versionId: string) => Promise<SkillVersionContent>
  unavailable?: boolean
}

export function SkillEvolutionPage({ loadOverview, loadDetail, loadContent, unavailable = false }: Props) {
  const [rows, setRows] = useState<SkillEvolutionRow[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SkillEvolutionDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [openInvocation, setOpenInvocation] = useState<string | null>(null)
  const [content, setContent] = useState<SkillVersionContent | null>(null)
  const [contentFor, setContentFor] = useState<string | null>(null)
  const [contentError, setContentError] = useState<string | null>(null)
  const generation = useRef(0)

  const refresh = useCallback(async () => {
    try {
      const result = await loadOverview()
      setRows(result.skills)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [loadOverview])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const select = useCallback(
    (cloudSkillId: string) => {
      setSelectedId(cloudSkillId)
      setDetail(null)
      setDetailError(null)
      setContent(null)
      setContentFor(null)
      setContentError(null)
      setOpenInvocation(null)
      const generationId = ++generation.current
      void (async () => {
        try {
          const next = await loadDetail(cloudSkillId)
          if (generation.current !== generationId) return
          setDetail(next)
        } catch (err) {
          if (generation.current !== generationId) return
          setDetailError(err instanceof Error ? err.message : String(err))
        }
      })()
    },
    [loadDetail],
  )

  const showContent = useCallback(
    (key: string, versionId: string) => {
      if (selectedId === null) return
      if (contentFor === key) {
        setContentFor(null)
        setContent(null)
        return
      }
      setContentFor(key)
      setContent(null)
      setContentError(null)
      void (async () => {
        try {
          const next = await loadContent(selectedId, versionId)
          setContent(next)
        } catch (err) {
          setContentError(err instanceof Error ? err.message : String(err))
        }
      })()
    },
    [contentFor, loadContent, selectedId],
  )

  const diff = content !== null && content.parent !== null
    ? diffLines(content.parent.content, content.content)
    : null

  const renderDiffPanel = (): ReactNode => (
    <div className={styles.diffPanel}>
      {contentError !== null ? (
        <div className={styles.error} role="alert"><div><strong>草稿读取失败</strong><span>{contentError}</span></div></div>
      ) : content === null ? (
        <div className={styles.empty}>加载草稿…</div>
      ) : (
        <>
          <div className={styles.diffMeta}>
            <Badge variant={statusVariant(content.status)}>{content.status ?? 'draft'}</Badge>
            <span>候选版本 {content.version_id}（只读，不会自动激活）</span>
          </div>
          {content.parent === null ? (
            <div className={styles.empty}>无父版本可对照——草稿全文如下。</div>
          ) : null}
          {diff === null ? (
            <pre className={styles.diffText}>{content.content}</pre>
          ) : (
            <pre className={styles.diffText}>
              {diff.map((line, index) => (
                <span key={index} className={styles[`diff_${line.kind}`]}>
                  {line.kind === 'added' ? '+ ' : line.kind === 'removed' ? '- ' : '  '}
                  {line.text}{'\n'}
                </span>
              ))}
            </pre>
          )}
        </>
      )}
    </div>
  )

  return (
    <section className={styles.page} aria-labelledby="skill-evolution-title">
      <PageHeader
        eyebrow="技能"
        title="Skill Evolution"
        titleId="skill-evolution-title"
        subtitle="每个技能的调用轨迹、进化批次与候选草稿的内容 diff。草稿为只读候选，不会自动激活。"
        actions={<Button onClick={() => { void refresh() }} disabled={loading}>{loading ? '加载中…' : '刷新'}</Button>}
      />

      {error !== null && (
        <div className={styles.error} role="alert">
          <div><strong>技能列表读取失败</strong><span>{error}</span></div>
          <button type="button" onClick={() => { void refresh() }}>重试</button>
        </div>
      )}

      {unavailable ? (
        <div className={styles.unavailable}>
          <div>
            <strong>Skill Evolution 观测不可用</strong>
            <span>当前记忆后端未启用完整模式，无法读取调用轨迹与进化批次。</span>
          </div>
        </div>
      ) : (
        <div className={styles.layout}>
          <div className={styles.skillList} role="list" aria-label="可进化技能">
            {rows !== null && rows.length === 0 && !loading && (
              <div className={styles.empty}>还没有产生云端调用轨迹的技能。</div>
            )}
            {(rows ?? []).map(row => (
              <button
                type="button"
                key={row.cloud_skill_id}
                className={styles.skillCard}
                data-active={row.cloud_skill_id === selectedId || undefined}
                onClick={() => { select(row.cloud_skill_id) }}
              >
                <span className={styles.skillTop}>
                  <strong className={styles.skillName}>{row.skill_id ?? row.cloud_skill_id}</strong>
                  {row.evolution_enabled && <Badge variant="success">进化：开</Badge>}
                  {row.last_draft_version_id !== null && <Badge variant="warning">draft</Badge>}
                </span>
                <span className={styles.skillMeta}>
                  <span>{row.unconsumed_count ?? 0}/{row.batch_size} 轨迹</span>
                  <span>{row.batches_count} 批次</span>
                  {row.last_batch_at !== null && <time>{formatEpoch(row.last_batch_at)}</time>}
                </span>
              </button>
            ))}
          </div>

          <div className={styles.detail}>
            {selectedId === null ? (
              <div className={styles.empty}>选择左侧一个技能查看进化详情。</div>
            ) : detailError !== null ? (
              <div className={styles.error} role="alert"><div><strong>详情读取失败</strong><span>{detailError}</span></div></div>
            ) : detail === null ? (
              <div className={styles.empty}>加载中…</div>
            ) : (
              <>
                <div className={styles.detailHeader}>
                  <strong>{detail.skill_id ?? detail.cloud_skill_id}</strong>
                  <span className={styles.detailMeta}>{detail.cloud_skill_id}</span>
                  {detail.evolution_enabled && <Badge variant="success">进化：开</Badge>}
                  <Badge variant="muted">{detail.unconsumed_count ?? 0}/{detail.batch_size} 待消费</Badge>
                </div>

                <h2 className={styles.sectionTitle}>进化批次</h2>
                {detail.batches.length === 0 ? (
                  <div className={styles.empty}>尚未触发进化——攒满 8 条 sealed 轨迹后产生批次。</div>
                ) : (
                  <div className={styles.batchList}>
                    {detail.batches.map(batch => (
                      <div key={batch.batch_id} className={styles.batchCard}>
                        <div className={styles.batchHeader}>
                          <Badge variant={statusVariant(batch.status)}>{batch.status}</Badge>
                          <span className={styles.detailMeta}>
                            {batch.trace_ids.length} 条轨迹 · {formatEpoch(batch.created_at)}
                          </span>
                          {batch.draft_version_id !== null && (
                            <button
                              type="button"
                              className={styles.diffToggle}
                              data-active={contentFor === `batch:${batch.draft_version_id}` || undefined}
                              onClick={() => { showContent(`batch:${batch.draft_version_id}`, batch.draft_version_id as string) }}
                            >
                              草稿 diff
                            </button>
                          )}
                        </div>
                        {batch.job !== null && batch.job.error !== null && (
                          <div className={styles.error} role="alert"><div><strong>任务错误</strong><span>{batch.job.error}</span></div></div>
                        )}
                        {batch.draft_version_id !== null && contentFor === `batch:${batch.draft_version_id}` && renderDiffPanel()}
                      </div>
                    ))}
                  </div>
                )}

                <h2 className={styles.sectionTitle}>调用轨迹</h2>
                {detail.invocations.length === 0 ? (
                  <div className={styles.empty}>该技能还没有 sealed 调用轨迹。</div>
                ) : (
                  <div className={styles.invocationList}>
                    {detail.invocations.map(invocation => (
                      <div key={invocation.invocation_id} className={styles.invocationCard}>
                        <button
                          type="button"
                          className={styles.invocationHeader}
                          onClick={() => {
                            setOpenInvocation(prev => (prev === invocation.invocation_id ? null : invocation.invocation_id))
                          }}
                        >
                          <Badge variant={statusVariant(invocation.status)}>{invocation.status}</Badge>
                          {invocation.score !== null && <span>score {invocation.score}</span>}
                          {invocation.outcome !== null && <span>{invocation.outcome}</span>}
                          <span className={styles.detailMeta}>{formatEpoch(invocation.sealed_at)}</span>
                          <span className={styles.detailMeta}>{invocation.steps.length} 步</span>
                        </button>
                        {invocation.task_input !== null && (
                          <p className={styles.taskInput}>{invocation.task_input}</p>
                        )}
                        {openInvocation === invocation.invocation_id && (
                          <ol className={styles.stepList}>
                            {invocation.steps.map((step, index) => (
                              <li key={step.tool_call_id ?? index} className={styles.step}>
                                <span className={styles.stepTool}>{step.tool_name ?? '（未知工具）'}</span>
                                {step.arguments !== null && (
                                  <code className={styles.stepArgs}>{JSON.stringify(step.arguments)}</code>
                                )}
                                <Badge variant={statusVariant(step.status)}>{step.status ?? 'unknown'}</Badge>
                                {step.result_tail !== null && (
                                  <code className={styles.stepTail}>
                                    {step.result_tail}{step.result_truncated ? '…' : ''}
                                  </code>
                                )}
                              </li>
                            ))}
                          </ol>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                <h2 className={styles.sectionTitle}>版本谱系</h2>
                {detail.versions_unavailable ? (
                  <div className={styles.empty}>服务端版本列表不可用。</div>
                ) : (
                  <ul className={styles.versionList}>
                    {(detail.versions ?? []).map(version => (
                      <li key={version.version_id} className={styles.versionItem}>
                        <button
                          type="button"
                          className={styles.versionRow}
                          data-active={contentFor === `version:${version.version_id}` || undefined}
                          onClick={() => { showContent(`version:${version.version_id}`, version.version_id) }}
                        >
                          <Badge variant={statusVariant(version.status)}>{version.status ?? 'unknown'}</Badge>
                          <code>{version.version_id}</code>
                          {version.parent_version_id !== null && (
                            <span className={styles.detailMeta}>parent {version.parent_version_id}</span>
                          )}
                        </button>
                        {contentFor === `version:${version.version_id}` && renderDiffPanel()}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
