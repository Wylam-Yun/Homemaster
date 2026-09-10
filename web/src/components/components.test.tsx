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
    expect(radios).toHaveLength(6)
    expect(radios.every(radio => !(radio as HTMLInputElement).checked)).toBe(true)
    expect(screen.getByRole('button', { name: '提交决定' })).toBeDisabled()

    fireEvent.click(screen.getAllByRole('radio', { name: '本次允许' })[0]!)
    expect(screen.getByRole('button', { name: '提交决定' })).toBeDisabled()
  })

  it('submits one always plus one once choice with the exact item ids', () => {
    const submit = vi.fn()
    render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={submit} onClose={vi.fn()} />)

    fireEvent.click(screen.getAllByRole('radio', { name: '始终允许' })[0]!)
    fireEvent.click(screen.getAllByRole('radio', { name: '本次允许' })[1]!)
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))

    expect(submit).toHaveBeenCalledTimes(1)
    expect(submit.mock.calls[0]![0]).toEqual({
      'item-cup-a': 'allow_always',
      'item-enter-b': 'allow_once',
    })
    expect(typeof submit.mock.calls[0]![1]).toBe('string')
  })

  it('locks a new submission id after the user changes a choice', () => {
    const submit = vi.fn()
    render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={submit} onClose={vi.fn()} />)

    fireEvent.click(screen.getAllByRole('radio', { name: '始终允许' })[0]!)
    fireEvent.click(screen.getAllByRole('radio', { name: '本次允许' })[1]!)
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))
    const firstId = submit.mock.calls[0]![1] as string

    fireEvent.click(screen.getAllByRole('radio', { name: '拒绝' })[1]!)
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))
    const secondId = submit.mock.calls[1]![1] as string

    expect(submit.mock.calls[1]![0]).toEqual({
      'item-cup-a': 'allow_always',
      'item-enter-b': 'reject',
    })
    expect(secondId).not.toBe(firstId)
  })

  it('treats Escape and the close button as cancellation without submitting', () => {
    const submit = vi.fn()
    const close = vi.fn()
    render(<ApprovalDialog approval={cardApproval} busy={false} onSubmit={submit} onClose={close} />)

    expect(screen.getByRole('button', { name: '关闭' })).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(close).toHaveBeenCalledTimes(1)
    expect(typeof close.mock.calls[0]![0]).toBe('string')
    expect(submit).not.toHaveBeenCalled()

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
