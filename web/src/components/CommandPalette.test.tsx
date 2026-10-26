import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { SessionSummary } from '../api/http'
import { CommandPalette, type CommandPaletteAction } from './CommandPalette'

const sessions: SessionSummary[] = [
  { session_id: 'session-aa', title: '整理下载目录', message_count: 6, updated_at: '2026-10-20T03:00:00+00:00' },
  { session_id: 'session-bb', title: '巡视客厅', message_count: 2, updated_at: '2026-10-19T03:00:00+00:00' },
]

function renderPalette(actions: CommandPaletteAction[], overrides: Partial<Parameters<typeof CommandPalette>[0]> = {}) {
  const onClose = vi.fn()
  const onSelectSession = vi.fn()
  render(
    <CommandPalette
      actions={actions}
      sessions={sessions}
      onSelectSession={onSelectSession}
      onClose={onClose}
      {...overrides}
    />,
  )
  return { onClose, onSelectSession }
}

describe('CommandPalette', () => {
  it('runs a fixed command with Enter', () => {
    const run = vi.fn()
    renderPalette([{ id: 'chat.new', label: '新建会话', run }])
    fireEvent.keyDown(screen.getByLabelText('搜索命令或会话'), { key: 'Enter' })
    expect(run).toHaveBeenCalledOnce()
  })

  it('filters commands by query', () => {
    renderPalette([
      { id: 'nav.settings', label: '前往：模型与设置', keywords: 'settings provider', run: vi.fn() },
      { id: 'theme.toggle', label: '切换到深色模式', keywords: 'theme dark', run: vi.fn() },
    ])
    const input = screen.getByLabelText('搜索命令或会话')
    fireEvent.change(input, { target: { value: '深色' } })
    expect(screen.queryByRole('option', { name: /切换到深色模式/ })).not.toBeNull()
    expect(screen.queryByRole('option', { name: /模型与设置/ })).toBeNull()
  })

  it('matches commands by english keywords', () => {
    renderPalette([{ id: 'nav.settings', label: '前往：模型与设置', keywords: 'settings provider', run: vi.fn() }])
    fireEvent.change(screen.getByLabelText('搜索命令或会话'), { target: { value: 'settings' } })
    expect(screen.queryByRole('option', { name: /模型与设置/ })).not.toBeNull()
  })

  it('jumps to a matching session by title', () => {
    const { onSelectSession } = renderPalette([])
    const input = screen.getByLabelText('搜索命令或会话')
    fireEvent.change(input, { target: { value: '下载目录' } })
    fireEvent.click(screen.getByRole('option', { name: /整理下载目录/ }))
    expect(onSelectSession).toHaveBeenCalledWith('session-aa')
  })

  it('moves selection with arrow keys and ignores empty Enter', () => {
    const runs = [vi.fn(), vi.fn()]
    renderPalette([
      { id: 'a', label: '动作甲', run: runs[0] },
      { id: 'b', label: '动作乙', run: runs[1] },
    ], { sessions: [] })
    const input = screen.getByLabelText('搜索命令或会话')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(runs[1]).toHaveBeenCalledOnce()
    expect(runs[0]).not.toHaveBeenCalled()
  })

  it('closes on Escape and backdrop click', () => {
    const { onClose } = renderPalette([])
    const input = screen.getByLabelText('搜索命令或会话')
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })
})
