import { render, screen } from '@testing-library/react'
import { fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  rejectMemories: false,
  stop: vi.fn(),
}))

vi.mock('./api/http', () => ({
  HttpError: class HttpError extends Error {},
  HomeMasterApi: class HomeMasterApi {
    listSessions = vi.fn().mockResolvedValue({ sessions: [{ session_id: 'session-01' }] })
    history = vi.fn().mockResolvedValue({ session_id: 'session-01', messages: [] })
    createSession = vi.fn().mockResolvedValue({ session_id: 'session-new' })
    sendMessage = vi.fn().mockResolvedValue({ accepted: true })
    cancel = vi.fn().mockResolvedValue({ cancelled: true })
    resolveApproval = vi.fn().mockResolvedValue({ approved: true })
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
      private readonly callbacks: { onStateChange: (state: string) => void },
    ) {}
    start() { this.callbacks.onStateChange('connected') }
    stop() { mocks.stop() }
  },
}))

import { App } from './App'


describe('App memory navigation', () => {
  beforeEach(() => {
    mocks.rejectMemories = false
    mocks.stop.mockClear()
    localStorage.clear()
    window.HTMLElement.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => { vi.clearAllMocks() })

  it('keeps conversation usable when memory loading fails', async () => {
    mocks.rejectMemories = true
    render(<App />)

    expect(await screen.findByRole('button', { name: '对话' })).toBeVisible()
    expect(await screen.findByPlaceholderText('Message HomeMaster…')).toBeEnabled()
  })

  it('switches to memory view and collapses history without changing the session', async () => {
    render(<App />)

    expect(await screen.findByRole('button', { name: '打开会话 session-01' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '折叠历史会话' }))
    expect(screen.queryByRole('button', { name: '打开会话 session-01' })).not.toBeInTheDocument()
    expect(localStorage.getItem('homemaster:web:history-collapsed')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: '记忆管理' }))
    expect(await screen.findByRole('heading', { name: '记忆管理' })).toBeVisible()
    expect(mocks.stop).not.toHaveBeenCalled()
    await waitFor(() => { expect(screen.getByText('来源会话')).toBeVisible() })
  })
})
