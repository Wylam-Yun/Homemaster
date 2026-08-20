import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApprovalDialog } from './ApprovalDialog'
import { ReasoningRow } from './ReasoningRow'
import { ToolCallCard } from './ToolCallCard'

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
})

describe('ToolCallCard', () => {
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
