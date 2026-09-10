import { describe, expect, it } from 'vitest'

import type { ManagedMemory } from '../api/http'
import {
  classificationLabel,
  computeMemoryInsight,
  failureReasonLabel,
  groupByType,
  humanizeGoal,
  shortEpisode,
  summarizeMemory,
  typeLabel,
} from './memorySummary'


function memory(partial: Partial<ManagedMemory> & { memory_id: string }): ManagedMemory {
  return {
    content: '',
    memory_type: 'fact',
    memory_type_label: '事实',
    status: 'active',
    session_id: 'session-01',
    created_at: '2026-08-24T08:00:00Z',
    updated_at: '2026-08-24T08:30:00Z',
    archived_at: null,
    archive_reason: null,
    record: null,
    structure_status: 'plain',
    has_history: false,
    ...partial,
  }
}

const PDDL_GOAL = '{"pddl_params":{"mrecep_target":"","object_sliced":false,"object_target":"BaseballBat","parent_target":"","toggle_target":"DeskLamp"},"task_type":"look_at_obj_in_light"}'

describe('typeLabel', () => {
  it('covers previously unlabeled machine types', () => {
    expect(typeLabel('tool_trace')).toBe('工具轨迹')
    expect(typeLabel('experience')).toBe('经验')
    expect(typeLabel('episodic')).toBe('情景')
    expect(typeLabel('skill_candidate')).toBe('技能候选')
    expect(typeLabel('profile')).toBe('画像')
    expect(typeLabel('fact')).toBe('事实')
  })

  it('falls back to the raw type for unknown values', () => {
    expect(typeLabel('something_new')).toBe('something_new')
  })
})

describe('humanizeGoal', () => {
  it('parses PDDL JSON goals', () => {
    expect(humanizeGoal(PDDL_GOAL)).toBe('用DeskLamp检查BaseballBat')
  })

  it('parses ALFWorld shorthand goals', () => {
    expect(humanizeGoal('look_at_obj_in_light-CD-None-DeskLamp-314')).toBe('用DeskLamp检查CD')
  })

  it('parses bare task types and place tasks', () => {
    expect(humanizeGoal('pick_and_place_simple')).toBe('放置')
    expect(humanizeGoal(
      '{"pddl_params":{"object_target":"Book","parent_target":"SideTable"},"task_type":"pick_and_place_simple"}',
    )).toBe('把Book放到SideTable')
  })

  it('returns null for unknown or missing goals', () => {
    expect(humanizeGoal('unknown')).toBeNull()
    expect(humanizeGoal(null)).toBeNull()
    expect(humanizeGoal(undefined)).toBeNull()
  })
})

describe('failureReasonLabel', () => {
  it('keeps the machine value next to the Chinese gloss', () => {
    expect(failureReasonLabel('setup_unexpected')).toBe('环境初始化异常 (setup_unexpected)')
    expect(failureReasonLabel('external_reset_failed')).toBe('环境重置失败 (external_reset_failed)')
    expect(failureReasonLabel('weird_new_reason')).toBe('weird_new_reason')
  })
})

describe('summarizeMemory', () => {
  it('summarizes a successful trajectory without leaking JSON', () => {
    const summary = summarizeMemory(memory({
      memory_id: 'm-success',
      content: '{"classification":"agent_success",...}',
      structure_status: 'valid',
      classification: 'agent_success',
      outcome: 'success',
      goal_type: PDDL_GOAL,
      episode_id: 'valid_seen/look_at_obj_in_light-BaseballBat-None-DeskLamp-303',
      record: { classification: 'agent_success' },
    }))

    expect(summary.tone).toBe('success')
    expect(summary.icon).toBe('✅')
    expect(summary.title).toBe('用DeskLamp检查BaseballBat')
    expect(summary.title).not.toContain('{')
    expect(summary.searchText).not.toContain('classification')
  })

  it('summarizes a failed trajectory with a bilingual reason', () => {
    const summary = summarizeMemory(memory({
      memory_id: 'm-fail',
      content: '{"classification":"runtime_failure",...}',
      structure_status: 'valid',
      classification: 'runtime_failure',
      outcome: 'unknown',
      goal_type: 'unknown',
      episode_id: '028c2cc3860e-0001/setup-terminal',
      record: { classification: 'runtime_failure', failure_reason: 'setup_unexpected' },
    }))

    expect(summary.tone).toBe('failure')
    expect(summary.title).toBe('运行失败 · 环境初始化异常 (setup_unexpected)')
  })

  it('falls back to the episode anchor when the goal is unreadable', () => {
    const summary = summarizeMemory(memory({
      memory_id: 'm-ep',
      content: '{}',
      structure_status: 'valid',
      classification: 'agent_success',
      outcome: 'success',
      goal_type: 'unknown',
      episode_id: 'valid_seen/look_at_obj_in_light-BaseballBat-None-DeskLamp-303',
      record: { classification: 'agent_success' },
    }))

    expect(summary.title).toBe('任务成功 · look_at_obj_in_light-BaseballBat-None-DeskLamp-303')
  })

  it('falls back to the goal for structured memories without a classification', () => {
    const summary = summarizeMemory(memory({
      memory_id: 'm-noclass',
      content: '{"record_kind":"trajectory",...}',
      structure_status: 'valid',
      goal_type: PDDL_GOAL,
      record: { record_kind: 'trajectory' },
    }))

    expect(summary.tone).toBe('neutral')
    expect(summary.title).toBe('用DeskLamp检查BaseballBat')
    expect(summary.title).not.toContain('{')
  })

  it('renders plain memories verbatim with a length cap', () => {
    const long = `内容${'很长'.repeat(100)}`
    const summary = summarizeMemory(memory({ memory_id: 'm-plain', content: long }))

    expect(summary.tone).toBe('neutral')
    expect(summary.title.endsWith('…')).toBe(true)
    expect(summary.title.length).toBe(141)
  })
})

describe('groupByType', () => {
  it('groups by machine type with a stable order', () => {
    const groups = groupByType([
      memory({ memory_id: 'a', memory_type: 'tool_trace' }),
      memory({ memory_id: 'b', memory_type: 'fact' }),
      memory({ memory_id: 'c', memory_type: 'fact' }),
    ])

    expect(groups.map(group => group.key)).toEqual(['fact', 'tool_trace'])
    expect(groups[0].label).toBe('事实')
    expect(groups[0].memories).toHaveLength(2)
  })
})

describe('computeMemoryInsight', () => {
  it('counts success, failure, and plain memories', () => {
    const insight = computeMemoryInsight([
      memory({ memory_id: 'a', outcome: 'success', structure_status: 'valid', classification: 'agent_success', record: {} }),
      memory({ memory_id: 'b', structure_status: 'valid', classification: 'runtime_failure', record: {} }),
      memory({ memory_id: 'c', structure_status: 'plain' }),
    ])

    expect(insight).toEqual({ success: 1, failure: 1, plain: 1, total: 3 })
  })
})

describe('shortEpisode', () => {
  it('keeps the tail after the last slash', () => {
    expect(shortEpisode('028c2cc3860e-0001/setup-terminal')).toBe('setup-terminal')
    expect(shortEpisode(null)).toBeNull()
  })
})

describe('classificationLabel', () => {
  it('covers known trajectory classifications', () => {
    expect(classificationLabel('execution_state_uncertain')).toBe('执行状态不确定')
    expect(classificationLabel('harness_navigation_failure')).toBe('导航失败')
  })
})
