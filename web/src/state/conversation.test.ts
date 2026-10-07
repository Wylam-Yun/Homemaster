import { describe, expect, it } from 'vitest'

import { initialConversationState, reduceConversation, reduceWebEvent } from './conversation'
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
  const requested = (items: Array<Record<string, unknown>>): WebEvent => event('approval.requested', {
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

  it('keeps extra item metadata for the details block', () => {
    const state = reduceWebEvent(initialConversationState, requested([
      {
        item_id: 'item-a', display_name: 'npm test', location: 'npm test', action_label: 'shell_exec',
        arguments: { command: 'npm test' },
      },
    ]))
    expect(state.turns['session-01:request-01'].approval?.items[0]).toMatchObject({
      item_id: 'item-a',
      arguments: { command: 'npm test' },
    })
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

describe('questions, ui mode and hydration', () => {
  it('tracks question.asked until question.answered and never touches turns', () => {
    let state = reduceWebEvent(initialConversationState, event('question.asked', {
      question_id: 'q-01', question: '杯子的颜色？', tool_call_id: 'call-9',
    }))
    expect(state.questions['session-01:q-01']).toMatchObject({
      questionId: 'q-01',
      sessionId: 'session-01',
      requestId: 'request-01',
      question: '杯子的颜色？',
      toolCallId: 'call-9',
    })
    // Session-scoped events must not create a transcript turn.
    expect(Object.keys(state.turns)).toHaveLength(0)

    state = reduceWebEvent(state, event('question.answered', { question_id: 'q-01' }))
    expect(state.questions).toEqual({})
  })

  it('drops a pending question on question.cancelled without touching turns', () => {
    let state = reduceWebEvent(initialConversationState, event('question.asked', {
      question_id: 'q-02', question: '哪个房间？',
    }))
    expect(state.questions['session-01:q-02']).toBeDefined()

    state = reduceWebEvent(state, event('question.cancelled', { question_id: 'q-02' }))
    expect(state.questions).toEqual({})
    // Session-scoped lifecycle events must not spawn transcript turns.
    expect(Object.keys(state.turns)).toHaveLength(0)
  })

  it('sweeps leftover questions of the ended run on terminal run events', () => {
    // Defense for a missed question.cancelled frame: run.completed/failed/
    // cancelled each tombstone the run's questions server-side, so a stale
    // entry must never keep holding the composer.
    const terminals: Array<[WebEvent['type'], Record<string, unknown>]> = [
      ['run.completed', { status: 'succeeded', final_reply: 'done' }],
      ['run.failed', { code: 'run_failed', message: 'boom', retryable: false }],
      ['run.cancelled', {}],
    ]
    for (const [type, payload] of terminals) {
      let state = reduceWebEvent(initialConversationState, event('question.asked', {
        question_id: 'q-stale', question: '还等吗？',
      }))
      state = reduceWebEvent(state, event(type, payload))
      expect(state.questions).toEqual({})
    }
  })

  it('keeps questions of other sessions and other requests on run terminal', () => {
    // The sweep mirrors the server's cancel_request scope (session+request):
    // a later run's fresh question must survive the prior run's late
    // terminal event, and other sessions are never touched.
    let state = reduceWebEvent(initialConversationState, event('question.asked', {
      question_id: 'q-dead', question: '过期的问题',
    }))
    state = reduceWebEvent(state, {
      ...event('question.asked', { question_id: 'q-next', question: '下一轮的问题' }),
      request_id: 'request-02',
    })
    state = reduceWebEvent(state, {
      ...event('question.asked', { question_id: 'q-elsewhere', question: '别的会话' }),
      session_id: 'session-02',
    })

    state = reduceWebEvent(state, event('run.completed', { status: 'succeeded' }))
    expect(state.questions['session-01:q-dead']).toBeUndefined()
    expect(state.questions['session-01:q-next']).toBeDefined()
    expect(state.questions['session-02:q-elsewhere']).toBeDefined()
  })

  it('records ui_mode per session without spawning a turn', () => {
    const state = reduceWebEvent(initialConversationState, {
      type: 'session.mode_changed',
      session_id: 'session-01',
      run_id: '',
      request_id: '',
      payload: { ui_mode: 'plan' },
    })
    expect(state.uiModes['session-01']).toBe('plan')
    expect(Object.keys(state.turns)).toHaveLength(0)
  })

  it('uses run.completed final_reply only when no answer was streamed', () => {
    // Empty-answer turn (reconnect path): final_reply becomes the answer.
    let state = reduceWebEvent(initialConversationState, event('request.accepted', {}, ''))
    state = reduceWebEvent(state, event('run.completed', { status: 'succeeded', final_reply: 'final text' }))
    const turn = state.turns['session-01:request-01']
    expect(turn.answer).toBe('final text')
    expect(turn.segments).toEqual([{ kind: 'answer', text: 'final text' }])
  })

  it('hydrates pending approvals into turns and prunes stale backfill rows', () => {
    let state = reduceConversation(initialConversationState, {
      type: 'session.hydrate_pending',
      sessionId: 'session-01',
      approvals: [{
        approval_id: 'ap-1',
        request_id: 'request-77',
        revision: 2,
        intent_summary: '擦桌子',
        items: [{ item_id: 'it-1', display_name: '桌子', location: '客厅', action_label: '擦拭' }],
        deadline_at: '2026-09-10T02:00:00Z',
        request_status: 'awaiting_approval',
      }],
      questions: [{ question_id: 'q-9', question: '要加水吗？', request_id: 'request-77' }],
      uiMode: 'plan',
    })

    const turn = state.turns['session-01:request-77']
    expect(turn.approval).toMatchObject({
      approvalId: 'ap-1', revision: 2, intentSummary: '擦桌子', source: 'backfill',
    })
    expect(state.questions['session-01:q-9']?.question).toBe('要加水吗？')
    expect(state.uiModes['session-01']).toBe('plan')

    // Second hydrate without the approval → the stale backfill row is dropped.
    state = reduceConversation(state, {
      type: 'session.hydrate_pending',
      sessionId: 'session-01',
      approvals: [],
      questions: [],
    })
    expect(state.turns['session-01:request-77']!.approval).toBeNull()
    expect(state.questions).toEqual({})
  })

  it('never overwrites a live WS-sourced approval with a backfill row', () => {
    let state = reduceWebEvent(initialConversationState, event('approval.requested', {
      approval_id: 'ap-live',
      protocol_version: 2,
      request_id: 'request-01',
      revision: 9,
      intent_summary: 'live request',
      items: [{ item_id: 'i', display_name: 'd', location: 'l', action_label: 'a' }],
      expires_at: '2026-09-10T02:00:00Z',
      request_status: 'awaiting_approval',
    }))
    state = reduceConversation(state, {
      type: 'session.hydrate_pending',
      sessionId: 'session-01',
      approvals: [{
        approval_id: 'ap-stale',
        request_id: 'request-01',
        revision: 1,
        intent_summary: 'stale row',
        items: [],
      }],
      questions: [],
    })
    expect(state.turns['session-01:request-01']!.approval?.approvalId).toBe('ap-live')
    expect(state.turns['session-01:request-01']!.approval?.revision).toBe(9)
  })
})
