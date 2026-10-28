import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { FeedbackRun, FeedbackRunDetail } from '../api/http'
import { FeedbackRunsPage } from './FeedbackRunsPage'

const run: FeedbackRun = {
  job_id: 'job-1',
  trigger: 'manual',
  status: 'completed',
  window: { window_start: '2026-10-25T12:00:00+00:00', window_end: '2026-10-28T12:00:00+00:00' },
  created_at: 1793170026,
  updated_at: 1793170686,
  actions_count: 1,
  error: null,
}

const detail: FeedbackRunDetail = {
  ...run,
  sessions: [
    {
      session_id: 'session-1',
      records_count: 2,
      excerpts: [
        { add_record_id: 'r1', submitted_at: '2026-10-27T10:00:00+00:00', preview: '其实我搬苏州了' },
      ],
    },
  ],
  actions: [
    {
      action_id: 'r:0',
      action: 'update',
      memory_id: 'm-1',
      memory_session_id: 'session-1',
      before: { content: '住杭州', status: 'archived' },
      after: { content: '住苏州', status: 'active' },
      missing: false,
    },
  ],
}

describe('FeedbackRunsPage', () => {
  it('lists runs and shows scanned sessions plus before/after diffs', async () => {
    const loadRuns = vi.fn().mockResolvedValue({ runs: [run] })
    const loadDetail = vi.fn().mockResolvedValue(detail)
    render(
      <FeedbackRunsPage
        loadRuns={loadRuns}
        loadDetail={loadDetail}
        sessions={[{ session_id: 'session-1', title: '配眼镜咨询' }]}
        pollMs={0}
      />,
    )

    const card = await screen.findByRole('button', { name: /手动/ })
    fireEvent.click(card)

    expect(await screen.findByText('配眼镜咨询')).toBeInTheDocument()
    expect(screen.getByText('住杭州')).toBeInTheDocument()
    expect(screen.getByText('住苏州')).toBeInTheDocument()
    expect(screen.getByText('更新')).toBeInTheDocument()

    // Excerpts expand on click to show the consumed conversation evidence.
    fireEvent.click(screen.getByRole('button', { name: /配眼镜咨询/ }))
    expect(await screen.findByText('其实我搬苏州了')).toBeInTheDocument()
    expect(loadDetail).toHaveBeenCalledWith('job-1')
  })

  it('keeps polling an in-flight run until terminal', async () => {
    const loadRuns = vi.fn().mockResolvedValue({ runs: [{ ...run, status: 'processing' }] })
    const loadDetail = vi
      .fn()
      .mockResolvedValueOnce({ ...detail, status: 'processing', sessions: [] })
      .mockResolvedValue({ ...detail, status: 'completed' })
    render(
      <FeedbackRunsPage
        loadRuns={loadRuns}
        loadDetail={loadDetail}
        pollMs={0}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: /整理中|完成/ }))

    await waitFor(() => expect(loadDetail).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('住苏州')).toBeInTheDocument()
  })
})
