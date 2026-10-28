import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ManagedMemory, MemoryGraph, MemoryHistory, MemorySnapshot } from '../api/http'
import { MemoryPage } from './MemoryPage'


function memory(
  memoryId: string,
  status: 'active' | 'archived',
  content: string,
  extra: Partial<ManagedMemory> = {},
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
    ...extra,
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


const graph: MemoryGraph = {
  nodes: [
    {
      memory_id: 'memory-active',
      preview: 'active memory body',
      memory_type: 'fact',
      status: 'active',
      session_id: 'session-01',
      updated_at: '2026-08-24T08:30:00Z',
    },
    {
      memory_id: 'memory-archived',
      preview: 'archived memory body',
      memory_type: 'fact',
      status: 'archived',
      session_id: 'session-01',
      updated_at: '2026-08-24T08:40:00Z',
    },
  ],
  entities: [{ entity_id: 'ent-1', name: '厨房' }],
  edges: [
    { src: 'memory-active', rel: 'MENTIONS', dst: 'ent-1', dst_label: 'Entity', entity_name: '厨房' },
  ],
  sessions: [{ session_id: 'session-01', title: 'first user request' }],
}

describe('MemoryPage', () => {
  it('renders insight stats and groups the active tab by session', () => {
    render(
      <MemoryPage
        snapshot={snapshot}
        loading={false}
        error={null}
        onRefresh={vi.fn()}
        loadHistory={vi.fn()}
      />,
    )

    expect(screen.getByText('任务成功')).toBeInTheDocument()
    expect(screen.getByText('失败·异常')).toBeInTheDocument()
    expect(screen.getByText('纯文本记忆')).toBeInTheDocument()
    expect(screen.getByText('记忆总数')).toBeInTheDocument()
    // Default grouping is the source session, labelled by its title.
    expect(screen.getByRole('button', { name: /first user request/ })).toBeInTheDocument()
    expect(screen.getByText('active memory body')).toBeInTheDocument()
    expect(screen.getByText('事实')).toBeInTheDocument()
    expect(screen.queryByText('archived memory body')).not.toBeInTheDocument()
    expect(screen.queryByText(/memory-active/)).not.toBeInTheDocument()
  })

  it('groups by memory type when the grouping toggle is switched', () => {
    render(
      <MemoryPage
        snapshot={snapshot}
        loading={false}
        error={null}
        onRefresh={vi.fn()}
        loadHistory={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: '按类型' }))
    expect(screen.getByRole('button', { name: /事实/ })).toBeInTheDocument()
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

    fireEvent.click(screen.getByRole('tab', { name: /历史版本/ }))
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
    const dialog = screen.getByRole('dialog', { name: '记忆详情' })
    expect(within(dialog).queryByRole('button', { name: /新增|编辑|删除|恢复|归档/ })).not.toBeInTheDocument()
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

  it('renders a human summary instead of raw JSON for structured trajectories', () => {
    const trajectory = memory('memory-traj', 'active', '{"classification":"agent_success",...}', {
      structure_status: 'valid',
      classification: 'agent_success',
      outcome: 'success',
      goal_type: '{"pddl_params":{"object_target":"Book","parent_target":"SideTable"},"task_type":"pick_and_place_simple"}',
      episode_id: 'valid_seen/pick_and_place_simple-Book-SideTable-301',
      record: { classification: 'agent_success' },
    })
    render(
      <MemoryPage
        snapshot={{ ...snapshot, groups: [{ ...snapshot.groups[0], memories: [trajectory] }] }}
        loading={false}
        error={null}
        onRefresh={vi.fn()}
        loadHistory={vi.fn()}
      />,
    )

    expect(screen.getByText('把Book放到SideTable')).toBeVisible()
    expect(screen.queryByText('{"classification":"agent_success",...}')).not.toBeInTheDocument()
    // Source session is labelled by its readable title, not the raw id.
    expect(screen.getByRole('button', { name: /first user request/ })).toBeVisible()
  })

  it('lazy-loads the graph view and opens details from a node click', async () => {
    const loadGraph = vi.fn().mockResolvedValue(graph)
    const history: MemoryHistory = { memory_id: activeMemory.memory_id, versions: [activeMemory] }
    render(
      <MemoryPage
        snapshot={snapshot}
        loading={false}
        error={null}
        onRefresh={vi.fn()}
        loadHistory={vi.fn().mockResolvedValue(history)}
        loadGraph={loadGraph}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: '图谱' }))
    expect(loadGraph).toHaveBeenCalledOnce()
    const canvas = await screen.findByRole('img', { name: '记忆知识图谱' })
    const memoryNode = canvas.querySelector('g[data-node="mem:memory-active"]')
    const entityNode = canvas.querySelector('g[data-node="ent:ent-1"]')
    expect(memoryNode?.querySelector('title')?.textContent).toBe('active memory body（1 条连接）')
    expect(entityNode?.querySelector('title')?.textContent).toBe('厨房（1 条连接）')
    // Legend exposes memory-type filters plus the entity marker.
    expect(screen.getByRole('button', { name: /事实/ })).toBeInTheDocument()
    expect(screen.getByText('实体')).toBeInTheDocument()

    fireEvent.click(memoryNode!)
    expect(await screen.findByRole('dialog', { name: '记忆详情' })).toBeVisible()
  })

  it('surfaces graph load errors with a retry', async () => {
    const loadGraph = vi.fn().mockRejectedValue(new Error('neo4j down'))
    render(
      <MemoryPage
        snapshot={snapshot}
        loading={false}
        error={null}
        onRefresh={vi.fn()}
        loadHistory={vi.fn()}
        loadGraph={loadGraph}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: '图谱' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('neo4j down')
  })

  it('supports creating, updating, and deleting memories when callbacks are supplied', async () => {
    const createMemory = vi.fn().mockResolvedValue({})
    const updateMemory = vi.fn().mockResolvedValue(undefined)
    const deleteMemory = vi.fn().mockResolvedValue(undefined)
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    render(
      <MemoryPage
        snapshot={snapshot}
        loading={false}
        error={null}
        onRefresh={onRefresh}
        loadHistory={vi.fn().mockResolvedValue({ memory_id: 'memory-active', versions: [] })}
        createMemory={createMemory}
        updateMemory={updateMemory}
        deleteMemory={deleteMemory}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '＋ 新增记忆' }))
    fireEvent.change(screen.getByRole('textbox', { name: '新记忆内容' }), { target: { value: '厨房水杯放在第二层架子' } })
    fireEvent.click(screen.getByRole('button', { name: '保存记忆' }))

    await waitFor(() => {
      expect(createMemory).toHaveBeenCalledWith({ content: '厨房水杯放在第二层架子', memory_type: 'fact' })
    })

    fireEvent.click(screen.getByRole('button', { name: /查看记忆 active memory body/ }))
    const dialog = await screen.findByRole('dialog', { name: '记忆详情' })

    fireEvent.click(within(dialog).getByRole('button', { name: '编辑' }))
    fireEvent.change(within(dialog).getByRole('textbox', { name: '编辑记忆内容' }), { target: { value: 'updated memory body' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存修改' }))
    await waitFor(() => {
      expect(updateMemory).toHaveBeenCalledWith('memory-active', 'updated memory body')
    })

    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '确认删除' }))
    await waitFor(() => {
      expect(deleteMemory).toHaveBeenCalledWith('memory-active')
    })
  })

})
