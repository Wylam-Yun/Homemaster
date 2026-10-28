import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { DreamingSessionDetail, DreamingSessionRow } from '../api/http'
import { DreamingPage } from './DreamingPage'

const row = (over: Partial<DreamingSessionRow>): DreamingSessionRow => ({
  session_id: 's-1',
  title: '爬山计划',
  updated_at: '2026-10-28T10:00:00Z',
  archive_state: 'completed',
  archive_count: 1,
  last_archive_at: 1793170686,
  ...over,
})

const detail: DreamingSessionDetail = {
  session_id: 's-1',
  archive_state: 'completed',
  session_excerpts: [{ turn_id: 'turn-3', preview: '我下周去爬山' }],
  archives: [
    {
      archive_id: 'archive:s-1:10',
      state: 'completed',
      turn_range: [0, 9],
      max_event_seq: 10,
      created_at: 1793170026,
      job: {
        job_id: 'dj-1',
        status: 'completed',
        attempts: 1,
        error: null,
        dreaming_record_id: 'dr-1',
        raw_verified: true,
        missing_ids: [],
        extracted_count: 1,
      },
      seeds: [
        { operation_id: 'op-1', turn_id: 'turn-3', preview: '我下周去爬山', missing: false },
      ],
      actions: [
        {
          action_id: 'a:0',
          action: 'update',
          memory_id: 'm-1',
          memory_session_id: 's-1',
          before: { content: '喜欢游泳', status: 'archived' },
          after: { content: '喜欢爬山', status: 'active' },
          missing: false,
        },
      ],
      diffs_unavailable: false,
    },
  ],
}

describe('DreamingPage', () => {
  it('lists sessions with state badges and filters them', async () => {
    const loadSessions = vi.fn().mockResolvedValue({
      sessions: [
        row({ session_id: 's-1', title: '爬山计划', archive_state: 'completed' }),
        row({ session_id: 's-2', title: '活跃会话', archive_state: 'unarchived', archive_count: 0 }),
        row({ session_id: 's-3', title: '整理中', archive_state: 'processing' }),
        row({ session_id: 's-4', title: '多归档', archive_state: 'completed', archive_count: 2 }),
      ],
    })
    render(<DreamingPage loadSessions={loadSessions} loadDetail={vi.fn()} pollMs={0} />)

    expect(await screen.findByText('爬山计划')).toBeInTheDocument()
    expect(screen.getByText('活跃会话')).toBeInTheDocument()
    expect(screen.getByText('×2')).toBeInTheDocument()
    expect(screen.getAllByText('已归档').length).toBeGreaterThanOrEqual(2)

    fireEvent.click(screen.getByLabelText('未归档'))
    expect(screen.queryByText('活跃会话')).not.toBeInTheDocument()
    expect(screen.getByText('爬山计划')).toBeInTheDocument()
  })

  it('shows per-archive seeds and one-to-one before/after diffs', async () => {
    const loadSessions = vi.fn().mockResolvedValue({ sessions: [row({})] })
    const loadDetail = vi.fn().mockResolvedValue(detail)
    render(<DreamingPage loadSessions={loadSessions} loadDetail={loadDetail} pollMs={0} />)

    fireEvent.click(await screen.findByText('爬山计划'))

    expect(await screen.findByText('喜欢游泳')).toBeInTheDocument()
    expect(screen.getByText('喜欢爬山')).toBeInTheDocument()
    expect(screen.getAllByText('我下周去爬山').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(/turns 0–9/)).toBeInTheDocument()
    expect(loadDetail).toHaveBeenCalledWith('s-1')
  })

  it('renders the unarchived empty state with session excerpts', async () => {
    const unarchived: DreamingSessionDetail = {
      session_id: 's-2',
      archive_state: 'unarchived',
      session_excerpts: [{ turn_id: 'turn-1', preview: '你好' }],
      archives: [],
    }
    const loadSessions = vi.fn().mockResolvedValue({
      sessions: [row({ session_id: 's-2', title: '活跃会话', archive_state: 'unarchived', archive_count: 0, last_archive_at: null })],
    })
    render(
      <DreamingPage loadSessions={loadSessions} loadDetail={vi.fn().mockResolvedValue(unarchived)} pollMs={0} />,
    )

    fireEvent.click(await screen.findByText('活跃会话'))
    expect(await screen.findByText('你好')).toBeInTheDocument()
    expect(await screen.findByText(/尚未归档/)).toBeInTheDocument()
  })

  it('shows the unavailable panel when the memory tier is off', () => {
    render(<DreamingPage loadSessions={vi.fn()} loadDetail={vi.fn()} unavailable />)
    expect(screen.getByText('Dreaming 观测不可用')).toBeInTheDocument()
  })
})
