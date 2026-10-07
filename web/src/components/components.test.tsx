import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ArtifactRef } from '../protocol/events'
import type { ApprovalState } from '../state/conversation'
import { ApprovalDialog } from './ApprovalDialog'
import { ReasoningRow } from './ReasoningRow'
import { ToolCallCard } from './ToolCallCard'

const imageArtifact: ArtifactRef = {
  artifact_handle: `hm-artifact:${'a'.repeat(32)}`,
  run_id: 'run-01',
  filename: 'frame-0004.png',
  media_type: 'image/png',
  content_sha256: 'b'.repeat(64),
}

const imageUrl = `/api/artifacts/${encodeURIComponent(imageArtifact.artifact_handle)}?session_id=session-01&run_id=run-01`

function imageTool() {
  return {
    toolCallId: 'call-01',
    name: 'robot_manipulate',
    arguments: {},
    status: 'completed' as const,
    output: 'Action completed.',
    artifacts: [imageArtifact],
  }
}

describe('ReasoningRow', () => {
  it('stays absent for empty reasoning and discloses streaming text on demand', () => {
    const { rerender } = render(<ReasoningRow text="" running />)
    expect(screen.queryByRole('button', { name: /thinking/i })).toBeNull()

    rerender(<ReasoningRow text={'first\nlatest'} running />)
    const toggle = screen.getByRole('button', { name: /thinking/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('latest')).toBeVisible()
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText((_, element) => (
      element?.tagName === 'PRE' && element.textContent === 'first\nlatest'
    ))).toBeVisible()
  })

  it('can stay expanded for a recording view', () => {
    render(<ReasoningRow text="full reasoning" running defaultExpanded />)
    expect(screen.getByRole('button', { name: /thinking/i })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText((_, element) => element?.tagName === 'PRE' && element.textContent === 'full reasoning')).toBeVisible()
  })
})

describe('ToolCallCard', () => {
  it('can keep arguments open for a recording view', () => {
    render(<ToolCallCard tool={imageTool()} defaultOpen />)
    expect(screen.getByText('Arguments').closest('details')).toHaveAttribute('open')
  })

  it('renders one independently keyed tool instance and artifact metadata', () => {
    render(<ToolCallCard tool={{
      toolCallId: 'call-01',
      name: 'search_files',
      arguments: { query: 'needle' },
      status: 'failed',
      output: 'not found',
      artifacts: [{
        artifact_handle: `hm-artifact:${'a'.repeat(32)}`,
        run_id: 'run-01',
        filename: 'result.txt',
        media_type: 'text/plain',
        content_sha256: 'b'.repeat(64),
      }],
    }} />)
    expect(screen.getByText('search_files')).toBeVisible()
    expect(screen.getByText('not found')).toBeVisible()
    expect(screen.getByText('result.txt')).toBeVisible()
    expect(screen.getByText('failed')).toBeVisible()
  })

  it('previews image artifacts and opens an accessible lightbox with the same URL', () => {
    render(<ToolCallCard sessionId="session-01" tool={imageTool()} />)

    const trigger = screen.getByRole('button', { name: 'Enlarge frame-0004.png' })
    expect(screen.getByRole('img', { name: 'frame-0004.png from robot_manipulate' })).toHaveAttribute('src', imageUrl)
    fireEvent.click(trigger)

    expect(screen.getByRole('dialog', { name: 'Image preview: frame-0004.png' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Close image preview' })).toHaveFocus()
    expect(screen.getByRole('link', { name: 'Open original' })).toHaveAttribute('href', imageUrl)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(trigger).toHaveFocus()
  })

  it('closes an image lightbox through the backdrop and close button', () => {
    render(<ToolCallCard sessionId="session-01" tool={imageTool()} />)
    const trigger = screen.getByRole('button', { name: 'Enlarge frame-0004.png' })

    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('dialog', { name: 'Image preview: frame-0004.png' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('button', { name: 'Close image preview' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('falls back to the authorized artifact link when an image cannot load', () => {
    render(<ToolCallCard sessionId="session-01" tool={imageTool()} />)
    fireEvent.error(screen.getByRole('img', { name: 'frame-0004.png from robot_manipulate' }))

    expect(screen.queryByRole('button', { name: 'Enlarge frame-0004.png' })).toBeNull()
    expect(screen.getByRole('link', { name: 'frame-0004.png' })).toHaveAttribute('href', imageUrl)
  })

  it('keeps the original link available when the enlarged image cannot load', () => {
    render(<ToolCallCard sessionId="session-01" tool={imageTool()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge frame-0004.png' }))
    const images = screen.getAllByRole('img', { name: 'frame-0004.png from robot_manipulate' })
    fireEvent.error(images.at(-1)!)

    expect(screen.getByText('Preview unavailable.')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open frame-0004.png' })).toHaveAttribute('href', imageUrl)
  })

  it('keeps non-image artifacts as authorized links', () => {
    const artifact = { ...imageArtifact, filename: 'result.txt', media_type: 'text/plain' }
    render(<ToolCallCard sessionId="session-01" tool={{ ...imageTool(), artifacts: [artifact] }} />)

    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.queryByRole('button', { name: /Enlarge/ })).toBeNull()
    expect(screen.getByRole('link', { name: 'result.txt' })).toHaveAttribute('href', imageUrl)
  })
})

const cardApproval: ApprovalState = {
  approvalId: 'approval-01',
  requestId: 'request-01',
  revision: 3,
  intentSummary: '去卧室拿杯子',
  items: [
    { item_id: 'item-cup-a', display_name: '白色杯子', location: '卧室床头柜', action_label: '拿取' },
    { item_id: 'item-enter-b', display_name: '卧室', location: '卧室', action_label: '进入' },
  ],
  expiresAt: '2026-09-10T02:00:00Z',
  requestStatus: 'awaiting_approval',
}

describe('ApprovalDialog', () => {
  it('starts with no preselected choice and keeps submit disabled until every item is decided', () => {
    render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={vi.fn()} onClose={vi.fn()} />)

    expect(screen.getByText('白色杯子 · 拿取')).toBeVisible()
    expect(screen.getByText('卧室 · 进入')).toBeVisible()
    const radios = screen.getAllByRole('radio')
    expect(radios).toHaveLength(8)
    expect(radios.every(radio => !(radio as HTMLInputElement).checked)).toBe(true)
    expect(screen.getByRole('button', { name: '提交决定' })).toBeDisabled()

    fireEvent.click(screen.getAllByRole('radio', { name: '本次允许' })[0]!)
    expect(screen.getByRole('button', { name: '提交决定' })).toBeDisabled()
  })

  it('offers 本会话允许 as a real choice submitted verbatim', () => {
    const submit = vi.fn()
    render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={submit} onClose={vi.fn()} />)

    fireEvent.click(screen.getAllByRole('radio', { name: '本会话允许' })[0]!)
    fireEvent.click(screen.getAllByRole('radio', { name: '拒绝' })[1]!)
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))

    expect(submit).toHaveBeenCalledTimes(1)
    expect(submit.mock.calls[0]![0]).toEqual({
      'item-cup-a': 'allow_session',
      'item-enter-b': 'reject',
    })
  })

  it('requires a second confirmation before 始终允许 lands as a decision', () => {
    const submit = vi.fn()
    render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={submit} onClose={vi.fn()} />)

    // First click arms the confirmation strip; the decision is not written yet.
    fireEvent.click(screen.getAllByRole('radio', { name: '始终允许' })[0]!)
    expect(screen.getByText(/将长期记住「白色杯子」的「拿取」权限/)).toBeVisible()
    fireEvent.click(screen.getAllByRole('radio', { name: '拒绝' })[1]!)
    // The armed item still holds no decision → submit stays disabled.
    expect(screen.getByRole('button', { name: '提交决定' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '确认始终允许' }))
    expect(screen.getByRole('button', { name: '提交决定' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))
    expect(submit.mock.calls[0]![0]).toEqual({
      'item-cup-a': 'allow_always',
      'item-enter-b': 'reject',
    })
  })

  it('shows per-item metadata under a collapsed details block without internal ids', () => {
    const approval: ApprovalState = {
      ...cardApproval,
      items: [{
        item_id: 'item-shell',
        display_name: 'npm test',
        location: 'npm test',
        action_label: 'shell_exec',
        arguments: { command: 'npm test', cwd: '/repo' },
      }],
    }
    render(<ApprovalDialog approval={approval} busy={false} onSubmit={vi.fn()} onClose={vi.fn()} />)

    const summary = screen.getByText('参数详情')
    const details = summary.closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(details.textContent).toContain('"command": "npm test"')
    // Internal identity keys never render, even inside metadata.
    expect(document.body.textContent).not.toContain('item-shell')
    expect(document.body.textContent).not.toContain('approval-01')
  })

  it('submits once plus reject choices with the exact item ids', () => {
    const submit = vi.fn()
    render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={submit} onClose={vi.fn()} />)

    fireEvent.click(screen.getAllByRole('radio', { name: '本次允许' })[0]!)
    fireEvent.click(screen.getAllByRole('radio', { name: '拒绝' })[1]!)
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))

    expect(submit).toHaveBeenCalledTimes(1)
    expect(submit.mock.calls[0]![0]).toEqual({
      'item-cup-a': 'allow_once',
      'item-enter-b': 'reject',
    })
    expect(typeof submit.mock.calls[0]![1]).toBe('string')
  })

  it('latches all controls after the first submit click so double-clicks cannot resubmit', () => {
    const submit = vi.fn()
    render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={submit} onClose={vi.fn()} />)

    fireEvent.click(screen.getAllByRole('radio', { name: '本次允许' })[0]!)
    fireEvent.click(screen.getAllByRole('radio', { name: '拒绝' })[1]!)
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))
    expect(submit).toHaveBeenCalledTimes(1)

    expect(screen.getByRole('button', { name: '提交中…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '关闭' })).toBeDisabled()
    for (const radio of screen.getAllByRole('radio', { name: '本次允许' })) {
      expect(radio).toBeDisabled()
    }

    fireEvent.click(screen.getByRole('button', { name: '提交中…' }))
    fireEvent.click(screen.getAllByRole('radio', { name: '拒绝' })[0]!)
    expect(submit).toHaveBeenCalledTimes(1)
  })

  it('releases the latch after a failed submission so the user can retry', () => {
    const submit = vi.fn()
    const { rerender } = render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={submit} onClose={vi.fn()} />)

    fireEvent.click(screen.getAllByRole('radio', { name: '本次允许' })[0]!)
    fireEvent.click(screen.getAllByRole('radio', { name: '本次允许' })[1]!)
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))
    expect(screen.getByRole('button', { name: '提交中…' })).toBeDisabled()

    rerender(<ApprovalDialog approval={cardApproval} busy onSubmit={submit} onClose={vi.fn()} />)
    rerender(<ApprovalDialog approval={cardApproval} busy={false} error="network down" onSubmit={submit} onClose={vi.fn()} />)

    expect(screen.getByRole('alert')).toHaveTextContent('network down')
    expect(screen.getByRole('button', { name: '提交决定' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))
    expect(submit).toHaveBeenCalledTimes(2)
  })

  it('formats shell-like items as a mono $ command and file-like items path-first', () => {
    const shellApproval: ApprovalState = {
      ...cardApproval,
      items: [
        { item_id: 'item-shell', display_name: 'ls -la /tmp', location: 'ls -la /tmp', action_label: 'shell_exec' },
        { item_id: 'item-file', display_name: 'config.yaml', location: '/etc/config.yaml', action_label: 'write_file' },
      ],
    }
    render(<ApprovalDialog approval={shellApproval} busy={false} onSubmit={vi.fn()} onClose={vi.fn()} />)

    expect(screen.getByText('$ ls -la /tmp')).toBeVisible()
    const path = screen.getByText('/etc/config.yaml')
    expect(path.tagName).toBe('CODE')
    // The metadata block lives at item level (fieldset), collapsed by default.
    const details = path.closest('fieldset')!.querySelector('details')
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute('open')
  })

  it('treats Escape and the close button as cancellation without submitting', () => {
    const submit = vi.fn()
    const close = vi.fn()
    const { rerender } = render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={submit} onClose={close} />)

    expect(screen.getByRole('button', { name: '关闭' })).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(close).toHaveBeenCalledTimes(1)
    expect(typeof close.mock.calls[0]![0]).toBe('string')
    expect(submit).not.toHaveBeenCalled()

    // The cancel click latched the dialog — a follow-up click cannot re-fire.
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(close).toHaveBeenCalledTimes(1)

    rerender(<ApprovalDialog approval={{ ...cardApproval, approvalId: 'approval-02' }} busy={false} onSubmit={submit} onClose={close} />)
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(close).toHaveBeenCalledTimes(2)
    expect(submit).not.toHaveBeenCalled()
  })

  it('never renders internal ids, revisions, directories or tool payloads', () => {
    render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={vi.fn()} onClose={vi.fn()} />)

    const text = document.body.textContent ?? ''
    expect(text).not.toContain('item-cup-a')
    expect(text).not.toContain('item-enter-b')
    expect(text).not.toContain('approval-01')
    expect(text).not.toContain('request-01')
  })
})
