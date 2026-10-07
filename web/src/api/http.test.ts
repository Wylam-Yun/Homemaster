import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeMasterApi, HttpError } from './http'

describe('HomeMasterApi', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('sends the structured approval submission and cancel schemas', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ accepted: true }), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        approval_id: 'approval-01',
        request_id: 'request-01',
        request_status: 'ready',
        execution_started: true,
        persisted_grant_ids: [],
        items: [{ item_id: 'item-a', choice: 'allow_once' }],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        approval_id: 'approval-01',
        request_status: 'cancelled',
      }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const api = new HomeMasterApi()

    await api.sendMessage('session one', 'request-01', 'hello')
    await api.submitApproval('approval/01', {
      protocol_version: 2,
      submission_id: 'sub-1',
      request_revision: 3,
      decisions: [
        { item_id: 'item-a', choice: 'allow_once' },
        { item_id: 'item-b', choice: 'allow_always' },
      ],
    })
    await api.cancelApproval('approval/01', { submission_id: 'sub-2', request_revision: 3 })

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/sessions/session%20one/messages', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ request_id: 'request-01', text: 'hello' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/approvals/approval%2F01', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        protocol_version: 2,
        submission_id: 'sub-1',
        request_revision: 3,
        decisions: [
          { item_id: 'item-a', choice: 'allow_once' },
          { item_id: 'item-b', choice: 'allow_always' },
        ],
      }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/approvals/approval%2F01/cancel', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ submission_id: 'sub-2', request_revision: 3 }),
    }))
  })

  it('lists and revokes long-term grants', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ grants: [], next_cursor: null }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ grant_id: 'grant-01', status: 'revoked' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const api = new HomeMasterApi()

    await api.listGrants({ resource_kind: 'object', status: 'active', limit: 50 })
    await api.revokeGrant('grant/01', { submission_id: 'rev-1', expected_revision: 2 })

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/permissions/grants?resource_kind=object&status=active&limit=50', expect.anything())
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/permissions/grants/grant%2F01/revoke', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ submission_id: 'rev-1', expected_revision: 2 }),
    }))
  })

  it('sends provider/model pass-through fields only when set', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ accepted: true }), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ accepted: true }), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ accepted: true }), { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)
    const api = new HomeMasterApi()

    await api.sendMessage('s1', 'r1', 'plain')
    await api.sendMessage('s1', 'r2', 'picked', { provider_name: 'primary' })
    await api.sendMessage('s1', 'r3', 'custom', { provider_name: 'backup', model: 'gpt-5-pro' })

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/sessions/s1/messages', expect.objectContaining({
      body: JSON.stringify({ request_id: 'r1', text: 'plain' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/sessions/s1/messages', expect.objectContaining({
      body: JSON.stringify({ request_id: 'r2', text: 'picked', provider_name: 'primary' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/sessions/s1/messages', expect.objectContaining({
      body: JSON.stringify({ request_id: 'r3', text: 'custom', provider_name: 'backup', model: 'gpt-5-pro' }),
    }))
  })

  it('reads providers, meta, session status and pending queues', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ providers: [{ name: 'p', kind: 'k', model: 'm', api_key_configured: true }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ version: '35', memory_mode: 'files', environment: null }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ session_id: 's1', generation: 1, revision: 2, status: 'idle', active: false, cancellation_requested: false, task_status: null, environment_ref: null, ui_mode: 'plan' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ approvals: [{ approval_id: 'a1' }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ questions: [{ question_id: 'q1', question: 'q?' }] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const api = new HomeMasterApi()

    await api.providers()
    await api.meta()
    await api.sessionStatus('s/1')
    await api.pendingApprovals('s1')
    await api.pendingQuestions('s1')

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/providers', expect.anything())
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/meta', expect.anything())
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/sessions/s%2F1/status', expect.anything())
    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/sessions/s1/approvals', expect.anything())
    expect(fetchMock).toHaveBeenNthCalledWith(5, '/api/sessions/s1/questions', expect.anything())
  })

  it('posts compact, mode and question answers to the session endpoints', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ session_id: 's1', generation: 1, revision: 2, triggered: true, kind: 'manual' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ session_id: 's1', ui_mode: 'plan' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ question_id: 'q9', accepted: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const api = new HomeMasterApi()

    await api.compactSession('s1')
    await api.setUiMode('s1', 'plan')
    await api.answerQuestion('s1', 'q/9', '蓝色')

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/sessions/s1/compact', expect.objectContaining({ method: 'POST' }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/sessions/s1/mode', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ ui_mode: 'plan' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/sessions/s1/questions/q%2F9/answer', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ text: '蓝色' }),
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
