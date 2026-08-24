import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ManagedMemory, MemoryHistory, MemorySnapshot } from '../api/http'
import { MemoryPage } from './MemoryPage'


function memory(
  memoryId: string,
  status: 'active' | 'archived',
  content: string,
): ManagedMemory {
  return {
    memory_id: memoryId,
    content,
    memory_type: 'fact',
    memory_type_label: '事实',
    status,
    session_id: 'session-01',
    created_at: '2026-08-24T08:00:00Z',
    updated_at: '2026-08-24T08:30:00Z',
    archived_at: status === 'archived' ? '2026-08-24T08:40:00Z' : null,
    archive_reason: status === 'archived' ? 'user_request' : null,
    record: null,
    structure_status: 'plain',
    has_history: true,
  }
}


const activeMemory = memory('memory-active', 'active', 'active memory body')
const archivedMemory = memory('memory-archived', 'archived', 'archived memory body')
const snapshot: MemorySnapshot = {
  stats: {
    active_count: 1,
    archived_count: 1,
    total_count: 2,
    session_group_count: 1,
  },
  groups: [
    {
      session_id: 'session-01',
      title: 'first user request',
      active_count: 1,
      archived_count: 1,
      memories: [activeMemory, archivedMemory],
    },
  ],
}


describe('MemoryPage', () => {
  it('renders Chinese stats and groups the active tab by session', () => {
    render(
      <MemoryPage
        snapshot={snapshot}
        loading={false}
        error={null}
        onRefresh={vi.fn()}
        loadHistory={vi.fn()}
      />,
    )

    expect(screen.getByText('生效中的记忆')).toBeInTheDocument()
    expect(screen.getByText('已归档的记忆')).toBeInTheDocument()
    expect(screen.getByText('记忆总数')).toBeInTheDocument()
    expect(screen.getByText('来源会话')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /first user request/ })).toBeInTheDocument()
    expect(screen.getByText('active memory body')).toBeInTheDocument()
    expect(screen.queryByText('archived memory body')).not.toBeInTheDocument()
  })

  it('switches tabs, filters text, and keeps matched groups expanded', () => {
    render(
      <MemoryPage
        snapshot={snapshot}
        loading={false}
        error={null}
        onRefresh={vi.fn()}
        loadHistory={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: /已归档/ }))
    fireEvent.click(screen.getByRole('button', { name: /first user request/ }))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'archived memory' } })

    expect(screen.getByText('archived memory body')).toBeVisible()
    expect(screen.queryByText('active memory body')).not.toBeInTheDocument()
  })

  it('opens a read-only detail and lazy-loads history without mutation controls', async () => {
    const history: MemoryHistory = { memory_id: activeMemory.memory_id, versions: [activeMemory] }
    const loadHistory = vi.fn().mockResolvedValue(history)
    render(
      <MemoryPage
        snapshot={snapshot}
        loading={false}
        error={null}
        onRefresh={vi.fn()}
        loadHistory={loadHistory}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /查看记忆 active memory body/ }))

    expect(await screen.findByRole('dialog', { name: '记忆详情' })).toBeVisible()
    expect(screen.getByText('版本历史')).toBeVisible()
    await waitFor(() => { expect(loadHistory).toHaveBeenCalledWith('memory-active') })
    expect(screen.queryByRole('button', { name: /新增|编辑|删除|恢复|归档/ })).not.toBeInTheDocument()
  })

  it('shows a retryable Chinese error without hiding the page heading', () => {
    const onRefresh = vi.fn()
    render(
      <MemoryPage
        snapshot={null}
        loading={false}
        error="记忆服务暂不可用"
        onRefresh={onRefresh}
        loadHistory={vi.fn()}
      />,
    )

    expect(screen.getByRole('heading', { name: '记忆管理' })).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent('记忆服务暂不可用')
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }))
    expect(onRefresh).toHaveBeenCalledOnce()
  })
})
