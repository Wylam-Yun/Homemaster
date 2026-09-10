import { useEffect, useMemo, useState } from 'react'

import type { ManagedMemory, MemoryHistory, MemorySnapshot } from '../api/http'
import { MemoryDetailDialog } from './MemoryDetailDialog'
import styles from './MemoryPage.module.css'
import {
  computeMemoryInsight,
  groupByType,
  shortSession,
  summarizeMemory,
  type MemorySummary,
} from './memorySummary'


type Props = {
  snapshot: MemorySnapshot | null
  loading: boolean
  error: string | null
  onRefresh: () => void | Promise<void>
  loadHistory: (memoryId: string) => Promise<MemoryHistory>
  compileMemory?: (memoryId: string) => Promise<{ job_id: string; status: string }>
  compileStatus?: (jobId: string) => Promise<{ status: string; error?: string }>
}

type StatusTab = 'active' | 'archived'

type VisibleMemory = { memory: ManagedMemory; summary: MemorySummary }

export function MemoryPage({ snapshot, loading, error, onRefresh, loadHistory, compileMemory, compileStatus }: Props) {
  const [tab, setTab] = useState<StatusTab>('active')
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [initializedGroups, setInitializedGroups] = useState(false)
  const [selected, setSelected] = useState<ManagedMemory | null>(null)

  const allMemories = useMemo(
    () => (snapshot?.groups ?? []).flatMap(group => group.memories),
    [snapshot],
  )
  const insight = useMemo(() => computeMemoryInsight(allMemories), [allMemories])

  useEffect(() => {
    if (!initializedGroups && snapshot !== null) {
      setExpanded(new Set(groupByType(allMemories).map(group => group.key)))
      setInitializedGroups(true)
    }
  }, [initializedGroups, snapshot, allMemories])

  const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN')
  const visibleGroups = useMemo(() => {
    const matched: VisibleMemory[] = []
    for (const memory of allMemories) {
      if (memory.status !== tab) continue
      const summary = summarizeMemory(memory)
      if (normalizedQuery.length > 0 && !summary.searchText.toLocaleLowerCase('zh-CN').includes(normalizedQuery)) {
        continue
      }
      matched.push({ memory, summary })
    }
    const buckets = new Map<string, { key: string; label: string; items: VisibleMemory[] }>()
    for (const group of groupByType(matched.map(item => item.memory))) {
      buckets.set(group.key, { key: group.key, label: group.label, items: [] })
    }
    for (const item of matched) {
      buckets.get(item.memory.memory_type)?.items.push(item)
    }
    return [...buckets.values()].filter(group => group.items.length > 0)
  }, [allMemories, normalizedQuery, tab])

  const toggleGroup = (key: string) => {
    setExpanded(current => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <section className={styles.page} aria-labelledby="memory-page-title">
      <div className={styles.headingRow}>
        <div>
          <p className={styles.eyebrow}>长期记忆</p>
          <h1 id="memory-page-title">记忆管理</h1>
          <p>按类型查看已保存的记忆摘要。本页面目前仅供查看。</p>
        </div>
        <button type="button" className={styles.refresh} onClick={() => { void onRefresh() }} disabled={loading}>
          {loading ? '加载中…' : '刷新'}
        </button>
      </div>

      {error !== null && (
        <div className={styles.error} role="alert">
          <div><strong>记忆数据加载失败</strong><span>{error}</span></div>
          <button type="button" onClick={() => { void onRefresh() }}>重新加载</button>
        </div>
      )}

      <div className={styles.stats} aria-label="记忆统计">
        <Stat label="任务成功" value={insight.success} tone="green" />
        <Stat label="失败·异常" value={insight.failure} tone="amber" />
        <Stat label="纯文本记忆" value={insight.plain} tone="blue" />
        <Stat label="记忆总数" value={snapshot?.stats.total_count} tone="violet" />
      </div>

      <div className={styles.toolbar}>
        <div className={styles.tabs} role="tablist" aria-label="记忆状态">
          <button type="button" role="tab" aria-selected={tab === 'active'} onClick={() => { setTab('active') }}>
            生效中 <span>{snapshot?.stats.active_count ?? 0}</span>
          </button>
          <button type="button" role="tab" aria-selected={tab === 'archived'} onClick={() => { setTab('archived') }}>
            已归档 <span>{snapshot?.stats.archived_count ?? 0}</span>
          </button>
        </div>
        <div className={styles.filters}>
          <label className={styles.search}>
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              value={query}
              onChange={event => { setQuery(event.target.value) }}
              placeholder="搜索摘要、类型或记忆 ID"
              aria-label="搜索记忆"
            />
          </label>
        </div>
      </div>

      <div className={styles.groups}>
        {loading && snapshot === null && <div className={styles.empty}>正在读取记忆…</div>}
        {!loading && error === null && snapshot !== null && visibleGroups.length === 0 && (
          <div className={styles.empty}>当前条件下没有记忆</div>
        )}
        {visibleGroups.map(group => {
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
                        <time>{formatDate(memory.updated_at ?? memory.created_at)}</time>
                      </span>
                      <span className={styles.content}>{summary.title}</span>
                      <span className={styles.meta}>{cardMeta(summary.detail, memory.session_id)}</span>
                    </button>
                  ))}
                </div>
              )}
            </article>
          )
        })}
      </div>

      {selected !== null && (
        <MemoryDetailDialog memory={selected} loadHistory={loadHistory} compileMemory={compileMemory} compileStatus={compileStatus} onClose={() => { setSelected(null) }} />
      )}
    </section>
  )
}

function cardMeta(detail: string | null, sessionId: string | null): string {
  const source = `来源 ${shortSession(sessionId)}`
  return detail !== null ? `${detail} · ${source}` : source
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
