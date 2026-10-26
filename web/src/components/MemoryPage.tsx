import { useCallback, useEffect, useMemo, useState } from 'react'

import type { ManagedMemory, MemoryGraph as MemoryGraphData, MemoryHistory, MemorySnapshot } from '../api/http'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'
import { PageHeader } from './ui/PageHeader'
import { MemoryDetailDialog } from './MemoryDetailDialog'
import { MemoryGraph, typeColor } from './MemoryGraph'
import { IconSearch } from './ui/icons'
import styles from './MemoryPage.module.css'
import {
  computeMemoryInsight,
  formatDate,
  groupByType,
  summarizeMemory,
  typeLabel,
  type MemorySummary,
} from './memorySummary'


type Props = {
  snapshot: MemorySnapshot | null
  loading: boolean
  error: string | null
  /** files 档下 /api/memories 503：展示降级说明卡而非整页错误。 */
  unavailable?: boolean
  onRefresh: () => void | Promise<void>
  loadHistory: (memoryId: string) => Promise<MemoryHistory>
  /** 图谱数据按视图懒加载；未提供时图谱视图不可用。 */
  loadGraph?: () => Promise<MemoryGraphData>
  compileMemory?: (memoryId: string) => Promise<{ job_id: string; status: string }>
  compileStatus?: (jobId: string) => Promise<{ status: string; error?: string }>
  createMemory?: (payload: { content: string; memory_type: 'fact' | 'procedure' }) => Promise<unknown>
  updateMemory?: (memoryId: string, content: string) => Promise<ManagedMemory | void>
  deleteMemory?: (memoryId: string) => Promise<void>
}

type StatusTab = 'active' | 'archived'
type ViewMode = 'list' | 'graph'
type GroupMode = 'session' | 'type'

type VisibleMemory = { memory: ManagedMemory; summary: MemorySummary }
type ListGroup = { key: string; label: string; sessionId: string | null; items: VisibleMemory[] }

export function MemoryPage({ snapshot, loading, error, unavailable = false, onRefresh, loadHistory, loadGraph, compileMemory, compileStatus, createMemory, updateMemory, deleteMemory }: Props) {
  const [tab, setTab] = useState<StatusTab>('active')
  const [view, setView] = useState<ViewMode>('list')
  const [groupMode, setGroupMode] = useState<GroupMode>('session')
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<ManagedMemory | null>(null)
  const [graph, setGraph] = useState<MemoryGraphData | null>(null)
  const [graphLoading, setGraphLoading] = useState(false)
  const [graphError, setGraphError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<Set<string> | null>(null)
  const [creating, setCreating] = useState(false)
  const [newType, setNewType] = useState<'fact' | 'procedure'>('fact')
  const [newContent, setNewContent] = useState('')
  const [createBusy, setCreateBusy] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const allMemories = useMemo(
    () => (snapshot?.groups ?? []).flatMap(group => group.memories),
    [snapshot],
  )
  const insight = useMemo(() => computeMemoryInsight(allMemories), [allMemories])
  const memoryById = useMemo(
    () => new Map(allMemories.map(memory => [memory.memory_id, memory])),
    [allMemories],
  )
  const sessionTitle = useMemo(() => {
    const map = new Map<string, string>()
    for (const group of snapshot?.groups ?? []) {
      if (group.session_id !== null) {
        const raw = group.title.trim()
        if (raw === '' || raw === '会话 session-') {
          const shortId = group.session_id.replace(/^session[-_]/i, '').slice(0, 8)
          map.set(group.session_id, `会话 ${shortId}`)
        } else {
          map.set(group.session_id, raw)
        }
      }
    }
    return map
  }, [snapshot])

  const memoryLabels = useMemo(() => {
    const junk = /^[{#[\[*]/
    return Object.fromEntries(
      allMemories.map(memory => {
        const title = summarizeMemory(memory).title.trim()
        const fromSession = memory.session_id !== null
          ? sessionTitle.get(memory.session_id)
          : undefined
        const fallback = fromSession !== undefined
          ? `${typeLabel(memory.memory_type)} · ${fromSession}`
          : typeLabel(memory.memory_type)
        return [memory.memory_id, junk.test(title) ? fallback : title]
      }),
    )
  }, [allMemories, sessionTitle])

  const loadGraphData = useCallback(async () => {
    if (loadGraph === undefined || graph !== null || graphLoading) return
    setGraphLoading(true)
    setGraphError(null)
    try {
      setGraph(await loadGraph())
    } catch (cause) {
      setGraphError(cause instanceof Error ? cause.message : '图谱数据加载失败。')
    } finally {
      setGraphLoading(false)
    }
  }, [graph, graphLoading, loadGraph])

  useEffect(() => {
    if (view === 'graph') void loadGraphData()
  }, [loadGraphData, view])

  const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN')
  const matched = useMemo(() => {
    const items: VisibleMemory[] = []
    for (const memory of allMemories) {
      if (memory.status !== tab) continue
      const summary = summarizeMemory(memory)
      if (normalizedQuery.length > 0 && !summary.searchText.toLocaleLowerCase('zh-CN').includes(normalizedQuery)) {
        continue
      }
      items.push({ memory, summary })
    }
    return items
  }, [allMemories, normalizedQuery, tab])

  const listGroups = useMemo((): ListGroup[] => {
    if (groupMode === 'session') {
      const matchedIds = new Set(matched.map(item => item.memory.memory_id))
      return (snapshot?.groups ?? [])
        .map(group => ({
          key: `session:${group.session_id ?? 'none'}`,
          label: group.title,
          sessionId: group.session_id,
          items: group.memories
            .filter(memory => matchedIds.has(memory.memory_id))
            .map(memory => ({ memory, summary: summarizeMemory(memory) })),
        }))
        .filter(group => group.items.length > 0)
    }
    return groupByType(matched.map(item => item.memory))
      .map(group => ({
        key: `type:${group.key}`,
        label: group.label,
        sessionId: null,
        items: group.memories.map(memory => ({
          memory,
          summary: summarizeMemory(memory),
        })),
      }))
      .filter(group => group.items.length > 0)
  }, [groupMode, matched, snapshot])

  const seenGroupsRef = useState(() => new Set<string>())[0]
  useEffect(() => {
    if (listGroups.length === 0) return
    const newKeys = listGroups.map(group => group.key).filter(key => !seenGroupsRef.has(key))
    if (newKeys.length > 0) {
      for (const key of newKeys) seenGroupsRef.add(key)
      setExpanded(current => {
        const next = new Set(current)
        for (const key of newKeys) next.add(key)
        return next
      })
    }
  }, [listGroups, seenGroupsRef])

  const toggleGroup = (key: string) => {
    setExpanded(current => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const graphTypes = useMemo(
    () => [...new Set((graph?.nodes ?? []).map(node => node.memory_type))].sort(),
    [graph],
  )
  const filteredGraph = useMemo((): MemoryGraphData | null => {
    if (graph === null) return null
    if (typeFilter === null) return graph
    const keep = new Set(
      graph.nodes.filter(node => typeFilter.has(node.memory_type)).map(node => node.memory_id),
    )
    const entityKeep = new Set(
      graph.edges
        .filter(edge => edge.rel === 'MENTIONS' && keep.has(edge.src))
        .map(edge => edge.dst),
    )
    return {
      nodes: graph.nodes.filter(node => keep.has(node.memory_id)),
      entities: graph.entities.filter(entity => entityKeep.has(entity.entity_id)),
      edges: graph.edges.filter(edge => keep.has(edge.src) && (keep.has(edge.dst) || entityKeep.has(edge.dst))),
      sessions: graph.sessions,
      edges_truncated: graph.edges_truncated,
    }
  }, [graph, typeFilter])

  const toggleType = (type: string) => {
    setTypeFilter(current => {
      const base = current ?? new Set(graphTypes)
      const next = new Set(base)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  const submitCreate = async () => {
    if (!createMemory) return
    const trimmed = newContent.trim()
    if (!trimmed) {
      setCreateError('请填写要保存的记忆内容。')
      return
    }
    setCreateBusy(true)
    setCreateError(null)
    try {
      await createMemory({ content: trimmed, memory_type: newType })
      setNewContent('')
      setCreating(false)
      await onRefresh()
    } catch (cause) {
      setCreateError(cause instanceof Error ? cause.message : '新增记忆失败')
    } finally {
      setCreateBusy(false)
    }
  }

  return (
    <section className={styles.page} aria-labelledby="memory-page-title">
      <PageHeader
        eyebrow="长期记忆"
        title="记忆管理"
        titleId="memory-page-title"
        subtitle="自动从历史对话中提炼事实、经验与情景记录，也支持手动添加、编辑或删除记忆。"
        actions={<>
          {createMemory !== undefined && !unavailable && (
            <Button variant="primary" onClick={() => { setCreating(v => !v); setCreateError(null) }}>
              ＋ 新增记忆
            </Button>
          )}
          <div className={styles.viewSwitch} role="tablist" aria-label="视图">
            <button type="button" role="tab" aria-selected={view === 'list'} onClick={() => { setView('list') }}>列表</button>
            <button type="button" role="tab" aria-selected={view === 'graph'} onClick={() => { setView('graph') }} disabled={loadGraph === undefined}>图谱</button>
          </div>
          <Button onClick={() => { void onRefresh() }} disabled={loading}>{loading ? '加载中…' : '刷新'}</Button>
        </>}
      />

      {creating && !unavailable && (
        <div className={styles.createPanel} aria-label="新增记忆表单">
          <div className={styles.createRow}>
            <label className={styles.createField}>
              <span>记忆类型</span>
              <select
                value={newType}
                aria-label="记忆类型"
                onChange={event => { setNewType(event.target.value as 'fact' | 'procedure') }}
              >
                <option value="fact">事实与偏好 (fact)</option>
                <option value="procedure">操作流程与经验 (procedure)</option>
              </select>
            </label>
          </div>
          <label className={styles.createField}>
            <span>记忆内容</span>
            <textarea
              rows={3}
              value={newContent}
              aria-label="新记忆内容"
              placeholder="输入希望智能助手长期记住的事实、偏好或操作流程…"
              onChange={event => { setNewContent(event.target.value) }}
            />
          </label>
          {createError !== null && <p className={styles.createError} role="alert">{createError}</p>}
          <div className={styles.createActions}>
            <Button variant="primary" disabled={createBusy} onClick={() => { void submitCreate() }}>
              {createBusy ? '保存中…' : '保存记忆'}
            </Button>
            <Button onClick={() => { setCreating(false); setCreateError(null) }}>取消</Button>
          </div>
        </div>
      )}

      {unavailable && (
        <div className={styles.unavailable} role="status">
          <div>
            <strong>当前为 files 轻量记忆档</strong>
            <span>文件式记忆随对话自动读写，本页的长期记忆库与知识图谱需要开启完整记忆模式（full 档）。</span>
            <span>如需启用，可在终端运行 <code>homemaster setup --full</code> 并将配置中的 <code>memory.mode</code> 设为 <code>full</code>。</span>
          </div>
          <button type="button" onClick={() => { void onRefresh() }} disabled={loading}>
            {loading ? '加载中…' : '重新检测'}
          </button>
        </div>
      )}

      {error !== null && !unavailable && (
        <div className={styles.error} role="alert">
          <div><strong>记忆数据加载失败</strong><span>{error}</span></div>
          <button type="button" onClick={() => { void onRefresh() }}>重新加载</button>
        </div>
      )}

      {!unavailable && <>
      <div className={styles.stats} aria-label="记忆统计">
        <Stat label="任务成功" value={insight.success} tone="green" />
        <Stat label="失败·异常" value={insight.failure} tone="amber" />
        <Stat label="纯文本记忆" value={insight.plain} tone="blue" />
        <Stat label="记忆总数" value={snapshot?.stats.total_count} tone="violet" />
      </div>

      {view === 'list' && <>
      <div className={styles.toolbar}>
        <div className={styles.tabs} role="tablist" aria-label="记忆状态">
          <button type="button" role="tab" aria-selected={tab === 'active'} onClick={() => { setTab('active') }}>
            生效中 <span>{snapshot?.stats.active_count ?? 0}</span>
          </button>
          <button type="button" role="tab" aria-selected={tab === 'archived'} onClick={() => { setTab('archived') }}>
            历史版本 <span>{snapshot?.stats.archived_count ?? 0}</span>
          </button>
        </div>
        <div className={styles.tabs} role="tablist" aria-label="分组方式">
          <button type="button" role="tab" aria-selected={groupMode === 'session'} onClick={() => { setGroupMode('session') }}>按会话</button>
          <button type="button" role="tab" aria-selected={groupMode === 'type'} onClick={() => { setGroupMode('type') }}>按类型</button>
        </div>
        <div className={styles.filters}>
          <label className={styles.search}>
            <span aria-hidden="true"><IconSearch size={14} /></span>
            <input
              type="search"
              value={query}
              onChange={event => { setQuery(event.target.value) }}
              placeholder="搜索摘要、对象、路径或会话…"
              aria-label="搜索记忆"
            />
          </label>
        </div>
      </div>

      <div className={styles.groups}>
        {loading && snapshot === null && <div className={styles.empty}>正在读取记忆…</div>}
        {!loading && error === null && snapshot !== null && listGroups.length === 0 && (
          <div className={styles.empty}>当前筛选条件下没有记忆记录</div>
        )}
        {listGroups.map(group => {
          const isOpen = normalizedQuery.length > 0 || expanded.has(group.key)
          return (
            <article className={styles.group} key={group.key}>
              <button
                type="button"
                className={styles.groupHeader}
                aria-expanded={isOpen}
                onClick={() => { toggleGroup(group.key) }}
              >
                <span className={styles.chevron} aria-hidden="true">›</span>
                <span className={styles.groupTitle}><strong>{group.label}</strong></span>
                <span className={styles.groupCount}>{group.items.length} 条</span>
              </button>
              {isOpen && (
                <div className={styles.memoryList}>
                  {group.items.map(({ memory, summary }) => (
                    <button
                      type="button"
                      className={styles.memoryCard}
                      key={memory.memory_id}
                      aria-label={`查看记忆 ${summary.title}`}
                      onClick={() => { setSelected(memory) }}
                    >
                      <span className={styles.cardTop}>
                        <span className={styles.summaryIcon} data-tone={summary.tone} aria-hidden="true">
                          {summary.icon}
                        </span>
                        <Badge variant="muted">{typeLabel(memory.memory_type)}</Badge>
                        <time>{formatDate(memory.updated_at ?? memory.created_at)}</time>
                      </span>
                      <span className={styles.content}>{summary.title}</span>
                      {summary.detail !== null && (
                        <span className={styles.detailLine}>{summary.detail}</span>
                      )}
                      {groupMode === 'type' && memory.session_id !== null && (
                        <span className={styles.meta}>来自对话：{sessionTitle.get(memory.session_id) ?? '历史会话'}</span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </article>
          )
        })}
      </div>
      </>}

      {view === 'graph' && (
        <div className={styles.graphPanel}>
          {graphLoading && <div className={styles.empty}>正在加载图谱…</div>}
          {graphError !== null && (
            <div className={styles.error} role="alert">
              <div><strong>图谱数据加载失败</strong><span>{graphError}</span></div>
              <button type="button" onClick={() => { setGraph(null); setGraphError(null); void loadGraphData() }}>重新加载</button>
            </div>
          )}
          {filteredGraph !== null && graphError === null && <>
            <div className={styles.graphToolbar}>
              <div className={styles.legend} role="group" aria-label="类型图例">
                {graphTypes.map(type => (
                  <button
                    type="button"
                    key={type}
                    className={styles.legendItem}
                    aria-pressed={typeFilter === null || typeFilter.has(type)}
                    data-off={typeFilter !== null && !typeFilter.has(type) || undefined}
                    onClick={() => { toggleType(type) }}
                  >
                    <span style={{ background: typeColor(type) }} />{typeLabel(type)}
                  </button>
                ))}
                <span className={styles.legendItem}><span style={{ background: 'var(--muted-2)' }} />实体</span>
              </div>
              <span className={styles.graphHint}>圆点=记忆 · 小点=实体 · 连线=提及实体 · 拖动平移 · 滚轮缩放 · 点击查看详情</span>
              {filteredGraph.edges_truncated === true && (
                <span className={styles.graphHint}>关系较多，当前仅显示高频核心连线</span>
              )}
            </div>
            <MemoryGraph
              graph={filteredGraph}
              memoryLabels={memoryLabels}
              onSelect={memoryId => {
                const memory = memoryById.get(memoryId)
                if (memory !== undefined) setSelected(memory)
              }}
            />
          </>}
        </div>
      )}
      </>}

      {selected !== null && (
        <MemoryDetailDialog
          memory={memoryById.get(selected.memory_id) ?? selected}
          loadHistory={loadHistory}
          compileMemory={compileMemory}
          compileStatus={compileStatus}
          onUpdate={updateMemory !== undefined ? async (id, content) => {
            const updated = await updateMemory(id, content)
            if (updated !== undefined && updated !== null && typeof updated === 'object' && 'memory_id' in updated) {
              setSelected(updated)
            }
            await onRefresh()
          } : undefined}
          onDelete={deleteMemory !== undefined ? async id => {
            await deleteMemory(id)
            setSelected(null)
            await onRefresh()
          } : undefined}
          onClose={() => { setSelected(null) }}
        />
      )}
    </section>
  )
}

function Stat({ label, value, tone }: { label: string; value: number | undefined; tone: string }) {
  return <div className={styles.stat} data-tone={tone}><span>{label}</span><strong>{value ?? '—'}</strong></div>
}


