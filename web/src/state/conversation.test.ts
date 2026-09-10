import { describe, expect, it } from 'vitest'

import { initialConversationState, reduceWebEvent } from './conversation'
import type { WebEvent } from '../protocol/events'

const event = (
  type: WebEvent['type'],
  payload: Record<string, unknown> = {},
  runId = 'run-01',
): WebEvent => ({
  type,
  session_id: 'session-01',
  request_id: 'request-01',
  run_id: runId,
  payload,
}) as WebEvent

describe('reduceWebEvent', () => {
  it('appends deltas, calibrates snapshots, and never duplicates terminal answer text', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted', {}, ''))
    state = reduceWebEvent(state, event('run.started'))
    state = reduceWebEvent(state, event('thinking.delta', { text: 'one ' }))
    state = reduceWebEvent(state, event('thinking.delta', { text: 'two' }))
    state = reduceWebEvent(state, event('answer.delta', { text: 'draft' }))
    state = reduceWebEvent(state, event('thinking.snapshot', { text: 'canonical thought' }))
    state = reduceWebEvent(state, event('answer.snapshot', { text: 'canonical answer' }))
    state = reduceWebEvent(state, event('run.completed', { final_reply: 'duplicate' }))

    const turn = state.turns['session-01:request-01']
    expect(turn.thinking).toBe('canonical thought')
    expect(turn.answer).toBe('canonical answer')
    expect(turn.status).toBe('completed')
  })

  it('fences a request to its first authoritative run id', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted', {}, ''))
    state = reduceWebEvent(state, event('run.started'))
    state = reduceWebEvent(state, event('answer.delta', { text: 'accepted' }, 'run-other'))

    expect(state.turns['session-01:request-01'].answer).toBe('')
    expect(state.diagnostics).toEqual([
      {
        code: 'run_id_conflict',
        sessionId: 'session-01',
        requestId: 'request-01',
        expectedRunId: 'run-01',
        receivedRunId: 'run-other',
      },
    ])
  })

  it('updates tools by tool_call_id and keeps partial thinking on failure', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted', {}, ''))
    state = reduceWebEvent(state, event('run.started'))
    state = reduceWebEvent(state, event('thinking.delta', { text: 'partial' }))
    state = reduceWebEvent(state, event('tool.started', {
      tool_call_id: 'call-01', name: 'search_files', arguments: { query: 'x' },
    }))
    state = reduceWebEvent(state, event('tool.failed', {
      tool_call_id: 'call-01', name: 'search_files', status: 'failed', output: 'not found', artifacts: [],
    }))
    state = reduceWebEvent(state, event('run.failed', {
      code: 'run_failed', message: 'failed', retryable: false,
    }))

    const turn = state.turns['session-01:request-01']
    expect(turn.thinking).toBe('partial')
    expect(turn.tools['call-01']).toMatchObject({ status: 'failed', output: 'not found' })
    expect(turn.status).toBe('failed')
  })
})

describe('structured approval cards', () => {
  const requested = (items: Array<Record<string, string>>): WebEvent => event('approval.requested', {
    approval_id: 'approval-01',
    protocol_version: 2,
    request_id: 'request-01',
    revision: 4,
    intent_summary: '去卧室拿杯子',
    items,
    expires_at: '2026-09-10T02:00:00Z',
    request_status: 'awaiting_approval',
  })

  it('stores per-item cards without tool internals and clears them on resolution', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted', {}, ''))
    state = reduceWebEvent(state, requested([
      { item_id: 'item-a', display_name: '白色杯子', location: '卧室床头柜', action_label: '拿取' },
      { item_id: 'item-b', display_name: '卧室', location: '卧室', action_label: '进入' },
    ]))

    const approval = state.turns['session-01:request-01'].approval
    expect(approval).toMatchObject({
      approvalId: 'approval-01',
      revision: 4,
      intentSummary: '去卧室拿杯子',
    })
    expect(approval?.items).toHaveLength(2)
    expect(approval).not.toHaveProperty('arguments')
    expect(approval).not.toHaveProperty('cwd')

    state = reduceWebEvent(state, event('approval.resolved', {
      approval_id: 'approval-01',
      request_status: 'ready',
      approved: true,
      items: [{ item_id: 'item-a', choice: 'allow_once' }],
    }))
    expect(state.turns['session-01:request-01'].approval).toBeNull()
  })

  it('ignores grant change notifications without touching the turn', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted', {}, ''))
    state = reduceWebEvent(state, event('permission.grants_changed', {
      request_id: 'request-01',
      grant_ids: ['grant-1'],
    }))
    expect(state.turns['session-01:request-01'].approval).toBeNull()
    expect(state.turns['session-01:request-01'].status).toBe('pending')
  })
})
