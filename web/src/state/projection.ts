import type { Usage } from '../protocol/events'
import type { ApprovalState, ConversationState, ToolCallState, TurnState, TurnStatus } from './conversation'

export type ToolRecord = {
  toolCallId: string
  name: string
  args: Record<string, unknown>
  argSummary: string
  status: ToolCallState['status']
  outputPreview: string
  error: string | null
  // Web events carry no timestamps; duration stays null until the protocol exposes them.
  durationMs: number | null
  tool: ToolCallState
}

export type Step =
  | { kind: 'reasoning'; text: string }
  | { kind: 'tool_group'; tools: ToolRecord[] }
  | { kind: 'answer'; text: string }
  | { kind: 'error'; status: 'failed' | 'cancelled'; message: string }

export type Turn = {
  requestId: string
  sessionId: string
  status: TurnStatus
  userText: string | null
  approval: ApprovalState | null
  usage: Usage | null
  steps: Step[]
}

const PREVIEW_LIMIT = 160

function truncate(text: string, limit = PREVIEW_LIMIT): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length <= limit ? flat : `${flat.slice(0, limit)}…`
}

function stringArg(args: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = args[key]
    if (typeof value === 'string' && value.length > 0) return value
    if (typeof value === 'number') return String(value)
  }
  return null
}

export function toolArgSummary(name: string, args: Record<string, unknown>): string {
  const lowered = name.toLowerCase()
  if (/shell|exec|bash|command|terminal/.test(lowered)) {
    const command = stringArg(args, ['command', 'cmd', 'script', 'input'])
    if (command !== null) return `$ ${truncate(command)}`
  }
  const path = stringArg(args, ['path', 'file_path', 'filepath', 'file', 'filename', 'target', 'source'])
  if (path !== null) return truncate(path)
  for (const value of Object.values(args)) {
    if (typeof value === 'string' && value.length > 0) return truncate(value)
    if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  }
  return ''
}

function outputPreview(output: string): string {
  const line = output.split('\n').find(entry => entry.trim().length > 0) ?? ''
  return truncate(line)
}

function toRecord(tool: ToolCallState): ToolRecord {
  return {
    toolCallId: tool.toolCallId,
    name: tool.name,
    args: tool.arguments,
    argSummary: toolArgSummary(tool.name, tool.arguments),
    status: tool.status,
    outputPreview: outputPreview(tool.output),
    error: tool.status === 'failed' ? tool.output : null,
    durationMs: null,
    tool,
  }
}

export function projectTurn(turn: TurnState, userText: string | null = null): Turn {
  const steps: Step[] = []
  let pending: ToolRecord[] = []
  const seenTools = new Set<string>()
  const flushTools = () => {
    if (pending.length === 0) return
    steps.push({ kind: 'tool_group', tools: pending })
    pending = []
  }

  for (const segment of turn.segments) {
    if (segment.kind === 'tool') {
      const tool = turn.tools[segment.toolCallId]
      if (tool === undefined) continue
      seenTools.add(segment.toolCallId)
      pending.push(toRecord(tool))
      continue
    }
    flushTools()
    if (segment.text.length === 0) continue
    steps.push(segment.kind === 'thinking'
      ? { kind: 'reasoning', text: segment.text }
      : { kind: 'answer', text: segment.text })
  }
  flushTools()

  // Fallback for turns whose flat fields were filled without segment tracking.
  if (turn.segments.length === 0) {
    if (turn.thinking.length > 0) steps.push({ kind: 'reasoning', text: turn.thinking })
    const orphan = Object.values(turn.tools).map(toRecord)
    if (orphan.length > 0) steps.push({ kind: 'tool_group', tools: orphan })
    if (turn.answer.length > 0) steps.push({ kind: 'answer', text: turn.answer })
  } else {
    const orphans = Object.values(turn.tools).filter(tool => !seenTools.has(tool.toolCallId)).map(toRecord)
    if (orphans.length > 0) steps.push({ kind: 'tool_group', tools: orphans })
  }

  if (turn.status === 'failed') {
    steps.push({ kind: 'error', status: 'failed', message: turn.error?.message ?? 'Run failed.' })
  } else if (turn.status === 'cancelled') {
    steps.push({ kind: 'error', status: 'cancelled', message: 'Run cancelled. Partial output was kept.' })
  }

  return {
    requestId: turn.requestId,
    sessionId: turn.sessionId,
    status: turn.status,
    userText,
    approval: turn.approval,
    usage: turn.usage,
    steps,
  }
}

export function projectSessionTurns(
  state: ConversationState,
  sessionId: string | null,
  submitted: Record<string, string>,
): Turn[] {
  return Object.values(state.turns)
    .filter(turn => turn.sessionId === sessionId)
    .map(turn => projectTurn(turn, submitted[turn.requestId] ?? null))
}
