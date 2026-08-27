import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ArtifactRef } from '../protocol/events'
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

describe('ApprovalDialog', () => {
  it('focuses the reject-safe action and handles Escape as rejection', () => {
    const reject = vi.fn()
    render(<ApprovalDialog approval={{
      approvalId: 'approval-01',
      toolCallId: 'call-01',
      name: 'write_file',
      arguments: { path: 'important.txt' },
      cwd: '/workspace',
      reason: 'confirmation required',
    }} busy={false} onApprove={vi.fn()} onReject={reject} />)

    expect(screen.getByRole('button', { name: 'Reject' })).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(reject).toHaveBeenCalledTimes(1)
  })
})
