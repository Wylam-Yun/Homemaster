export type ArtifactRef = {
  artifact_handle: string
  run_id: string
  filename: string
  media_type: string
  content_sha256: string
}

export type Usage = Record<string, number>

type Envelope<T extends string, P> = {
  type: T
  session_id: string
  run_id: string
  request_id: string
  payload: P
}

export type WebEvent =
  | Envelope<'request.accepted' | 'run.started' | 'run.completed' | 'run.cancelled', Record<string, never>>
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
      tool_call_id: string
      name: string
      arguments: Record<string, unknown>
      cwd: string
      reason: string
    }>
  | Envelope<'approval.resolved', {
      approval_id: string
      tool_call_id: string
      name: string
      approved: boolean
      outcome: string
    }>
  | Envelope<'usage.updated', Usage>
  | Envelope<'context.compacted', {
      trigger?: string
      before_tokens?: number
      after_tokens?: number
    }>

const EVENT_TYPES = new Set<WebEvent['type']>([
  'request.accepted', 'run.started', 'run.completed', 'run.failed', 'run.cancelled',
  'thinking.delta', 'thinking.snapshot', 'answer.delta', 'answer.snapshot',
  'tool.started', 'tool.completed', 'tool.failed',
  'approval.requested', 'approval.resolved', 'usage.updated', 'context.compacted',
])

export function isWebEvent(value: unknown): value is WebEvent {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return typeof item.type === 'string'
    && EVENT_TYPES.has(item.type as WebEvent['type'])
    && typeof item.session_id === 'string'
    && typeof item.run_id === 'string'
    && typeof item.request_id === 'string'
    && typeof item.payload === 'object'
    && item.payload !== null
}
