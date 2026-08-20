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
})
