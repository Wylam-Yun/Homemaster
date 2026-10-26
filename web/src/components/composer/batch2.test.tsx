import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ProviderInfo, ProviderModels, SessionStatusInfo } from '../../api/http'
import type { PendingQuestionState } from '../../state/conversation'
import { ContextMeter, FALLBACK_CONTEXT_TOKENS } from './ContextMeter'
import { ModeBadge } from './ModeBadge'
import { ModelPicker } from './ModelPicker'
import { QueueDock } from './QueueDock'
import { ApprovalWaitingCard, QuestionCard } from './TakeoverCard'

const PROVIDERS: ProviderInfo[] = [
  { name: 'primary', kind: 'anthropic', model: 'claude-sonnet-4', api_key_configured: true },
  { name: 'backup', kind: 'openai', model: 'gpt-5', api_key_configured: false },
]

const STATUS: SessionStatusInfo = {
  session_id: 's1', generation: 3, revision: 7, status: 'idle',
  active: false, cancellation_requested: false, task_status: 'active',
  environment_ref: null, ui_mode: 'act',
}

describe('ModelPicker', () => {
  it('lists configured providers and disables entries without an API key', () => {
    render(<ModelPicker providers={PROVIDERS} value={{}} onChange={vi.fn()} />)

    const select = screen.getByRole('combobox', { name: '服务商' })
    const options = Array.from(select.querySelectorAll('option'))
    expect(options.map(option => option.textContent)).toEqual([
      '默认',
      'primary · claude-sonnet-4',
      'backup · gpt-5（未配置 Key）',
    ])
    expect(options[2]).toBeDisabled()
  })

  it('emits provider_name on selection and clears on 默认', () => {
    const onChange = vi.fn()
    render(<ModelPicker providers={PROVIDERS} value={{}} onChange={onChange} />)

    fireEvent.change(screen.getByRole('combobox', { name: '服务商' }), { target: { value: 'primary' } })
    expect(onChange).toHaveBeenCalledWith({ provider_name: 'primary' })
  })

  it('supports a free-text custom model id via 自定义…', async () => {
    const onChange = vi.fn()
    render(<ModelPicker providers={PROVIDERS} value={{ provider_name: 'primary' }} onChange={onChange} />)

    fireEvent.change(screen.getByRole('combobox', { name: '模型' }), { target: { value: '__custom__' } })
    const input = await screen.findByRole('textbox', { name: '自定义 model id' })
    fireEvent.change(input, { target: { value: 'claude-opus-4.5' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onChange).toHaveBeenLastCalledWith({ provider_name: 'primary', model: 'claude-opus-4.5' })
  })

  it('keeps a stale provider visible but disabled as an unavailable option', () => {
    render(<ModelPicker providers={PROVIDERS} value={{ provider_name: 'gone', model: 'x' }} onChange={vi.fn()} />)
    const option = screen.getByRole('option', { name: /gone（已不可用）/ })
    expect(option).toBeInTheDocument()
    expect(option).toBeDisabled()
  })

  it('retries a failed model enumeration on demand', async () => {
    const loadModels = vi.fn<(name: string) => Promise<ProviderModels>>()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue({ name: 'primary', models: ['claude-opus-4.5'], source: 'live' })
    render(
      <ModelPicker
        providers={PROVIDERS}
        value={{ provider_name: 'primary' }}
        onChange={vi.fn()}
        loadModels={loadModels}
      />,
    )

    await screen.findByText(/在线枚举失败/)
    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    await screen.findByRole('option', { name: 'claude-opus-4.5' })
    expect(loadModels).toHaveBeenCalledTimes(2)
  })

  it('shows a stale custom model as its own option instead of duplicating 自定义…', async () => {
    const loadModels = vi.fn<(name: string) => Promise<ProviderModels>>()
      .mockResolvedValue({ name: 'primary', models: ['claude-sonnet-4'], source: 'live' })
    render(
      <ModelPicker
        providers={PROVIDERS}
        value={{ provider_name: 'primary', model: 'claude-opus-4.5' }}
        onChange={vi.fn()}
        loadModels={loadModels}
      />,
    )

    await screen.findByRole('option', { name: 'claude-opus-4.5' })
    const select = screen.getByRole('combobox', { name: '模型' })
    // Exactly one option carries the editor sentinel value.
    const sentinels = Array.from(select.querySelectorAll('option'))
      .filter(option => option.value === '__custom__')
    expect(sentinels).toHaveLength(1)
    expect(sentinels[0].textContent).toBe('自定义…')
  })

  it('lazily enumerates live models for the selected provider and emits them on pick', async () => {
    const loadModels = vi.fn<(name: string) => Promise<ProviderModels>>()
      .mockResolvedValue({ name: 'primary', models: ['claude-sonnet-4', 'claude-opus-4.5'], source: 'live' })
    const onChange = vi.fn()
    render(
      <ModelPicker
        providers={PROVIDERS}
        value={{ provider_name: 'primary' }}
        onChange={onChange}
        loadModels={loadModels}
      />,
    )

    await waitFor(() => { expect(loadModels).toHaveBeenCalledWith('primary') })
    const select = await screen.findByRole('combobox', { name: '模型' })
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'claude-opus-4.5' })).toBeInTheDocument()
    })
    // Configured model stays as the 默认 option; the live list adds the rest.
    expect(select.querySelectorAll('option').length).toBeGreaterThanOrEqual(3)

    fireEvent.change(select, { target: { value: 'claude-opus-4.5' } })
    expect(onChange).toHaveBeenLastCalledWith({ provider_name: 'primary', model: 'claude-opus-4.5' })
  })

  it('caches enumeration per provider instead of refetching on rerender', async () => {
    const loadModels = vi.fn<(name: string) => Promise<ProviderModels>>()
      .mockResolvedValue({ name: 'primary', models: ['m1'], source: 'live' })
    const { rerender } = render(
      <ModelPicker providers={PROVIDERS} value={{ provider_name: 'primary' }} onChange={vi.fn()} loadModels={loadModels} />,
    )
    await waitFor(() => { expect(loadModels).toHaveBeenCalledTimes(1) })
    rerender(
      <ModelPicker providers={PROVIDERS} value={{ provider_name: 'primary' }} onChange={vi.fn()} loadModels={loadModels} />,
    )
    await screen.findByRole('option', { name: 'm1' })
    expect(loadModels).toHaveBeenCalledTimes(1)
  })

  it('marks a fallback enumeration as unavailable instead of listing template models', async () => {
    const loadModels = vi.fn<(name: string) => Promise<ProviderModels>>()
      .mockResolvedValue({ name: 'primary', models: ['claude-sonnet-4', 'template-x'], source: 'fallback' })
    render(
      <ModelPicker
        providers={PROVIDERS}
        value={{ provider_name: 'primary' }}
        onChange={vi.fn()}
        loadModels={loadModels}
      />,
    )

    await screen.findByText(/在线枚举不可用/)
    // Fallback models are misleading templates: only 默认 + 自定义… remain.
    expect(screen.queryByRole('option', { name: 'template-x' })).toBeNull()
    expect(screen.getByRole('option', { name: '自定义…' })).toBeInTheDocument()
  })
})

describe('ModeBadge', () => {
  it('toggles plan/act on click with the pressed state on plan', () => {
    const onToggle = vi.fn()
    const { rerender } = render(<ModeBadge mode="act" onToggle={onToggle} />)

    const act = screen.getByRole('button', { name: '执行' })
    const plan = screen.getByRole('button', { name: '规划' })
    expect(act).toHaveAttribute('aria-pressed', 'true')
    expect(plan).toHaveAttribute('aria-pressed', 'false')

    // Clicking the already-active option is a no-op; the other option toggles.
    fireEvent.click(act)
    expect(onToggle).not.toHaveBeenCalled()
    fireEvent.click(plan)
    expect(onToggle).toHaveBeenCalledWith('plan')

    rerender(<ModeBadge mode="plan" onToggle={onToggle} />)
    expect(screen.getByRole('button', { name: '规划' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '执行' }))
    expect(onToggle).toHaveBeenLastCalledWith('act')
  })
})

describe('ContextMeter', () => {
  it('renders usage percentage and hover details with status fields', () => {
    render(<ContextMeter usage={{ total_tokens: 50_000 }} status={STATUS} onCompact={vi.fn()} />)

    expect(screen.getByLabelText('上下文用量 25%')).toBeInTheDocument()
    expect(screen.getByText('50.0k')).toBeVisible()
    const meter = screen.getByLabelText('上下文用量 25%').closest('div')!
    expect(meter.title).toContain('generation 3')
    expect(meter.title).toContain('revision 7')
    expect(meter.title).toContain('task active')
  })

  it('prefers per-request context_tokens over cumulative billing totals', () => {
    // Regression: cumulative total_tokens made the ring look like the
    // context window exploded; context_tokens is the real occupancy.
    render(
      <ContextMeter
        usage={{ total_tokens: 500_000, context_tokens: 50_000 }}
        status={null}
        onCompact={vi.fn()}
      />,
    )
    expect(screen.getByLabelText('上下文用量 25%')).toBeInTheDocument()
    expect(screen.getByText('50.0k')).toBeVisible()
  })

  it('clamps the ring at 100% and flags the high level', () => {
    render(<ContextMeter usage={{ total_tokens: FALLBACK_CONTEXT_TOKENS * 2 }} status={null} onCompact={vi.fn()} />)
    expect(screen.getByLabelText('上下文用量 100%')).toBeInTheDocument()
  })

  it('routes the compact button to onCompact', () => {
    const onCompact = vi.fn()
    render(<ContextMeter usage={null} status={null} onCompact={onCompact} />)
    fireEvent.click(screen.getByRole('button', { name: '压缩上下文' }))
    expect(onCompact).toHaveBeenCalledTimes(1)
  })
})

describe('QueueDock', () => {
  const items = [
    { id: 'a', text: '第一条排队消息' },
    { id: 'b', text: '第二条排队消息，带很长很长很长很长很长很长很长很长很长很长的后缀' },
  ]

  it('renders nothing when the queue is empty', () => {
    const { container } = render(<QueueDock items={[]} onRemove={vi.fn()} onClear={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists queued previews with per-item remove and a clear-all button', () => {
    const onRemove = vi.fn()
    const onClear = vi.fn()
    render(<QueueDock items={items} onRemove={onRemove} onClear={onClear} />)

    expect(screen.getByText('排队 2 条')).toBeVisible()
    expect(screen.getByText('第一条排队消息')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '移除排队消息 2' }))
    expect(onRemove).toHaveBeenCalledWith('b')
    fireEvent.click(screen.getByRole('button', { name: '清空' }))
    expect(onClear).toHaveBeenCalledTimes(1)
  })
})

const QUESTION: PendingQuestionState = {
  questionId: 'q-01',
  sessionId: 'session-01',
  requestId: 'request-01',
  question: '杯子要什么颜色？',
  toolCallId: 'call-9',
}

describe('TakeoverCard', () => {
  it('renders the question, submits the trimmed answer on Enter and locks while in flight', async () => {
    let resolveAnswer: (ok: boolean) => void = () => {}
    const onSubmit = vi.fn(() => new Promise<boolean>(resolve => { resolveAnswer = resolve }))
    render(<QuestionCard question={QUESTION} onSubmit={onSubmit} />)

    expect(screen.getByText('杯子要什么颜色？')).toBeVisible()
    const input = screen.getByRole('textbox', { name: '回答 Agent 的问题' })
    fireEvent.change(input, { target: { value: '  蓝色  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onSubmit).toHaveBeenCalledWith('q-01', '蓝色')
    expect(input).toBeDisabled()
    await waitFor(() => { expect(screen.getByRole('button', { name: '提交中…' })).toBeInTheDocument() })
    await waitFor(() => { resolveAnswer(false) })
    await waitFor(() => { expect(input).toBeEnabled() })
  })

  it('keeps the answer editable when submission fails', async () => {
    const onSubmit = vi.fn(async () => false)
    render(<QuestionCard question={QUESTION} onSubmit={onSubmit} />)

    const input = screen.getByRole('textbox', { name: '回答 Agent 的问题' })
    fireEvent.change(input, { target: { value: '再想想' } })
    fireEvent.click(screen.getByRole('button', { name: '回答' }))

    await waitFor(() => { expect(onSubmit).toHaveBeenCalledTimes(1) })
    await waitFor(() => { expect(input).toBeEnabled() })
    expect((input as HTMLInputElement).value).toBe('再想想')
  })

  it('shows a non-interactive waiting card for pending approvals', () => {
    render(<ApprovalWaitingCard itemCount={3} />)
    expect(screen.getByText('等待审批')).toBeVisible()
    expect(screen.getByText(/3 项申请待决定/)).toBeVisible()
    expect(screen.queryByRole('textbox')).toBeNull()
  })
})
