import type { ManagedMemory } from '../api/http'

/**
 * Read-only human projection of a managed memory.
 *
 * The stored `content` of structured (ALFWorld trajectory) memories is a
 * serialized machine JSON blob. Rendering it verbatim is unreadable, so the
 * list view uses `summarizeMemory` (one human line + one anchor line) and
 * reserves the raw JSON for a collapsed debug block in the detail dialog.
 * Nothing here affects what the agent consumes; it is display-only.
 */

export type SummaryTone = 'success' | 'failure' | 'uncertain' | 'neutral'

export type MemorySummary = {
  icon: string
  title: string
  detail: string | null
  tone: SummaryTone
  searchText: string
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
export function shortEpisode(episodeId: string | null | undefined): string | null {
  if (episodeId === null || episodeId === undefined) return null
  const trimmed = episodeId.trim()
  if (trimmed.length === 0) return null
  const tail = trimmed.includes('/') ? (trimmed.split('/').pop() ?? trimmed) : trimmed
  return tail.length > 0 ? tail : null
}

export function shortSession(sessionId: string | null): string {
  if (sessionId === null || sessionId.length === 0) return '未关联'
  return sessionId.slice(0, 8)
}

function recordString(record: Record<string, unknown> | null, key: string): string | null {
  if (record === null) return null
  return stringField(record[key])
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
