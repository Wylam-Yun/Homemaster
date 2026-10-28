import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type {
  SkillEvolutionDetail,
  SkillEvolutionRow,
  SkillVersionContent,
} from '../api/http'
import { SkillEvolutionPage } from './SkillEvolutionPage'

const row = (over: Partial<SkillEvolutionRow>): SkillEvolutionRow => ({
  cloud_skill_id: 'cs-1',
  skill_id: 'housekeeping',
  evolution_enabled: true,
  invocation_count: 9,
  unconsumed_count: 1,
  batch_size: 8,
  batches_count: 1,
  last_batch_at: 1793170086,
  last_draft_version_id: 'v2',
  ...over,
})

const detail: SkillEvolutionDetail = {
  cloud_skill_id: 'cs-1',
  skill_id: 'housekeeping',
  evolution_enabled: true,
  unconsumed_count: 1,
  batch_size: 8,
  invocations: [
    {
      invocation_id: 'inv-1',
      status: 'consumed',
      score: 0.9,
      outcome: 'success',
      task_input: '整理客厅',
      sealed_at: 1793169486,
      evolution_batch_id: 'b-1',
      skill_version_id: 'v1',
      steps: [
        {
          tool_call_id: 'call-1', tool_name: 'load_skill', arguments: { name: 'housekeeping' },
          result_tail: 'loaded housekeeping', result_truncated: false, status: 'success',
        },
      ],
    },
  ],
  batches: [
    {
      batch_id: 'b-1',
      status: 'completed',
      created_at: 1793170086,
      trace_ids: ['t-1'],
      job: { job_id: 'j-1', status: 'completed', attempts: 1, error: null },
      draft_version_id: 'v2',
    },
  ],
  versions: [
    { version_id: 'v2', parent_version_id: 'v1', status: 'draft', version_label: 'evo-1', content_hash: 'h2' },
    { version_id: 'v1', parent_version_id: null, status: 'observed', version_label: null, content_hash: 'h1' },
  ],
  versions_unavailable: false,
}

const content: SkillVersionContent = {
  version_id: 'v2',
  status: 'draft',
  parent_version_id: 'v1',
  content: '---\nname: housekeeping\n---\n先扫客厅再扫厨房',
  parent: {
    version_id: 'v1',
    content: '---\nname: housekeeping\n---\n先扫厨房',
  },
}

function renderPage(over: Partial<Parameters<typeof SkillEvolutionPage>[0]> = {}) {
  const props = {
    loadOverview: vi.fn().mockResolvedValue({ skills: [row({})] }),
    loadDetail: vi.fn().mockResolvedValue(detail),
    loadContent: vi.fn().mockResolvedValue(content),
    ...over,
  }
  render(<SkillEvolutionPage {...props} />)
  return props
}

describe('SkillEvolutionPage', () => {
  it('lists skills with trace progress and draft badge', async () => {
    renderPage()
    expect(await screen.findByText('housekeeping')).toBeInTheDocument()
    expect(screen.getByText('1/8 轨迹')).toBeInTheDocument()
    expect(screen.getByText('1 批次')).toBeInTheDocument()
    expect(screen.getByText('draft')).toBeInTheDocument()
  })

  it('shows invocations with expandable tool steps and batch history', async () => {
    const { loadDetail } = renderPage()
    fireEvent.click(await screen.findByText('housekeeping'))

    expect(loadDetail).toHaveBeenCalledWith('cs-1')
    expect(await screen.findByText('score 0.9')).toBeInTheDocument()
    expect(screen.getByText('整理客厅')).toBeInTheDocument()
    expect(screen.getByText('1 步')).toBeInTheDocument()

    fireEvent.click(screen.getByText('consumed'))
    expect(await screen.findByText('load_skill')).toBeInTheDocument()
    expect(screen.getByText('loaded housekeeping')).toBeInTheDocument()
    expect(screen.getByText('parent v1')).toBeInTheDocument()
  })

  it('renders the draft-vs-parent line diff when a batch is expanded', async () => {
    const { loadContent } = renderPage()
    fireEvent.click(await screen.findByText('housekeeping'))
    fireEvent.click(await screen.findByText('草稿 diff'))

    expect(loadContent).toHaveBeenCalledWith('cs-1', 'v2')
    expect(await screen.findByText(/- 先扫厨房/)).toBeInTheDocument()
    expect(screen.getByText(/\+ 先扫客厅再扫厨房/)).toBeInTheDocument()
    expect(screen.getByText(/只读，不会自动激活/)).toBeInTheDocument()
  })

  it('marks versions unavailable without failing the page', async () => {
    renderPage({
      loadDetail: vi.fn().mockResolvedValue({ ...detail, versions: null, versions_unavailable: true }),
    })
    fireEvent.click(await screen.findByText('housekeeping'))
    expect(await screen.findByText('服务端版本列表不可用。')).toBeInTheDocument()
  })

  it('shows the unavailable panel when the memory tier is off', () => {
    renderPage({ unavailable: true })
    expect(screen.getByText('Skill Evolution 观测不可用')).toBeInTheDocument()
  })
})
