import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { SessionSummary } from '../api/http'
import { SessionsPage, type SessionSignal } from './SessionsPage'

const SESSIONS: SessionSummary[] = [
  { session_id: 's-1', title: '整理厨房', message_count: 12, updated_at: '2026-10-25T10:00:00Z' },
  { session_id: 's-2', title: '写周报', message_count: 3, updated_at: '2026-10-25T08:00:00Z' },
  { session_id: 's-3', title: '调试烤炉', message_count: 30, updated_at: '2026-10-25T12:00:00Z' },
]

const SIGNALS: Record<string, SessionSignal> = { 's-3': 'running' }

function renderPage(overrides: Partial<Parameters<typeof SessionsPage>[0]> = {}) {
  const props: Parameters<typeof SessionsPage>[0] = {
    sessions: SESSIONS,
    sessionId: null,
    signals: SIGNALS,
    sessionTitle: session => session.title ?? session.session_id,
    formatTime: value => value ?? '',
    renamingId: null,
    renameDraft: '',
    onRenameDraft: vi.fn(),
    onStartRename: vi.fn(),
    onCommitRename: vi.fn(),
    onCancelRename: vi.fn(),
    onArchive: vi.fn(),
    onDelete: vi.fn(),
    archivingIds: new Set<string>(),
    canArchive: true,
    ...overrides,
  }
  render(<SessionsPage {...props} />)
  return props
}

function rowOrder(): string[] {
  return screen.getAllByRole('link', { name: /打开会话/ })
    .map(link => link.getAttribute('aria-label') ?? '')
}

describe('SessionsPage', () => {
  it('filters sessions by title via the search box', () => {
    renderPage()

    fireEvent.change(screen.getByRole('searchbox', { name: '搜索会话' }), { target: { value: '烤炉' } })

    expect(screen.getByText('调试烤炉')).toBeInTheDocument()
    expect(screen.queryByText('整理厨房')).toBeNull()
    expect(screen.queryByText('写周报')).toBeNull()

    fireEvent.change(screen.getByRole('searchbox', { name: '搜索会话' }), { target: { value: '不存在' } })
    expect(screen.getByText('没有匹配的会话')).toBeInTheDocument()
  })

  it('sorts by updated time, title and message count', () => {
    renderPage()

    // Default: most recently updated first.
    expect(rowOrder()).toEqual(['打开会话 调试烤炉', '打开会话 整理厨房', '打开会话 写周报'])

    fireEvent.change(screen.getByRole('combobox', { name: '排序方式' }), { target: { value: 'title' } })
    expect(rowOrder()).toEqual(['打开会话 写周报', '打开会话 整理厨房', '打开会话 调试烤炉'])

    fireEvent.change(screen.getByRole('combobox', { name: '排序方式' }), { target: { value: 'count' } })
    expect(rowOrder()).toEqual(['打开会话 调试烤炉', '打开会话 整理厨房', '打开会话 写周报'])
  })

  it('routes row actions to the shared callbacks', () => {
    const props = renderPage()

    fireEvent.click(screen.getByRole('button', { name: '重命名会话 写周报' }))
    expect(props.onStartRename).toHaveBeenCalledWith(SESSIONS[1])

    fireEvent.click(screen.getByRole('button', { name: '归档会话 整理厨房' }))
    expect(props.onArchive).toHaveBeenCalledWith(SESSIONS[0])

    fireEvent.click(screen.getByRole('button', { name: '删除会话 调试烤炉' }))
    expect(props.onDelete).toHaveBeenCalledWith(SESSIONS[2])

    expect(screen.getByRole('link', { name: '打开会话 整理厨房' })).toHaveAttribute('href', '#/chat/s-1')
  })

  it('renders the shared inline rename editor for the renaming row', () => {
    const props = renderPage({ renamingId: 's-2', renameDraft: '新标题' })

    const input = screen.getByRole('textbox', { name: '重命名会话 写周报' })
    expect(input).toHaveValue('新标题')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(props.onCommitRename).toHaveBeenCalledWith(SESSIONS[1])
  })

  it('disables the archive button while a job is in flight and hides it without canArchive', () => {
    const { rerender } = render(
      <SessionsPage
        {...{
          sessions: SESSIONS,
          sessionId: null,
          signals: SIGNALS,
          sessionTitle: session => session.title ?? session.session_id,
          formatTime: value => value ?? '',
          renamingId: null,
          renameDraft: '',
          onRenameDraft: vi.fn(),
          onStartRename: vi.fn(),
          onCommitRename: vi.fn(),
          onCancelRename: vi.fn(),
          onArchive: vi.fn(),
          onDelete: vi.fn(),
          archivingIds: new Set(['s-1']),
          canArchive: true,
        }}
      />,
    )

    expect(screen.getByRole('button', { name: '归档会话 整理厨房' })).toBeDisabled()

    rerender(
      <SessionsPage
        {...{
          sessions: SESSIONS,
          sessionId: null,
          signals: SIGNALS,
          sessionTitle: session => session.title ?? session.session_id,
          formatTime: value => value ?? '',
          renamingId: null,
          renameDraft: '',
          onRenameDraft: vi.fn(),
          onStartRename: vi.fn(),
          onCommitRename: vi.fn(),
          onCancelRename: vi.fn(),
          onArchive: vi.fn(),
          onDelete: vi.fn(),
          archivingIds: new Set<string>(),
          canArchive: false,
        }}
      />,
    )
    expect(screen.queryByRole('button', { name: /归档会话/ })).toBeNull()
  })
})
