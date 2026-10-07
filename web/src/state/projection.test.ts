import { describe, expect, it } from 'vitest'

import type { WebEvent } from '../protocol/events'
import { initialConversationState, reduceWebEvent } from './conversation'
import { projectSessionTurns, projectTurn, toolArgSummary } from './projection'

const event = (
  type: WebEvent['type'],
  payload: Record<string, unknown> = {},
  requestId = 'request-01',
  sessionId = 'session-01',
): WebEvent => ({
  type,
  session_id: sessionId,
  request_id: requestId,
  run_id: 'run-01',
  payload,
}) as WebEvent

const toolStarted = (id: string, name: string, args: Record<string, unknown> = {}, requestId = 'request-01') =>
  event('tool.started', { tool_call_id: id, name, arguments: args }, requestId)
const toolCompleted = (id: string, name: string, output = 'ok', requestId = 'request-01') =>
  event('tool.completed', { tool_call_id: id, name, status: 'completed', output, artifacts: [] }, requestId)

describe('projectSessionTurns', () => {
  it('keeps turn boundaries per request and threads the submitted user text', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted'))
    state = reduceWebEvent(state, event('answer.delta', { text: 'first answer' }))
    state = reduceWebEvent(state, event('run.completed'))
    state = reduceWebEvent(state, event('request.accepted', {}, 'request-02'))
    state = reduceWebEvent(state, event('answer.delta', { text: 'second answer' }, 'request-02'))
    state = reduceWebEvent(state, event('request.accepted', {}, 'request-x', 'session-02'))

    const turns = projectSessionTurns(state, 'session-01', {
      'request-01': 'first question',
      'request-02': 'second question',
    })

    expect(turns).toHaveLength(2)
    expect(turns[0]).toMatchObject({ requestId: 'request-01', userText: 'first question', status: 'completed' })
    expect(turns[1]).toMatchObject({ requestId: 'request-02', userText: 'second question', status: 'pending' })
    expect(turns[0]!.steps).toEqual([{ kind: 'answer', text: 'first answer' }])
  })

  it('groups consecutive tool calls into one tool_group step', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted'))
    state = reduceWebEvent(state, event('run.started'))
    state = reduceWebEvent(state, toolStarted('call-01', 'read_file', { path: '/tmp/a.txt' }))
    state = reduceWebEvent(state, toolStarted('call-02', 'run_shell', { command: 'ls -la' }))
    state = reduceWebEvent(state, toolCompleted('call-01', 'read_file', 'file body'))
    state = reduceWebEvent(state, toolCompleted('call-02', 'run_shell', 'total 3'))

    const [turn] = projectSessionTurns(state, 'session-01', {})
    expect(turn!.steps).toHaveLength(1)
    const step = turn!.steps[0]
    expect(step?.kind).toBe('tool_group')
    if (step?.kind !== 'tool_group') return
    expect(step.tools.map(tool => tool.toolCallId)).toEqual(['call-01', 'call-02'])
    expect(step.tools[0]!.argSummary).toBe('/tmp/a.txt')
    expect(step.tools[1]!.argSummary).toBe('$ ls -la')
    expect(step.tools[0]!.outputPreview).toBe('file body')
  })

  it('splits tool groups around interleaved reasoning and answer segments', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted'))
    state = reduceWebEvent(state, event('run.started'))
    state = reduceWebEvent(state, event('thinking.delta', { text: 'plan first' }))
    state = reduceWebEvent(state, toolStarted('call-01', 'search_files', { query: 'cup' }))
    state = reduceWebEvent(state, toolCompleted('call-01', 'search_files', 'found'))
    state = reduceWebEvent(state, event('thinking.delta', { text: 'now act' }))
    state = reduceWebEvent(state, toolStarted('call-02', 'robot_manipulate', { target: 'cup' }))
    state = reduceWebEvent(state, toolCompleted('call-02', 'robot_manipulate', 'done'))
    state = reduceWebEvent(state, event('answer.delta', { text: '拿到了' }))
    state = reduceWebEvent(state, event('run.completed'))

    const [turn] = projectSessionTurns(state, 'session-01', {})
    expect(turn!.steps.map(step => step.kind)).toEqual([
      'reasoning', 'tool_group', 'reasoning', 'tool_group', 'answer',
    ])
    const firstGroup = turn!.steps[1]
    const secondGroup = turn!.steps[3]
    if (firstGroup?.kind === 'tool_group') expect(firstGroup.tools).toHaveLength(1)
    if (secondGroup?.kind === 'tool_group') expect(secondGroup.tools).toHaveLength(1)
  })

  it('collapses streaming deltas into a canonical snapshot segment once', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted'))
    state = reduceWebEvent(state, event('thinking.delta', { text: 'one ' }))
    state = reduceWebEvent(state, event('thinking.delta', { text: 'two' }))
    state = reduceWebEvent(state, toolStarted('call-01', 'noop'))
    state = reduceWebEvent(state, event('thinking.snapshot', { text: 'canonical thought' }))
    state = reduceWebEvent(state, event('answer.delta', { text: 'draft' }))
    state = reduceWebEvent(state, event('answer.snapshot', { text: 'canonical answer' }))
    state = reduceWebEvent(state, event('run.completed'))

    const turn = projectTurn(state.turns['session-01:request-01']!)
    const reasoning = turn.steps.filter(step => step.kind === 'reasoning')
    const answers = turn.steps.filter(step => step.kind === 'answer')
    expect(reasoning).toEqual([{ kind: 'reasoning', text: 'canonical thought' }])
    expect(answers).toEqual([{ kind: 'answer', text: 'canonical answer' }])
  })

  it('marks failed runs with an error step carrying the server message', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted'))
    state = reduceWebEvent(state, event('run.started'))
    state = reduceWebEvent(state, event('thinking.delta', { text: 'partial' }))
    state = reduceWebEvent(state, toolStarted('call-01', 'search_files', { query: 'x' }))
    state = reduceWebEvent(state, event('tool.failed', {
      tool_call_id: 'call-01', name: 'search_files', status: 'failed', output: 'not found', artifacts: [],
    }))
    state = reduceWebEvent(state, event('run.failed', {
      code: 'run_failed', message: 'planner exploded', retryable: false,
    }))

    const turn = projectTurn(state.turns['session-01:request-01']!)
    expect(turn.status).toBe('failed')
    const last = turn.steps.at(-1)
    expect(last).toEqual({ kind: 'error', status: 'failed', message: 'planner exploded' })
    const group = turn.steps.find(step => step.kind === 'tool_group')
    if (group?.kind === 'tool_group') {
      expect(group.tools[0]).toMatchObject({ status: 'failed', error: 'not found', outputPreview: 'not found' })
    }
  })

  it('marks cancelled runs with a cancelled error step', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted'))
    state = reduceWebEvent(state, event('run.started'))
    state = reduceWebEvent(state, event('answer.delta', { text: 'half answer' }))
    state = reduceWebEvent(state, event('run.cancelled'))

    const turn = projectTurn(state.turns['session-01:request-01']!)
    expect(turn.status).toBe('cancelled')
    expect(turn.steps.map(step => step.kind)).toEqual(['answer', 'error'])
    expect(turn.steps.at(-1)).toMatchObject({ kind: 'error', status: 'cancelled' })
  })

  it('keeps approval state on the projected turn', () => {
    let state = reduceWebEvent(initialConversationState, event('request.accepted'))
    state = reduceWebEvent(state, event('approval.requested', {
      approval_id: 'approval-01',
      protocol_version: 2,
      request_id: 'request-01',
      revision: 1,
      intent_summary: '进入卧室',
      items: [{ item_id: 'item-a', display_name: '卧室', location: '卧室', action_label: '进入' }],
      expires_at: '2026-09-10T02:00:00Z',
      request_status: 'awaiting_approval',
    }))

    const turn = projectTurn(state.turns['session-01:request-01']!)
    expect(turn.approval?.approvalId).toBe('approval-01')
  })
})

describe('toolArgSummary', () => {
  it('prefixes shell-like tools with $ and prefers paths for file tools', () => {
    expect(toolArgSummary('run_shell', { command: 'ls -la /tmp' })).toBe('$ ls -la /tmp')
    expect(toolArgSummary('read_file', { path: '/etc/hosts' })).toBe('/etc/hosts')
    expect(toolArgSummary('write_file', { content: 'x', file_path: '/tmp/out.txt' })).toBe('/tmp/out.txt')
    expect(toolArgSummary('search_files', { query: 'needle' })).toBe('needle')
    expect(toolArgSummary('noop', {})).toBe('')
  })
})
