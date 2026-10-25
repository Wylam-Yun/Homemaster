import { useCallback, useEffect, useMemo, useState } from 'react'

import type { ManagedMemory, MemoryGraph as MemoryGraphData, MemoryHistory, MemorySnapshot } from '../api/http'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'
import { PageHeader } from './ui/PageHeader'
import { MemoryDetailDialog } from './MemoryDetailDialog'
import { MemoryGraph, typeColor } from './MemoryGraph'
import styles from './MemoryPage.module.css'
import {
  computeMemoryInsight,
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
}

type StatusTab = 'active' | 'archived'
type ViewMode = 'list' | 'graph'
type GroupMode = 'session' | 'type'

type VisibleMemory = { memory: ManagedMemory; summary: MemorySummary }
type ListGroup = { key: string; label: string; sessionId: string | null; items: VisibleMemory[] }

export function MemoryPage({ snapshot, loading, error, unavailable = false, onRefresh, loadHistory, loadGraph, compileMemory, compileStatus }: Props) {
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
      if (group.session_id !== null) map.set(group.session_id, group.title)
    }
    return map
  }, [snapshot])

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

  useEffect(() => {
    if (listGroups.length > 0 && expanded.size === 0) {
      setExpanded(new Set(listGroups.map(group => group.key)))
    }
  }, [expanded.size, listGroups])

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

  return (
    <section className={styles.page} aria-labelledby="memory-page-title">
      <PageHeader
        eyebrow="长期记忆"
        title="记忆管理"
        titleId="memory-page-title"
        subtitle="按来源会话或类型浏览记忆，或切到图谱查看记忆间的实体关联。"
        actions={<>
          <div className={styles.viewSwitch} role="tablist" aria-label="视图">
            <button type="button" role="tab" aria-selected={view === 'list'} onClick={() => { setView('list') }}>列表</button>
            <button type="button" role="tab" aria-selected={view === 'graph'} onClick={() => { setView('graph') }} disabled={loadGraph === undefined}>图谱</button>
          </div>
          <Button onClick={() => { void onRefresh() }} disabled={loading}>{loading ? '加载中…' : '刷新'}</Button>
        </>}
      />

      {unavailable && (
        <div className={styles.unavailable} role="status">
          <div>
            <strong>当前为 files 轻量记忆档</strong>
            <span>文件式记忆随会话读写，本页的长期记忆管理在 files 档下不可用。</span>
            <span>切换到 full 档：将 memory.mode 设为 full 并接入记忆后端后重启服务。</span>
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
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              value={query}
              onChange={event => { setQuery(event.target.value) }}
              placeholder="搜索摘要或会话"
              aria-label="搜索记忆"
            />
          </label>
        </div>
      </div>

      <div className={styles.groups}>
        {loading && snapshot === null && <div className={styles.empty}>正在读取记忆…</div>}
        {!loading && error === null && snapshot !== null && listGroups.length === 0 && (
          <div className={styles.empty}>当前条件下没有记忆</div>
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
                      {groupMode === 'type' && memory.session_id !== null && (
                        <span className={styles.meta}>来自 {sessionTitle.get(memory.session_id) ?? '未知会话'}</span>
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
              <div className={styles.legend} aria-label="类型图例">
                {graphTypes.map(type => (
                  <button
                    type="button"
                    key={type}
                    className={styles.legendItem}
                    data-off={typeFilter !== null && !typeFilter.has(type) || undefined}
                    onClick={() => { toggleType(type) }}
                  >
                    <span style={{ background: typeColor(type) }} />{typeLabel(type)}
                  </button>
                ))}
                <span className={styles.legendItem}><span style={{ background: 'var(--muted-2)' }} />实体</span>
              </div>
              <span className={styles.graphHint}>圆点=记忆 · 小点=实体 · 拖动平移 · 滚轮缩放 · 点击查看详情</span>
            </div>
            <MemoryGraph
              graph={filteredGraph}
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
        <MemoryDetailDialog memory={selected} loadHistory={loadHistory} compileMemory={compileMemory} compileStatus={compileStatus} onClose={() => { setSelected(null) }} />
      )}
    </section>
  )
}

function Stat({ label, value, tone }: { label: string; value: number | undefined; tone: string }) {
  return <div className={styles.stat} data-tone={tone}><span>{label}</span><strong>{value ?? '—'}</strong></div>
}

function formatDate(value: string | null): string {
  if (value === null) return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date)
}
