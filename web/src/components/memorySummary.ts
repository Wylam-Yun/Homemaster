import type { ManagedMemory } from '../api/http'

/**
 * Read-only human projection of a managed memory.
 *
 * Stored `content` can be:
 * 1. A structured ALFWorld trajectory (`structure_status === 'valid'`)
 * 2. A JSON-serialized fact/experience/procedure record (`content` starts with `{`)
 * 3. A Markdown episodic record (`# Episodic Memory Record` / `**Episode Date:...`)
 * 4. Plain human text
 *
 * `summarizeMemory` extracts a clean human title, optional subtitle (`detail`),
 * and structured key-value fields (`fields`) so the UI never dumps raw JSON or
 * Markdown headers on memory cards.
 */

export type SummaryTone = 'success' | 'failure' | 'uncertain' | 'neutral'

export type MemoryField = {
  label: string
  value: string
}

export type MemorySummary = {
  icon: string
  title: string
  detail: string | null
  tone: SummaryTone
  searchText: string
  fields?: MemoryField[]
  rawJson?: Record<string, unknown> | null
}

const TYPE_LABELS: Record<string, string> = {
  fact: '事实',
  procedure: '操作流程',
  trajectory: 'ALFWorld 轨迹',
  alfworld_experience: 'ALFWorld 派生经验',
  experience: '经验',
  episodic: '情景',
  tool_trace: '工具轨迹',
  skill_candidate: '技能候选',
  profile: '画像',
  file_knowledge: '文件知识',
}

export function typeLabel(memoryType: string): string {
  return TYPE_LABELS[memoryType] ?? memoryType
}

/** Stable group order for the type-grouped list; unknown types go last. */
export const TYPE_GROUP_ORDER = [
  'fact',
  'trajectory',
  'alfworld_experience',
  'experience',
  'skill_candidate',
  'episodic',
  'tool_trace',
  'procedure',
  'profile',
  'file_knowledge',
]

const CLASSIFICATION_LABELS: Record<string, string> = {
  agent_success: '任务成功',
  runtime_failure: '运行失败',
  harness_navigation_failure: '导航失败',
  harness_operation_failure: '操作失败',
  unclassified_execution_failure: '执行失败',
  execution_state_uncertain: '执行状态不确定',
}

export function classificationLabel(classification: string): string {
  return CLASSIFICATION_LABELS[classification] ?? classification
}

const FAILURE_REASON_LABELS: Record<string, string> = {
  setup_unexpected: '环境初始化异常',
  external_reset_failed: '环境重置失败',
}

/** Bilingual reason: Chinese gloss plus the original machine value. */
export function failureReasonLabel(reason: string): string {
  const gloss = FAILURE_REASON_LABELS[reason]
  return gloss !== undefined && gloss !== reason ? `${gloss} (${reason})` : reason
}

const TASK_VERBS: Record<string, string> = {
  look_at_obj_in_light: '检查',
  pick_and_place_simple: '放置',
  pick_two_obj_and_place: '放置两件',
  pick_heat_then_place_in_recep: '加热后放置',
  pick_cool_then_place_in_recep: '冷却后放置',
  pick_clean_then_place_in_recep: '清洗后放置',
}

type ParsedGoal = {
  taskType: string
  object: string | null
  parent: string | null
  toggle: string | null
}

function stringField(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed.length > 0 && trimmed !== 'None' ? trimmed : null
}

function capText(text: string, max = 140): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > max ? `${clean.slice(0, max)}…` : clean
}

/**
 * goal_type comes in three shapes: PDDL JSON
 * ({"task_type": "...", "pddl_params": {...}}), ALFWorld shorthand
 * (look_at_obj_in_light-CD-None-DeskLamp-314), or a bare task_type.
 */
export function parseGoalType(goalType: string | null | undefined): ParsedGoal | null {
  if (goalType === null || goalType === undefined) return null
  const trimmed = goalType.trim()
  if (trimmed.length === 0 || trimmed === 'unknown') return null
  if (trimmed.startsWith('{')) {
    try {
      const parsed = JSON.parse(trimmed) as { task_type?: unknown; pddl_params?: unknown }
      if (typeof parsed.task_type !== 'string' || parsed.task_type.length === 0) return null
      const params = (
        typeof parsed.pddl_params === 'object' && parsed.pddl_params !== null
          ? (parsed.pddl_params as Record<string, unknown>)
          : {}
      )
      return {
        taskType: parsed.task_type,
        object: stringField(params['object_target']),
        parent: stringField(params['parent_target']) ?? stringField(params['mrecep_target']),
        toggle: stringField(params['toggle_target']),
      }
    } catch {
      return null
    }
  }
  const shorthand = parseShorthandGoal(trimmed)
  if (shorthand !== null) return shorthand
  if (/^[A-Za-z][A-Za-z0-9_]*$/.test(trimmed)) {
    return { taskType: trimmed, object: null, parent: null, toggle: null }
  }
  return null
}

function parseShorthandGoal(value: string): ParsedGoal | null {
  const parts = value.split('-')
  if (parts.length < 3 || !parts[0].includes('_')) return null
  const last = parts[parts.length - 1]
  const body = /^\d+$/.test(last) ? parts.slice(0, -1) : parts
  const meaningful = body.slice(1).filter(part => part !== '' && part !== 'None')
  return {
    taskType: parts[0],
    object: meaningful[0] ?? null,
    parent: null,
    toggle: meaningful[1] ?? null,
  }
}

/** Human one-liner for a goal, e.g. 用DeskLamp检查BaseballBat. */
export function humanizeGoal(goalType: string | null | undefined): string | null {
  const parsed = parseGoalType(goalType)
  if (parsed === null) return null
  const object = parsed.object ?? ''
  switch (parsed.taskType) {
    case 'look_at_obj_in_light':
      return parsed.toggle !== null ? `用${parsed.toggle}检查${object}` : `检查${object}`
    case 'pick_and_place_simple':
      return parsed.parent !== null ? `把${object}放到${parsed.parent}` : `放置${object}`
    case 'pick_two_obj_and_place':
      return parsed.parent !== null ? `把两个${object}放到${parsed.parent}` : `放置两件${object}`
    case 'pick_heat_then_place_in_recep':
    case 'pick_cool_then_place_in_recep':
    case 'pick_clean_then_place_in_recep': {
      const action = parsed.taskType.includes('heat')
        ? '加热'
        : parsed.taskType.includes('cool') ? '冷却' : '清洗'
      return parsed.parent !== null ? `${action}${object}后放到${parsed.parent}` : `${action}${object}`
    }
    default: {
      const verb = TASK_VERBS[parsed.taskType] ?? parsed.taskType
      return object.length > 0 ? `${verb} ${object}`.trim() : verb
    }
  }
}

/** Short anchor for an episode id: tail after the last slash. */
export function shortEpisode(episodeId: string | null | undefined): string {
  if (episodeId === null || episodeId === undefined) return null as unknown as string
  const trimmed = episodeId.trim()
  if (trimmed.length === 0) return null as unknown as string
  const tail = trimmed.includes('/') ? (trimmed.split('/').pop() ?? trimmed) : trimmed
  return (tail.length > 0 ? tail : null) as unknown as string
}

export function shortSession(sessionId: string | null): string {
  if (sessionId === null || sessionId.length === 0) return '未关联'
  const stripped = sessionId.replace(/^session[-_]/i, '')
  return (stripped.length > 0 ? stripped : sessionId).slice(0, 8)
}

function recordString(record: Record<string, unknown> | null, key: string): string | null {
  if (record === null) return null
  return stringField(record[key])
}

const RESULT_LABELS: Record<string, string> = {
  found: '已找到',
  not_found: '未找到',
  error: '执行报错',
  success: '成功',
  failure: '失败',
  unknown: '待确认',
}

/**
 * Summarize JSON-encoded plain memories (fact / experience / procedure) so raw
 * JSON strings never leak onto memory cards.
 */
function summarizeJsonContent(
  rawText: string,
  memory: ManagedMemory,
  label: string,
): MemorySummary | null {
  if (!rawText.startsWith('{')) return null
  let parsed: Record<string, unknown>
  try {
    const candidate = JSON.parse(rawText) as unknown
    if (typeof candidate !== 'object' || candidate === null || Array.isArray(candidate)) return null
    parsed = candidate as Record<string, unknown>
  } catch {
    return null
  }

  const summary = stringField(parsed['summary'])
  const task = stringField(parsed['task'])
  const target = stringField(parsed['target'])
  const objectName = stringField(parsed['object'])
  const location = stringField(parsed['location'])
  const obsResult = stringField(parsed['result']) ?? stringField(parsed['observation_kind']) ?? stringField(parsed['outcome'])
  const recordType = stringField(parsed['type'])
  const recordTime = stringField(parsed['record_time']) ?? stringField(parsed['observed_at'])

  let failureLessonSummary: string | null = null
  const failureLesson = parsed['failure_lesson']
  if (typeof failureLesson === 'object' && failureLesson !== null) {
    failureLessonSummary = stringField((failureLesson as Record<string, unknown>)['summary'])
  }

  const fields: MemoryField[] = []
  if (task !== null) fields.push({ label: '关联任务', value: task })
  if (objectName !== null) fields.push({ label: '目标对象', value: objectName })
  if (location !== null) fields.push({ label: '所在位置', value: location })
  if (target !== null && target !== location) fields.push({ label: '作用范围', value: target })
  if (obsResult !== null) fields.push({ label: '状态结果', value: RESULT_LABELS[obsResult] ?? obsResult })
  if (summary !== null) fields.push({ label: '核心摘要', value: summary })
  if (failureLessonSummary !== null) fields.push({ label: '经验教训', value: failureLessonSummary })
  if (Array.isArray(parsed['steps']) && parsed['steps'].length > 0) {
    fields.push({ label: '记录步骤', value: `共 ${parsed['steps'].length} 个执行步骤` })
  }
  if (recordTime !== null) fields.push({ label: '记录时间', value: recordTime })

  let title: string
  let detail: string | null = null
  if (summary !== null) {
    title = capText(summary, 140)
    if (task !== null) detail = `任务：${task}`
    else if (objectName !== null && location !== null) detail = `${objectName} · ${location}`
  } else if (task !== null) {
    title = capText(task, 140)
    detail = failureLessonSummary !== null ? capText(failureLessonSummary, 90) : target
  } else if (objectName !== null && location !== null) {
    const statusText = obsResult !== null ? (RESULT_LABELS[obsResult] ?? obsResult) : null
    title = statusText !== null
      ? `${objectName}（${statusText}）· ${location}`
      : `${objectName} 位于 ${location}`
    detail = recordType
  } else if (failureLessonSummary !== null) {
    title = capText(failureLessonSummary, 140)
  } else {
    const firstStr = Object.values(parsed).find(v => typeof v === 'string' && v.trim().length > 0) as string | undefined
    title = firstStr !== undefined ? capText(firstStr, 140) : `${label}记录`
  }

  const tone: SummaryTone = obsResult === 'success' || obsResult === 'found'
    ? 'success'
    : obsResult === 'error' || obsResult === 'failure'
      ? 'failure'
      : 'neutral'
  const icon = tone === 'success' ? '✅' : tone === 'failure' ? '⚠️' : memory.memory_type === 'experience' ? '💡' : '📌'

  return {
    icon,
    title,
    detail,
    tone,
    searchText: [title, detail ?? '', rawText, label, memory.memory_type, memory.memory_id].join(' '),
    fields,
    rawJson: parsed,
  }
}

/**
 * Summarize Markdown episodic memories (`# Episodic Memory Record` or
 * `**Episode Date:...`) by stripping machine metadata headers and picking the
 * first meaningful turn heading or user question.
 */
function summarizeEpisodicMarkdown(
  rawText: string,
  memory: ManagedMemory,
  label: string,
): MemorySummary | null {
  if (!rawText.startsWith('#') && !rawText.startsWith('**Episode Date') && !rawText.startsWith('On ')) {
    return null
  }
  const lines = rawText.split('\n').map(line => line.trim()).filter(line => line.length > 0)
  const turnHeadings: string[] = []
  const userQuotes: string[] = []
  const bodyLines: string[] = []

  for (const line of lines) {
    if (/^#\s+Episodic Memory Record/i.test(line)) continue
    if (/^\*\*(Conversation timestamp|Session ID|Runtime context|Episode Date)/i.test(line)) continue
    if (/^Session ID:/i.test(line)) continue
    if (/^---+$/.test(line)) continue
    if (/^##\s+Turn\s+\d+\s*[—-]\s*(.+)$/i.test(line)) {
      const match = /^##\s+Turn\s+\d+\s*[—-]\s*(.+)$/i.exec(line)
      if (match?.[1]) turnHeadings.push(match[1].replace(/\([^)]*UTC\)/gi, '').trim())
      continue
    }
    const asked = /asked(?:\s+the\s+HomeMaster\s+assistant)?:\s*"([^"]+)"/i.exec(line)
    if (asked?.[1]) {
      userQuotes.push(asked[1].trim())
    }
    if (!/^On\s+\w+day,\s+\d{4}/i.test(line) && !/^At\s+\d{2}:\d{2}:\d{2}/i.test(line)) {
      bodyLines.push(line.replace(/^#+\s*/, '').replace(/\*\*/g, ''))
    }
  }

  let title: string
  let detail: string | null = null
  if (userQuotes.length > 0) {
    title = capText(`对话记录：「${userQuotes[0]}」`, 140)
    if (turnHeadings.length > 0) {
      detail = `共 ${turnHeadings.length} 轮交互 · ${turnHeadings.slice(0, 3).join(' / ')}`
    }
  } else if (turnHeadings.length > 0) {
    title = capText(`情景记录：${turnHeadings.join(' → ')}`, 140)
  } else if (bodyLines.length > 0) {
    title = capText(bodyLines[0]!, 140)
  } else {
    return null
  }

  return {
    icon: '💬',
    title,
    detail,
    tone: 'neutral',
    searchText: [title, detail ?? '', rawText, label, memory.memory_type, memory.memory_id].join(' '),
  }
}

export function summarizeMemory(memory: ManagedMemory): MemorySummary {
  const label = typeLabel(memory.memory_type)
  const record = memory.record
  const classification = memory.classification ?? recordString(record, 'classification')
  const goalSource = memory.goal_type ?? recordString(record, 'goal_type')
  const goalText = humanizeGoal(goalSource)
  const episode = shortEpisode(memory.episode_id ?? recordString(record, 'episode_id'))

  if (memory.structure_status === 'valid' && record !== null) {
    if (classification !== null) {
      if (classification === 'agent_success') {
        const title = goalText ?? (episode !== null ? `任务成功 · ${episode}` : '任务成功')
        const searchText = [title, episode, label, '任务成功 agent_success', memory.memory_id]
          .filter((part): part is string => part !== null)
          .join(' ')
        return { icon: '✅', title, detail: episode, tone: 'success', searchText }
      }
      if (classification === 'execution_state_uncertain') {
        const title = goalText !== null ? `执行状态不确定 · ${goalText}` : '执行状态不确定'
        const searchText = [title, episode, label, '执行状态不确定 execution_state_uncertain', memory.memory_id]
          .filter((part): part is string => part !== null)
          .join(' ')
        return { icon: '❓', title, detail: episode, tone: 'uncertain', searchText }
      }
      const reason = recordString(record, 'failure_reason')
      const reasonText = reason !== null ? failureReasonLabel(reason) : null
      const title = reasonText !== null
        ? `${classificationLabel(classification)} · ${reasonText}`
        : classificationLabel(classification)
      const detail = goalText ?? episode
      const searchText = [title, detail, episode, label, classificationLabel(classification), classification, reason, memory.memory_id]
        .filter((part): part is string => part !== null)
        .join(' ')
      return { icon: '❌', title, detail, tone: 'failure', searchText }
    }
    const fallback = goalText ?? episode ?? '结构化记忆'
    const searchText = [fallback, episode, label, memory.memory_id]
      .filter((part): part is string => part !== null)
      .join(' ')
    return {
      icon: '📝',
      title: fallback,
      detail: episode !== null && episode !== fallback ? episode : null,
      tone: 'neutral',
      searchText,
    }
  }

  const text = memory.content.trim()
  const jsonSummary = summarizeJsonContent(text, memory, label)
  if (jsonSummary !== null) return jsonSummary

  const episodicSummary = summarizeEpisodicMarkdown(text, memory, label)
  if (episodicSummary !== null) return episodicSummary

  const title = text.length > 140 ? `${text.slice(0, 140)}…` : text
  return {
    icon: '📝',
    title,
    detail: null,
    tone: 'neutral',
    searchText: [memory.content, label, memory.memory_type, memory.memory_id].join(' '),
  }
}

export type TypeGroup = { key: string; label: string; memories: ManagedMemory[] }

export function groupByType(memories: ManagedMemory[]): TypeGroup[] {
  const order = new Map(TYPE_GROUP_ORDER.map((type, index) => [type, index]))
  const buckets = new Map<string, ManagedMemory[]>()
  for (const memory of memories) {
    const bucket = buckets.get(memory.memory_type)
    if (bucket !== undefined) bucket.push(memory)
    else buckets.set(memory.memory_type, [memory])
  }
  return [...buckets.entries()]
    .map(([key, items]) => ({ key, label: typeLabel(key), memories: items }))
    .sort((left, right) => {
      const leftOrder = order.get(left.key) ?? TYPE_GROUP_ORDER.length
      const rightOrder = order.get(right.key) ?? TYPE_GROUP_ORDER.length
      if (leftOrder !== rightOrder) return leftOrder - rightOrder
      return left.label.localeCompare(right.label, 'zh-CN')
    })
}

export type MemoryInsight = { success: number; failure: number; plain: number; total: number }

/** Display-only counts across all statuses (matches the tab-independent tiles). */
export function computeMemoryInsight(memories: ManagedMemory[]): MemoryInsight {
  let success = 0
  let failure = 0
  let plain = 0
  for (const memory of memories) {
    if (memory.outcome === 'success') {
      success += 1
    } else if (
      memory.structure_status === 'valid' &&
      memory.classification !== null &&
      memory.classification !== undefined &&
      memory.classification !== 'agent_success'
    ) {
      failure += 1
    }
    if (memory.structure_status === 'plain') plain += 1
  }
  return { success, failure, plain, total: memories.length }
}

/** Shared display helpers for memory pages/dialogs (dates, enum labels). */
export function formatDate(value: string | null): string {
  if (value === null) return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

export function formatFullDate(value: string | null): string {
  if (value === null) return '未知'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return '未知'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium' }).format(date)
}

export function archiveReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    user_request: '用户请求',
    direct_structured_update: '结构化更新替换',
  }
  return labels[reason] ?? reason
}

export function compileStateLabel(state: string): string {
  const labels: Record<string, string> = {
    queued: '排队中',
    running: '编译中',
    succeeded: '已完成',
    failed: '失败',
  }
  return labels[state] ?? state
}

export function subtaskLabel(tasksetId: string, subtaskIndex: number | null | undefined): string {
  if (subtaskIndex === null || subtaskIndex === undefined) return tasksetId
  return `${tasksetId} / 子任务 ${subtaskIndex + 1}`
}
