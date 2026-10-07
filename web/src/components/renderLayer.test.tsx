import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionSummary } from '../api/http'
import type { ToolCallState } from '../state/conversation'
import type { ToolRecord } from '../state/projection'
import { ToolGroup } from './ToolGroup'
import { TranscriptSearch } from './TranscriptSearch'
import { WelcomePanel } from './WelcomePanel'

function toolRecord(name: string, args: Record<string, unknown>, overrides: Partial<ToolCallState> = {}): ToolRecord {
  const tool: ToolCallState = {
    toolCallId: `call-${name}`,
    name,
    arguments: args,
    status: 'completed',
    output: 'ok',
    artifacts: [],
    ...overrides,
  }
  return {
    toolCallId: tool.toolCallId,
    name: tool.name,
    args: tool.arguments,
    argSummary: name.includes('shell') ? `$ ${String(args.command ?? '')}` : String(args.path ?? ''),
    status: tool.status,
    outputPreview: tool.output,
    error: tool.status === 'failed' ? tool.output : null,
    durationMs: null,
    tool,
  }
}

describe('ToolGroup', () => {
  it('renders one summary row per record and expands the full card on click', () => {
    render(<ToolGroup tools={[
      toolRecord('run_shell', { command: 'ls -la' }),
      toolRecord('read_file', { path: '/tmp/a.txt' }),
    ]} />)

    const rows = screen.getAllByRole('button', { name: /工具调用/ })
    expect(rows).toHaveLength(2)
    expect(screen.getByText('run_shell')).toBeVisible()
    expect(screen.getByText('$ ls -la')).toBeVisible()
    expect(screen.getByText('/tmp/a.txt')).toBeVisible()
    expect(screen.queryByText('Arguments')).toBeNull()

    fireEvent.click(rows[1]!)
    expect(rows[1]).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Arguments')).toBeVisible()

    fireEvent.click(rows[1]!)
    expect(screen.queryByText('Arguments')).toBeNull()
  })

  it('marks failed records with the failed state', () => {
    render(<ToolGroup tools={[toolRecord('search_files', {}, { status: 'failed', output: 'boom' })]} />)
    const row = screen.getByRole('button', { name: '工具调用 search_files' })
    expect(row.querySelector('[data-state="failed"]')).not.toBeNull()
  })
})

describe('WelcomePanel', () => {
  const sessions: SessionSummary[] = [
    { session_id: 'session-a', title: '拿过杯子', message_count: 4, updated_at: '2026-09-10T03:00:00+00:00' },
    { session_id: 'session-b', title: '巡视客厅', message_count: 2, updated_at: '2026-09-09T03:00:00+00:00' },
  ]

  it('sends the suggestion text when a task card is clicked', () => {
    const suggest = vi.fn()
    render(<WelcomePanel recentSessions={[]} sessionTitle={session => session.title} onSuggestion={suggest} onContinue={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /巡视检查/ }))
    expect(suggest).toHaveBeenCalledWith('巡视客厅，报告有没有遗落的物品')
  })

  it('offers continue entries for recent sessions', () => {
    const cont = vi.fn()
    render(<WelcomePanel recentSessions={sessions} sessionTitle={session => session.title} onSuggestion={vi.fn()} onContinue={cont} />)

    fireEvent.click(screen.getByRole('button', { name: /拿过杯子/ }))
    expect(cont).toHaveBeenCalledWith('session-a')
  })
})

describe('TranscriptSearch', () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn()
  })

  const entries = [
    { key: 'a', text: 'hello world' },
    { key: 'b', text: 'hello again' },
    { key: 'c', text: 'something else' },
  ]

  it('jumps between matches and highlights the active row', () => {
    render(
      <div>
        <div data-search-key="a">first</div>
        <div data-search-key="b">second</div>
        <div data-search-key="c">third</div>
        <TranscriptSearch open entries={entries} onClose={vi.fn()} />
      </div>,
    )

    const input = screen.getByRole('searchbox', { name: '搜索转录内容' })
    fireEvent.change(input, { target: { value: 'hello' } })
    expect(document.querySelector('[data-search-key="a"]')).toHaveAttribute('data-search-hit', 'true')

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(document.querySelector('[data-search-key="a"]')).not.toHaveAttribute('data-search-hit')
    expect(document.querySelector('[data-search-key="b"]')).toHaveAttribute('data-search-hit', 'true')

    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(document.querySelector('[data-search-key="a"]')).toHaveAttribute('data-search-hit', 'true')
  })

  it('closes on Escape and clears the highlight', () => {
    const close = vi.fn()
    const { rerender } = render(
      <div>
        <div data-search-key="a">first</div>
        <TranscriptSearch open entries={entries} onClose={close} />
      </div>,
    )
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'hello' } })
    expect(document.querySelector('[data-search-hit]')).not.toBeNull()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(close).toHaveBeenCalledTimes(1)

    rerender(
      <div>
        <div data-search-key="a">first</div>
        <TranscriptSearch open={false} entries={entries} onClose={close} />
      </div>,
    )
    expect(document.querySelector('[data-search-hit]')).toBeNull()
  })
})
