import type { PendingApprovalRequest, PendingQuestion } from '../api/http'
import type { ApprovalItem, ArtifactRef, UiMode, Usage, WebEvent } from '../protocol/events'

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
  requestId: string
  revision: number
  intentSummary: string
  items: ApprovalItem[]
  expiresAt: string
  requestStatus: string
  /** 'backfill' entries came from GET …/approvals on (re)connect and are
   * pruned when the next fetch no longer lists them. */
  source?: 'ws' | 'backfill'
}

export type PendingQuestionState = {
  questionId: string
  sessionId: string
  requestId: string
  question: string
  toolCallId: string | null
}

export type TurnSegment =
  | { kind: 'thinking'; text: string }
  | { kind: 'answer'; text: string }
  | { kind: 'tool'; toolCallId: string }

export type TurnState = {
  requestId: string
  sessionId: string
  runId: string | null
  thinking: string
  answer: string
  tools: Record<string, ToolCallState>
  segments: TurnSegment[]
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
  /** Pending ask_user questions, keyed `${session_id}:${question_id}`. */
  questions: Record<string, PendingQuestionState>
  /** Server-persisted plan/act mode per session. */
  uiModes: Record<string, UiMode>
}

export const initialConversationState: ConversationState = {
  turns: {},
  diagnostics: [],
  questions: {},
  uiModes: {},
}

/**
 * Client-side action carrying the pending approvals/questions fetched after a
 * (re)connect. REST payloads never flow through the WebSocket reducer, so they
 * are folded in via this hydration step instead.
 */
export type HydratePendingAction = {
  type: 'session.hydrate_pending'
  sessionId: string
  approvals: PendingApprovalRequest[]
  questions: PendingQuestion[]
  uiMode?: UiMode | null
}

export type ConversationAction = WebEvent | HydratePendingAction

const turnKey = (event: WebEvent): string => `${event.session_id}:${event.request_id}`

function emptyTurn(sessionId: string, requestId: string): TurnState {
  return {
    requestId,
    sessionId,
    runId: null,
    thinking: '',
    answer: '',
    tools: {},
    segments: [],
    approval: null,
    usage: null,
    status: 'pending',
    error: null,
  }
}

function appendDelta(segments: TurnSegment[], kind: 'thinking' | 'answer', text: string): TurnSegment[] {
  const last = segments.at(-1)
  if (last?.kind === kind) {
    return [...segments.slice(0, -1), { kind, text: last.text + text }]
  }
  return [...segments, { kind, text }]
}

function applyTextSnapshot(segments: TurnSegment[], kind: 'thinking' | 'answer', text: string): TurnSegment[] {
  const firstIndex = segments.findIndex(segment => segment.kind === kind)
  if (firstIndex === -1) return [...segments, { kind, text }]
  const kept = segments.filter(segment => segment.kind !== kind)
  const before = segments.slice(0, firstIndex).filter(segment => segment.kind !== kind).length
  return [...kept.slice(0, before), { kind, text }, ...kept.slice(before)]
}

export function reduceConversation(state: ConversationState, action: ConversationAction): ConversationState {
  if (action.type === 'session.hydrate_pending') return hydratePending(state, action)
  return reduceWebEvent(state, action)
}

function normalizePendingApproval(raw: PendingApprovalRequest): ApprovalState | null {
  if (typeof raw.approval_id !== 'string' || raw.approval_id.length === 0) return null
  const items: ApprovalItem[] = (raw.items ?? [])
    .filter((item): item is Record<string, unknown> & { item_id: string } =>
      typeof item.item_id === 'string' && item.item_id.length > 0)
    .map(item => ({
      ...item,
      display_name: typeof item.display_name === 'string' ? item.display_name : '',
      location: typeof item.location === 'string' ? item.location : '',
      action_label: typeof item.action_label === 'string' ? item.action_label : '',
    }))
  const expiresAt = typeof raw.expires_at === 'string'
    ? raw.expires_at
    : typeof raw.deadline_at === 'string' ? raw.deadline_at : ''
  return {
    approvalId: raw.approval_id,
    requestId: typeof raw.request_id === 'string' ? raw.request_id : '',
    revision: typeof raw.revision === 'number' ? raw.revision : 0,
    intentSummary: typeof raw.intent_summary === 'string' ? raw.intent_summary : '',
    items,
    expiresAt,
    requestStatus: typeof raw.request_status === 'string' ? raw.request_status : 'awaiting_approval',
    source: 'backfill',
  }
}

/**
 * Merge the pending approvals/questions fetched over REST into the event state.
 * The fetched sets are authoritative *for that session*: hydrated approvals
 * absent from the fresh list are dropped (they resolved while we were offline),
 * while WS-sourced approvals are left to their resolved events.
 */
function hydratePending(state: ConversationState, action: HydratePendingAction): ConversationState {
  const sessionId = action.sessionId
  const fetchedApprovalIds = new Set(action.approvals.map(raw => raw.approval_id))
  const turns = { ...state.turns }
  for (const [key, turn] of Object.entries(turns)) {
    if (turn.sessionId !== sessionId) continue
    const approval = turn.approval
    if (approval !== null && approval.source === 'backfill' && !fetchedApprovalIds.has(approval.approvalId)) {
      turns[key] = { ...turn, approval: null }
    }
  }
  for (const raw of action.approvals) {
    const approval = normalizePendingApproval(raw)
    if (approval === null) continue
    const requestId = approval.requestId
    const turnRequestId = requestId !== '' ? requestId : `approval:${approval.approvalId}`
    const key = `${sessionId}:${turnRequestId}`
    const current = turns[key]
    if (current !== undefined) {
      if (current.approval !== null && current.approval.source !== 'backfill') continue
      turns[key] = { ...current, approval }
      continue
    }
    turns[key] = { ...emptyTurn(sessionId, turnRequestId), approval }
  }

  const questions: Record<string, PendingQuestionState> = {}
  for (const [key, pending] of Object.entries(state.questions)) {
    if (pending.sessionId !== sessionId) questions[key] = pending
  }
  for (const raw of action.questions) {
    if (typeof raw.question_id !== 'string' || raw.question_id.length === 0) continue
    if (typeof raw.question !== 'string' || raw.question.length === 0) continue
    questions[`${sessionId}:${raw.question_id}`] = {
      questionId: raw.question_id,
      sessionId,
      requestId: typeof raw.request_id === 'string' ? raw.request_id : '',
      question: raw.question,
      toolCallId: typeof raw.tool_call_id === 'string' ? raw.tool_call_id : null,
    }
  }

  const uiModes = action.uiMode === 'plan' || action.uiMode === 'act'
    ? { ...state.uiModes, [sessionId]: action.uiMode }
    : state.uiModes
  return { ...state, turns, questions, uiModes }
}

export function reduceWebEvent(state: ConversationState, event: WebEvent): ConversationState {
  // Session-scoped events carry no reliable run/request identity; folding them
  // into a turn would leak an empty stuck-pending row into the transcript.
  if (event.type === 'session.mode_changed') {
    return { ...state, uiModes: { ...state.uiModes, [event.session_id]: event.payload.ui_mode } }
  }
  if (event.type === 'question.asked') {
    const key = `${event.session_id}:${event.payload.question_id}`
    const pending: PendingQuestionState = {
      questionId: event.payload.question_id,
      sessionId: event.session_id,
      requestId: event.request_id,
      question: event.payload.question,
      toolCallId: typeof event.payload.tool_call_id === 'string' ? event.payload.tool_call_id : null,
    }
    return { ...state, questions: { ...state.questions, [key]: pending } }
  }
  if (event.type === 'question.answered' || event.type === 'question.cancelled') {
    const key = `${event.session_id}:${event.payload.question_id}`
    if (!(key in state.questions)) return state
    const questions = { ...state.questions }
    delete questions[key]
    return { ...state, questions }
  }

  const key = turnKey(event)
  const current = state.turns[key] ?? emptyTurn(event.session_id, event.request_id)
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
      turn = {
        ...current,
        thinking: current.thinking + event.payload.text,
        segments: appendDelta(current.segments, 'thinking', event.payload.text),
      }
      break
    case 'thinking.snapshot':
      turn = {
        ...current,
        thinking: event.payload.text,
        segments: applyTextSnapshot(current.segments, 'thinking', event.payload.text),
      }
      break
    case 'answer.delta':
      turn = {
        ...current,
        answer: current.answer + event.payload.text,
        segments: appendDelta(current.segments, 'answer', event.payload.text),
      }
      break
    case 'answer.snapshot':
      turn = {
        ...current,
        answer: event.payload.text,
        segments: applyTextSnapshot(current.segments, 'answer', event.payload.text),
      }
      break
    case 'tool.started': {
      const id = event.payload.tool_call_id
      const hasSegment = current.segments.some(segment => segment.kind === 'tool' && segment.toolCallId === id)
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
        segments: hasSegment ? current.segments : [...current.segments, { kind: 'tool', toolCallId: id }],
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
        requestId: event.payload.request_id,
        revision: event.payload.revision,
        intentSummary: event.payload.intent_summary,
        // Keep any extra per-item fields (tool arguments etc.) — the approval
        // card renders them under a <details> metadata block.
        items: event.payload.items.map(item => ({ ...item })),
        expiresAt: event.payload.expires_at,
        requestStatus: event.payload.request_status,
        source: 'ws',
      } }
      break
    case 'approval.resolved':
      turn = current.approval?.approvalId === event.payload.approval_id
        ? { ...current, approval: null }
        : current
      break
    case 'permission.grants_changed':
      turn = current
      break
    case 'usage.updated':
      turn = { ...current, usage: event.payload }
      break
    case 'context.compacted':
      turn = current
      break
    case 'run.completed': {
      // final_reply settles the terminal answer for (re)connected clients that
      // missed the streaming deltas; an already-assembled answer always wins.
      const reply = typeof event.payload.final_reply === 'string' ? event.payload.final_reply : ''
      const hasAnswer = current.answer.length > 0
      turn = reply.length > 0 && !hasAnswer
        ? { ...current, approval: null, status: 'completed', answer: reply, segments: appendDelta(current.segments, 'answer', reply) }
        : { ...current, approval: null, status: 'completed' }
      break
    }
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
  // A run's pending questions die with it: the server tombstones them via
  // PendingQuestionRegistry.cancel_request() and broadcasts one
  // question.cancelled each, but a missed frame must not strand the composer
  // on a QuestionCard that can never be answered again. The sweep mirrors the
  // server scope — session+request, never the whole session — so a later
  // run's fresh questions survive a late terminal event from the prior run.
  let questions = state.questions
  if (event.type === 'run.completed' || event.type === 'run.failed' || event.type === 'run.cancelled') {
    const kept = Object.entries(state.questions).filter(
      ([, pending]) => pending.sessionId !== event.session_id || pending.requestId !== event.request_id,
    )
    if (kept.length !== Object.keys(state.questions).length) {
      questions = Object.fromEntries(kept)
    }
  }
  return { ...state, turns: { ...state.turns, [key]: turn }, questions }
}
