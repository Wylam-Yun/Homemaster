import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeMasterApi, HttpError } from './http'

describe('HomeMasterApi', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('sends the stable message request and approval schemas', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ accepted: true }), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ approved: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const api = new HomeMasterApi()

    await api.sendMessage('session one', 'request-01', 'hello')
    await api.resolveApproval('approval/01', 'reject')

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/sessions/session%20one/messages', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ request_id: 'request-01', text: 'hello' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/approvals/approval%2F01', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ outcome: 'reject' }),
    }))
  })

  it('raises stable typed errors for non-success responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'session_busy', message: 'busy', retryable: true,
    }), { status: 409 })))

    await expect(new HomeMasterApi().cancel('session-01')).rejects.toEqual(
      new HttpError(409, 'session_busy', 'busy', true),
    )
  })

  it('reads memory snapshots and encoded history ids', async () => {
    const snapshot = {
      stats: { active_count: 1, archived_count: 0, total_count: 1, session_group_count: 1 },
      groups: [],
    }
    const history = { memory_id: 'memory/01', versions: [] }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(snapshot), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(history), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const api = new HomeMasterApi()

    await expect(api.memories()).resolves.toEqual(snapshot)
    await expect(api.memoryHistory('memory/01')).resolves.toEqual(history)

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/memories', expect.anything())
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/memories/memory%2F01/history',
      expect.anything(),
    )
  })
})
