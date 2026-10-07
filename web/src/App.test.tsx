import { act, render, screen } from '@testing-library/react'
import { fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  rejectMemories: false,
  stop: vi.fn(),
  emitters: [] as Array<{ onEvent: (event: unknown) => void }>,
  apis: [] as Array<{
    submitApproval: (...args: Array<unknown>) => Promise<unknown>
    cancelApproval: (...args: Array<unknown>) => Promise<unknown>
  }>,
}))

vi.mock('./api/http', () => ({
  HttpError: class HttpError extends Error {},
  HomeMasterApi: class HomeMasterApi {
    constructor() {
      mocks.apis.push(this as unknown as {
        submitApproval: (...args: Array<unknown>) => Promise<unknown>
        cancelApproval: (...args: Array<unknown>) => Promise<unknown>
      })
    }

    listSessions = vi.fn().mockResolvedValue({ sessions: [{ session_id: 'session-01', title: 'first request title', message_count: 2, updated_at: '2026-09-10T03:00:00+00:00' }] })
    history = vi.fn().mockResolvedValue({ session_id: 'session-01', messages: [] })
    createSession = vi.fn().mockResolvedValue({ session_id: 'session-new' })
    sendMessage = vi.fn().mockResolvedValue({ accepted: true })
    cancel = vi.fn().mockResolvedValue({ cancelled: true })
    providers = vi.fn().mockResolvedValue({ providers: [
      { name: 'primary', kind: 'anthropic', model: 'claude-sonnet-4', api_key_configured: true },
      { name: 'backup', kind: 'openai', model: 'gpt-5', api_key_configured: false },
    ] })
    meta = vi.fn().mockResolvedValue({ version: '35.0.0', memory_mode: 'files', environment: null })
    sessionStatus = vi.fn().mockResolvedValue({
      session_id: 'session-01', generation: 2, revision: 5, status: 'idle',
      active: false, cancellation_requested: false, task_status: null,
      environment_ref: null, ui_mode: 'act',
    })
    compactSession = vi.fn().mockResolvedValue({
      session_id: 'session-01', generation: 2, revision: 6, triggered: true, kind: 'manual',
    })
    setUiMode = vi.fn().mockResolvedValue({ session_id: 'session-01', ui_mode: 'plan' })
    pendingApprovals = vi.fn().mockResolvedValue({ approvals: [] })
    pendingQuestions = vi.fn().mockResolvedValue({ questions: [] })
    answerQuestion = vi.fn().mockResolvedValue({ question_id: 'q-01', accepted: true })
    submitApproval = vi.fn().mockResolvedValue({
      approval_id: 'approval-01',
      request_id: 'request-01',
      request_status: 'ready',
      execution_started: true,
      persisted_grant_ids: [],
      items: [],
    })
    cancelApproval = vi.fn().mockResolvedValue({ approval_id: 'approval-01', request_status: 'cancelled' })
    readApproval = vi.fn().mockResolvedValue(null)
    listGrants = vi.fn().mockResolvedValue({ grants: [], next_cursor: null })
    revokeGrant = vi.fn().mockResolvedValue(null)
    memoryHistory = vi.fn().mockResolvedValue({ memory_id: 'memory-01', versions: [] })
    memories = vi.fn().mockImplementation(() => {
      if (mocks.rejectMemories) return Promise.reject(new Error('memory unavailable'))
      return Promise.resolve({
        stats: { active_count: 1, archived_count: 0, total_count: 1, session_group_count: 1 },
        groups: [],
      })
    })
  },
}))

vi.mock('./api/connection', () => ({
  EventConnection: class EventConnection {
    constructor(
      _sessionId: string,
      _url: undefined,
      private readonly callbacks: {
        onEvent: (event: unknown) => void
        onStateChange: (state: string) => void
        onReject: () => void
      },
    ) {
      mocks.emitters.push({ onEvent: callbacks.onEvent })
    }

    start() { this.callbacks.onStateChange('connected') }
    stop() { mocks.stop() }
  },
}))

import { App } from './App'


describe('App memory navigation', () => {
  beforeEach(() => {
    mocks.rejectMemories = false
    mocks.stop.mockClear()
    mocks.emitters.length = 0
    localStorage.clear()
    window.HTMLElement.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    window.history.replaceState({}, '', '/')
    vi.clearAllMocks()
  })

  it('keeps conversation usable when memory loading fails', async () => {
    mocks.rejectMemories = true
    render(<App />)

    expect(await screen.findByRole('button', { name: '对话' })).toBeVisible()
    expect(await screen.findByPlaceholderText('Message…')).toBeEnabled()
  })

  it('switches to memory view and collapses history without changing the session', async () => {
    render(<App />)

    expect(await screen.findByRole('button', { name: '打开会话 first request title' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '折叠历史会话' }))
    expect(screen.queryByRole('button', { name: '打开会话 first request title' })).not.toBeInTheDocument()
    expect(localStorage.getItem('homemaster:web:history-collapsed')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: '记忆管理' }))
    expect(await screen.findByRole('heading', { name: '记忆管理' })).toBeVisible()
    expect(mocks.stop).not.toHaveBeenCalled()
    await waitFor(() => { expect(screen.getByText('记忆总数')).toBeVisible() })
  })

  it('attaches a recording view to the requested session', async () => {
    window.history.replaceState({}, '', '/?record=1&session_id=session-01')
    render(<App />)

    expect(await screen.findByRole('button', { name: '打开会话 first request title' })).toBeVisible()
    expect(document.querySelector('.shell')).toHaveAttribute('data-recording', 'true')
    expect(await screen.findByPlaceholderText('Message…')).toBeEnabled()
    window.history.replaceState({}, '', '/')
  })

  it('filters the session list through the search box', async () => {
    render(<App />)

    expect(await screen.findByRole('button', { name: '打开会话 first request title' })).toBeVisible()
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索会话' }), { target: { value: 'zzz-no-match' } })
    expect(screen.queryByRole('button', { name: '打开会话 first request title' })).not.toBeInTheDocument()
    expect(screen.getByText('没有匹配的会话')).toBeVisible()
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索会话' }), { target: { value: 'first request' } })
    expect(screen.getByRole('button', { name: '打开会话 first request title' })).toBeVisible()
  })

  it('opens the permissions view and reads the grant list from the backend', async () => {
    render(<App />)

    expect(await screen.findByRole('button', { name: '打开会话 first request title' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '权限' }))
    expect(await screen.findByRole('heading', { name: '权限管理' })).toBeVisible()
    expect(await screen.findByText(/暂无长期权限/)).toBeVisible()
    expect(screen.getByText(/区域权限只检查目的地，不限制途经区域/)).toBeVisible()
  })

  it('keeps the composer text and shows a notice when sending fails', async () => {
    render(<App />)
    const input = await screen.findByPlaceholderText('Message…')
    await waitFor(() => { expect(input).toBeEnabled() })

    const api = mocks.apis[mocks.apis.length - 1]! as unknown as {
      sendMessage: ReturnType<typeof vi.fn>
    }
    api.sendMessage.mockRejectedValueOnce(new Error('offline'))

    fireEvent.change(input, { target: { value: 'do not lose this' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => { expect(api.sendMessage).toHaveBeenCalledTimes(1) })
    expect(await screen.findByRole('alert')).toHaveTextContent('Message could not be sent.')
    // The failed send must leave the typed input and out of prompt history.
    expect((input as HTMLTextAreaElement).value).toBe('do not lose this')
    expect(JSON.parse(localStorage.getItem('hm.promptHistory') ?? '[]')).toEqual([])
  })

  it('clears the composer text after a successful send', async () => {
    render(<App />)
    const input = await screen.findByPlaceholderText('Message…')
    await waitFor(() => { expect(input).toBeEnabled() })

    const api = mocks.apis[mocks.apis.length - 1]! as unknown as {
      sendMessage: ReturnType<typeof vi.fn>
    }

    fireEvent.change(input, { target: { value: 'send me' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => { expect(api.sendMessage).toHaveBeenCalledTimes(1) })
    await waitFor(() => { expect((input as HTMLTextAreaElement).value).toBe('') })
    expect(JSON.parse(localStorage.getItem('hm.promptHistory') ?? '[]')).toEqual(['send me'])
  })

  it('decides every approval item and submits the structured protocol body', async () => {
    render(<App />)
    expect(await screen.findByPlaceholderText('Message…')).toBeEnabled()

    const emitter = mocks.emitters[mocks.emitters.length - 1]!
    await act(async () => {
      emitter.onEvent({
        type: 'approval.requested',
        session_id: 'session-01',
        run_id: 'run-01',
        request_id: 'request-01',
        payload: {
          approval_id: 'approval-01',
          protocol_version: 2,
          request_id: 'request-01',
          revision: 3,
          intent_summary: '去卧室拿杯子',
          items: [
            { item_id: 'item-cup-a', display_name: '白色杯子', location: '卧室床头柜', action_label: '拿取' },
            { item_id: 'item-enter-b', display_name: '卧室', location: '卧室', action_label: '进入' },
          ],
          expires_at: '2026-09-10T02:00:00Z',
          request_status: 'awaiting_approval',
        },
      })
    })

    expect(await screen.findByRole('dialog')).toBeVisible()
    expect(screen.getByText('白色杯子 · 拿取')).toBeVisible()
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).not.toContain('item-cup-a')
    expect(bodyText).not.toContain('item-enter-b')
    expect(bodyText).not.toContain('approval-01')

    expect(screen.getByRole('button', { name: '提交决定' })).toBeDisabled()
    fireEvent.click(screen.getAllByRole('radio', { name: '本次允许' })[0]!)
    fireEvent.click(screen.getAllByRole('radio', { name: '拒绝' })[1]!)
    fireEvent.click(screen.getByRole('button', { name: '提交决定' }))

    const api = mocks.apis[mocks.apis.length - 1]!
    await waitFor(() => { expect(api.submitApproval).toHaveBeenCalledTimes(1) })
    expect(api.submitApproval).toHaveBeenCalledWith('approval-01', {
      protocol_version: 2,
      submission_id: expect.any(String),
      request_revision: 3,
      decisions: [
        { item_id: 'item-cup-a', choice: 'allow_once' },
        { item_id: 'item-enter-b', choice: 'reject' },
      ],
    })

    await act(async () => {
      emitter.onEvent({
        type: 'approval.resolved',
        session_id: 'session-01',
        run_id: 'run-01',
        request_id: 'request-01',
        payload: {
          approval_id: 'approval-01',
          request_status: 'ready',
          approved: true,
          outcome: null,
          items: [],
        },
      })
    })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('App batch-2 integration', () => {
  beforeEach(() => {
    mocks.rejectMemories = false
    mocks.stop.mockClear()
    mocks.emitters.length = 0
    localStorage.clear()
    window.HTMLElement.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    window.history.replaceState({}, '', '/')
    vi.clearAllMocks()
  })

  const emit = async (event: Record<string, unknown>) => {
    const emitter = mocks.emitters[mocks.emitters.length - 1]!
    await act(async () => { emitter.onEvent(event) })
  }

  const api = () => mocks.apis[mocks.apis.length - 1]! as unknown as Record<string, ReturnType<typeof vi.fn>>

  it('hydrates a pending approval fetched over REST on (re)connect', async () => {
    api().pendingApprovals.mockResolvedValueOnce({
      approvals: [{
        approval_id: 'approval-h1',
        request_id: 'request-h1',
        revision: 2,
        intent_summary: '断线期间到达的申请',
        items: [{ item_id: 'item-h1', display_name: '白色杯子', location: '卧室', action_label: '拿取' }],
        deadline_at: '2026-09-10T02:00:00Z',
        request_status: 'awaiting_approval',
      }],
    })
    render(<App />)

    expect(await screen.findByRole('dialog')).toBeVisible()
    expect(screen.getByText('断线期间到达的申请')).toBeVisible()
    expect(screen.getByText('等待审批')).toBeVisible()
  })

  it('takes over the composer on question.asked and answers through the REST endpoint', async () => {
    render(<App />)
    expect(await screen.findByPlaceholderText('Message…')).toBeEnabled()

    await emit({
      type: 'request.accepted', session_id: 'session-01', run_id: 'run-01', request_id: 'request-01', payload: {},
    })
    await emit({
      type: 'question.asked', session_id: 'session-01', run_id: 'run-01', request_id: 'request-01',
      payload: { question_id: 'q-01', question: '杯子要什么颜色？', tool_call_id: 'call-1' },
    })

    expect(screen.queryByRole('textbox', { name: 'Message' })).toBeNull()
    expect(screen.getByText('杯子要什么颜色？')).toBeVisible()

    const answer = screen.getByRole('textbox', { name: '回答 Agent 的问题' })
    fireEvent.change(answer, { target: { value: '蓝色' } })
    fireEvent.keyDown(answer, { key: 'Enter' })

    await waitFor(() => { expect(api().answerQuestion).toHaveBeenCalledWith('session-01', 'q-01', '蓝色') })
    // Optimistic removal restores the composer editor immediately.
    await waitFor(() => { expect(screen.getByRole('textbox', { name: 'Message' })).toBeInTheDocument() })
  })

  it('queues composer input while busy and flushes in order on run completion', async () => {
    render(<App />)
    const input = await screen.findByPlaceholderText('Message…')
    await waitFor(() => { expect(input).toBeEnabled() })

    await emit({
      type: 'request.accepted', session_id: 'session-01', run_id: 'run-01', request_id: 'request-01', payload: {},
    })
    await emit({
      type: 'run.started', session_id: 'session-01', run_id: 'run-01', request_id: 'request-01', payload: {},
    })

    fireEvent.change(input, { target: { value: '排队第一条' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.change(input, { target: { value: '排队第二条' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(api().sendMessage).not.toHaveBeenCalled()
    expect(screen.getByText('排队 2 条')).toBeVisible()

    // Mid-queue removal works while still busy.
    fireEvent.click(screen.getByRole('button', { name: '移除排队消息 1' }))
    expect(screen.getByText('排队 1 条')).toBeVisible()

    await emit({
      type: 'run.completed', session_id: 'session-01', run_id: 'run-01', request_id: 'request-01',
      payload: { status: 'succeeded', final_reply: 'done' },
    })

    await waitFor(() => { expect(api().sendMessage).toHaveBeenCalledTimes(1) })
    expect(api().sendMessage.mock.calls[0]![2]).toBe('排队第二条')
    await waitFor(() => { expect(screen.queryByText(/排队 \d+ 条/)).toBeNull() })
  })

  it('toggles plan/act mode through the badge and echoes it optimistically', async () => {
    render(<App />)
    expect(await screen.findByPlaceholderText('Message…')).toBeEnabled()

    const badge = await screen.findByRole('button', { name: '执行' })
    fireEvent.click(badge)

    await waitFor(() => { expect(api().setUiMode).toHaveBeenCalledWith('session-01', 'plan') })
    expect(await screen.findByRole('button', { name: '规划' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByPlaceholderText(/规划模式/)).toBeInTheDocument()

    // A session.mode_changed broadcast from another client drives the same badge.
    await emit({
      type: 'session.mode_changed', session_id: 'session-01', run_id: '', request_id: '',
      payload: { ui_mode: 'act' },
    })
    expect(await screen.findByRole('button', { name: '执行' })).toBeInTheDocument()
  })

  it('edits into the draft and resends from the user bubble actions', async () => {
    api().history.mockResolvedValueOnce({
      session_id: 'session-01',
      messages: [{ role: 'user', text: '旧的提问' }],
    })
    render(<App />)
    const input = await screen.findByPlaceholderText('Message…')
    await waitFor(() => { expect(input).toBeEnabled() })
    expect(await screen.findByText('旧的提问')).toBeVisible()

    // 编辑 → text lands in the composer draft, transcript unchanged.
    fireEvent.click(screen.getByRole('button', { name: '编辑这条消息' }))
    expect((input as HTMLTextAreaElement).value).toBe('旧的提问')
    expect(document.querySelector('.user-row .user-text')).toHaveTextContent('旧的提问')

    // 重发 → goes straight through sendMessage.
    fireEvent.click(screen.getByRole('button', { name: '重发这条消息' }))
    await waitFor(() => { expect(api().sendMessage).toHaveBeenCalledTimes(1) })
    expect(api().sendMessage.mock.calls[0]![2]).toBe('旧的提问')
  })

  it('passes the picked provider/model through sendMessage and persists per session', async () => {
    render(<App />)
    const input = await screen.findByPlaceholderText('Message…')
    await waitFor(() => { expect(input).toBeEnabled() })
    expect(await screen.findByRole('combobox', { name: 'Provider' })).toBeInTheDocument()

    fireEvent.change(screen.getByRole('combobox', { name: 'Provider' }), { target: { value: 'primary' } })
    expect(localStorage.getItem('hm.model.session-01')).toBe(JSON.stringify({ provider_name: 'primary' }))

    fireEvent.change(input, { target: { value: '带模型的消息' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => { expect(api().sendMessage).toHaveBeenCalledTimes(1) })
    expect(api().sendMessage).toHaveBeenCalledWith('session-01', expect.any(String), '带模型的消息', {
      provider_name: 'primary',
      model: undefined,
    })
  })

  it('posts manual compaction and surfaces the triggered/kind toast', async () => {
    render(<App />)
    expect(await screen.findByPlaceholderText('Message…')).toBeEnabled()

    fireEvent.click(await screen.findByRole('button', { name: '压缩上下文' }))
    await waitFor(() => { expect(api().compactSession).toHaveBeenCalledWith('session-01') })
    expect(await screen.findByText('已触发上下文压缩')).toBeVisible()
    expect(screen.getByText(/kind: manual/)).toBeVisible()
  })
})
