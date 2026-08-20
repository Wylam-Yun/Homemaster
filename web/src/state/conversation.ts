import type { ArtifactRef, Usage, WebEvent } from '../protocol/events'

export type TurnStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export type ToolCallState = {
  toolCallId: string
  name: string
  arguments: Record<string, unknown>
  status: 'running' | 'completed' | 'failed'
  output: string
  artifacts: ArtifactRef[]
}

export type ApprovalState = {
  approvalId: string
  toolCallId: string
  name: string
  arguments: Record<string, unknown>
  cwd: string
  reason: string
}

export type TurnState = {
  requestId: string
  sessionId: string
  runId: string | null
  thinking: string
  answer: string
  tools: Record<string, ToolCallState>
  approval: ApprovalState | null
  usage: Usage | null
  status: TurnStatus
  error: { code: string; message: string; retryable: boolean } | null
}

export type ClientDiagnostic = {
  code: 'run_id_conflict'
  sessionId: string
  requestId: string
  expectedRunId: string
  receivedRunId: string
}

export type ConversationState = {
  turns: Record<string, TurnState>
  diagnostics: ClientDiagnostic[]
}

export const initialConversationState: ConversationState = { turns: {}, diagnostics: [] }

const turnKey = (event: WebEvent): string => `${event.session_id}:${event.request_id}`

function emptyTurn(event: WebEvent): TurnState {
  return {
    requestId: event.request_id,
    sessionId: event.session_id,
    runId: null,
    thinking: '',
    answer: '',
    tools: {},
    approval: null,
    usage: null,
    status: 'pending',
    error: null,
  }
}

export function reduceWebEvent(state: ConversationState, event: WebEvent): ConversationState {
  const key = turnKey(event)
  const current = state.turns[key] ?? emptyTurn(event)
  if (current.runId !== null && event.run_id !== '' && event.run_id !== current.runId) {
    return {
      ...state,
      diagnostics: [...state.diagnostics, {
        code: 'run_id_conflict',
        sessionId: event.session_id,
        requestId: event.request_id,
        expectedRunId: current.runId,
        receivedRunId: event.run_id,
      }],
    }
  }

  let turn = current
  switch (event.type) {
    case 'request.accepted':
      turn = { ...current, status: 'pending' }
      break
    case 'run.started':
      turn = { ...current, runId: event.run_id, status: 'running' }
      break
    case 'thinking.delta':
      turn = { ...current, thinking: current.thinking + event.payload.text }
      break
    case 'thinking.snapshot':
      turn = { ...current, thinking: event.payload.text }
      break
    case 'answer.delta':
      turn = { ...current, answer: current.answer + event.payload.text }
      break
    case 'answer.snapshot':
      turn = { ...current, answer: event.payload.text }
      break
    case 'tool.started': {
      const id = event.payload.tool_call_id
      turn = {
        ...current,
        tools: { ...current.tools, [id]: {
          toolCallId: id,
          name: event.payload.name,
          arguments: event.payload.arguments,
          status: 'running',
          output: '',
          artifacts: [],
        } },
      }
      break
    }
    case 'tool.completed':
    case 'tool.failed': {
      const id = event.payload.tool_call_id
      const previous = current.tools[id]
      turn = {
        ...current,
        tools: { ...current.tools, [id]: {
          toolCallId: id,
          name: event.payload.name,
          arguments: previous?.arguments ?? {},
          status: event.type === 'tool.failed' ? 'failed' : 'completed',
          output: event.payload.output,
          artifacts: event.payload.artifacts,
        } },
      }
      break
    }
    case 'approval.requested':
      turn = { ...current, approval: {
        approvalId: event.payload.approval_id,
        toolCallId: event.payload.tool_call_id,
        name: event.payload.name,
        arguments: event.payload.arguments,
        cwd: event.payload.cwd,
        reason: event.payload.reason,
      } }
      break
    case 'approval.resolved':
      turn = current.approval?.approvalId === event.payload.approval_id
        ? { ...current, approval: null }
        : current
      break
    case 'usage.updated':
      turn = { ...current, usage: event.payload }
      break
    case 'context.compacted':
      turn = current
      break
    case 'run.completed':
      turn = { ...current, approval: null, status: 'completed' }
      break
    case 'run.failed':
      turn = { ...current, approval: null, status: 'failed', error: event.payload }
      break
    case 'run.cancelled':
      turn = { ...current, approval: null, status: 'cancelled' }
      break
    default: {
      const exhaustive: never = event
      return exhaustive
    }
  }
  return { ...state, turns: { ...state.turns, [key]: turn } }
}
