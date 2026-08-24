import { useEffect, useMemo, useState } from 'react'

import type { ManagedMemory, MemoryHistory, MemorySnapshot } from '../api/http'
import { MemoryDetailDialog } from './MemoryDetailDialog'
import styles from './MemoryPage.module.css'


type Props = {
  snapshot: MemorySnapshot | null
  loading: boolean
  error: string | null
  onRefresh: () => void | Promise<void>
  loadHistory: (memoryId: string) => Promise<MemoryHistory>
}

type StatusTab = 'active' | 'archived'

const groupKey = (sessionId: string | null) => sessionId ?? '__unassigned__'

export function MemoryPage({ snapshot, loading, error, onRefresh, loadHistory }: Props) {
  const [tab, setTab] = useState<StatusTab>('active')
  const [query, setQuery] = useState('')
  const [memoryType, setMemoryType] = useState('all')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [initializedGroups, setInitializedGroups] = useState(false)
  const [selected, setSelected] = useState<ManagedMemory | null>(null)

  useEffect(() => {
    if (!initializedGroups && snapshot !== null) {
      setExpanded(new Set(snapshot.groups.map(group => groupKey(group.session_id))))
      setInitializedGroups(true)
    }
  }, [initializedGroups, snapshot])

  const availableTypes = useMemo(() => {
    const labels = new Map<string, string>()
    for (const group of snapshot?.groups ?? []) {
      for (const memory of group.memories) labels.set(memory.memory_type, memory.memory_type_label)
    }
    return [...labels.entries()].sort((left, right) => left[1].localeCompare(right[1], 'zh-CN'))
  }, [snapshot])

  const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN')
  const filtering = normalizedQuery.length > 0 || memoryType !== 'all'
  const visibleGroups = useMemo(() => (snapshot?.groups ?? []).map(group => ({
    ...group,
    memories: group.memories.filter(memory => {
      if (memory.status !== tab) return false
      if (memoryType !== 'all' && memory.memory_type !== memoryType) return false
      if (normalizedQuery.length === 0) return true
      return [memory.content, memory.memory_id, memory.memory_type_label, group.title, group.session_id ?? '']
        .some(value => value.toLocaleLowerCase('zh-CN').includes(normalizedQuery))
    }),
  })).filter(group => group.memories.length > 0), [memoryType, normalizedQuery, snapshot, tab])

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
          <p>按来源会话查看 HomeMaster 已保存的记忆。本页面目前仅供查看。</p>
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
        <Stat label="生效中的记忆" value={snapshot?.stats.active_count} tone="green" />
        <Stat label="已归档的记忆" value={snapshot?.stats.archived_count} tone="amber" />
        <Stat label="记忆总数" value={snapshot?.stats.total_count} tone="blue" />
        <Stat label="来源会话" value={snapshot?.stats.session_group_count} tone="violet" />
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
              placeholder="搜索正文、会话或记忆 ID"
              aria-label="搜索记忆"
            />
          </label>
          <label>
            <span className={styles.srOnly}>记忆类型</span>
            <select value={memoryType} onChange={event => { setMemoryType(event.target.value) }} aria-label="记忆类型">
              <option value="all">全部类型</option>
              {availableTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
        </div>
      </div>

      <div className={styles.groups}>
        {loading && snapshot === null && <div className={styles.empty}>正在读取记忆…</div>}
        {!loading && error === null && snapshot !== null && visibleGroups.length === 0 && (
          <div className={styles.empty}>当前条件下没有记忆</div>
        )}
        {visibleGroups.map(group => {
          const key = groupKey(group.session_id)
          const isOpen = filtering || expanded.has(key)
          return (
            <article className={styles.group} key={key}>
              <button
                type="button"
                className={styles.groupHeader}
                aria-expanded={isOpen}
                onClick={() => { toggleGroup(key) }}
              >
                <span className={styles.chevron} aria-hidden="true">›</span>
                <span className={styles.groupTitle}><strong>{group.title}</strong><small>{group.session_id ?? '无 session ID'}</small></span>
                <span className={styles.groupCount}>{group.memories.length} 条</span>
              </button>
              {isOpen && (
                <div className={styles.memoryList}>
                  {group.memories.map(memory => (
                    <button
                      type="button"
                      className={styles.memoryCard}
                      key={memory.memory_id}
                      aria-label={`查看记忆 ${memory.content}`}
                      onClick={() => { setSelected(memory) }}
                    >
                      <span className={styles.cardTop}>
                        <span className={styles.typeBadge}>{memory.memory_type_label}</span>
                        <time>{formatDate(memory.updated_at ?? memory.created_at)}</time>
                      </span>
                      <span className={styles.content}>{memory.content}</span>
                      <span className={styles.memoryId}>{memory.memory_id}</span>
                    </button>
                  ))}
                </div>
              )}
            </article>
          )
        })}
      </div>

      {selected !== null && (
        <MemoryDetailDialog memory={selected} loadHistory={loadHistory} onClose={() => { setSelected(null) }} />
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
