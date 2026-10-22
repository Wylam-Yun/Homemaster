export type ArtifactRef = {
  artifact_handle: string
  run_id: string
  filename: string
  media_type: string
  content_sha256: string
}

export type Usage = Record<string, number>

export type UiMode = 'plan' | 'act'

export type ApprovalItem = {
  item_id: string
  display_name: string
  location: string
  action_label: string
  // The approval flow may attach extra per-item metadata (tool arguments,
  // resource kind/id, matched grants…); the dialog renders them under
  // <details> minus the internal identity keys.
  [key: string]: unknown
}

export type ApprovalResolvedItem = {
  item_id: string
  choice?: string
}

type Envelope<T extends string, P> = {
  type: T
  session_id: string
  run_id: string
  request_id: string
  // Stamped by WebEventHub.publish under its fanout lock: per-session
  // monotonic sequence plus epoch-second timestamp on every wire event,
  // including direct publishes that bypass the Runtime event pump.
  seq: number
  ts: number
  payload: P
}

export type WebEvent =
  | Envelope<'request.accepted' | 'run.started' | 'run.cancelled', Record<string, never>>
  // status/final_reply let a (re)connected client settle the terminal reply
  // without a history reload.
  | Envelope<'run.completed', { status?: string; final_reply?: string }>
  | Envelope<'thinking.delta' | 'thinking.snapshot' | 'answer.delta' | 'answer.snapshot', { text: string }>
  | Envelope<'run.failed', { code: string; message: string; retryable: boolean }>
  | Envelope<'tool.started', {
      tool_call_id: string
      name: string
      arguments: Record<string, unknown>
    }>
  | Envelope<'tool.completed' | 'tool.failed', {
      tool_call_id: string
      name: string
      status: 'completed' | 'failed'
      output: string
      artifacts: ArtifactRef[]
    }>
  | Envelope<'approval.requested', {
      approval_id: string
      protocol_version: number
      request_id: string
      revision: number
      intent_summary: string
      items: ApprovalItem[]
      expires_at: string
      request_status: string
    }>
  | Envelope<'approval.resolved', {
      approval_id: string
      protocol_version?: number
      request_id?: string
      revision?: number
      request_status: string
      approved?: boolean | null
      outcome?: string | null
      items: ApprovalResolvedItem[]
    }>
  | Envelope<'permission.grants_changed', {
      request_id: string
      grant_ids: string[]
    }>
  | Envelope<'usage.updated', Usage>
  | Envelope<'context.compacted', {
      trigger?: string
      before_tokens?: number
      after_tokens?: number
    }>
  | Envelope<'question.asked', {
      question_id: string
      question: string
      tool_call_id?: string | null
    }>
  | Envelope<'question.answered', {
      question_id: string
      answer?: string
    }>
  // Emitted once per pending question when its run ends or is cancelled
  // (app.py run teardown); payload carries only the question id.
  | Envelope<'question.cancelled', {
      question_id: string
    }>
  | Envelope<'session.mode_changed', { ui_mode: UiMode }>
  // Session-finalization (archive) job lifecycle, emitted by the memory work
  // queue on the session's stream; the payload carries job status detail.
  | Envelope<'memory.work.processing' | 'memory.work.completed' | 'memory.work.failed', {
      job_id?: string
      job_type?: string
      status?: string
      error?: string
      extracted_count?: number
      collected_events?: number
      duration_ms?: number
    }>

const EVENT_TYPES = new Set<WebEvent['type']>([
  'request.accepted', 'run.started', 'run.completed', 'run.failed', 'run.cancelled',
  'thinking.delta', 'thinking.snapshot', 'answer.delta', 'answer.snapshot',
  'tool.started', 'tool.completed', 'tool.failed',
  'approval.requested', 'approval.resolved', 'permission.grants_changed',
  'usage.updated', 'context.compacted',
  'question.asked', 'question.answered', 'question.cancelled', 'session.mode_changed',
  'memory.work.processing', 'memory.work.completed', 'memory.work.failed',
])

export function isWebEvent(value: unknown): value is WebEvent {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.type === 'string'
    && EVENT_TYPES.has(item.type as WebEvent['type'])
    && typeof item.session_id === 'string'
    && typeof item.run_id === 'string'
    && typeof item.request_id === 'string'
    && typeof item.seq === 'number'
    && typeof item.ts === 'number'
    && typeof item.payload === 'object'
    && item.payload !== null
}
