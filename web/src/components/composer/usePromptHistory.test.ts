import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { draftStorageKey, usePromptHistory } from './usePromptHistory'

const HISTORY_KEY = 'hm.promptHistory'

function storedHistory(): string[] {
  return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]') as string[]
}

describe('usePromptHistory', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('records submissions newest-first with dedup, capped at 200 entries', () => {
    const { result } = renderHook(() => usePromptHistory('s1'))

    act(() => {
      result.current.record('first')
      result.current.record('second')
      result.current.record('first')
    })

    expect(storedHistory()).toEqual(['first', 'second'])

    act(() => {
      for (let index = 0; index < 205; index += 1) result.current.record(`cmd-${index}`)
    })
    const entries = storedHistory()
    expect(entries).toHaveLength(200)
    expect(entries[0]).toBe('cmd-204')
    expect(entries.at(-1)).toBe('cmd-5')
    expect(entries).not.toContain('cmd-4')
  })

  it('ignores blank submissions and hydrates from a stored ring', () => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(['older', 'oldest']))
    const { result } = renderHook(() => usePromptHistory('s1'))

    act(() => {
      result.current.record('   ')
      result.current.record('next')
    })
    expect(storedHistory()).toEqual(['next', 'older', 'oldest'])

    const fresh = renderHook(() => usePromptHistory('other'))
    // navigate mutates refs only — no act() needed.
    expect(fresh.result.current.navigate('up', '', 0)).toEqual({ text: 'next', caret: 'start' })
    fresh.unmount()
  })

  it('walks the ring on ArrowUp and returns to the saved draft on ArrowDown', () => {
    const { result } = renderHook(() => usePromptHistory('s1'))
    act(() => {
      result.current.record('one')
      result.current.record('two')
    })

    expect(result.current.navigate('up', '', 0)).toEqual({ text: 'two', caret: 'start' })
    expect(result.current.navigate('up', 'two', 0)).toEqual({ text: 'one', caret: 'start' })
    // Oldest entry reached — further up is not handled.
    expect(result.current.navigate('up', 'one', 0)).toBeNull()

    expect(result.current.navigate('down', 'one', 3)).toEqual({ text: 'two', caret: 'end' })
    // Down past the newest entry restores the draft saved on entry.
    expect(result.current.navigate('down', 'two', 3)).toEqual({ text: '', caret: 'end' })
    expect(result.current.navigate('down', '', 0)).toBeNull()
  })

  it('refuses to enter history while a non-empty draft exists', () => {
    const { result } = renderHook(() => usePromptHistory('s1'))
    act(() => { result.current.record('old') })

    expect(result.current.navigate('up', 'typed draft', 0)).toBeNull()
    expect(result.current.navigate('up', 'typed draft', 5)).toBeNull()
  })

  it('restores the mid-edit draft when leaving history', () => {
    // A draft can only be non-empty if navigation started from an empty one,
    // but the saved draft must round-trip through the ring regardless.
    localStorage.setItem(HISTORY_KEY, JSON.stringify(['a', 'b']))
    const { result } = renderHook(() => usePromptHistory('s1'))

    expect(result.current.navigate('up', '', 0)?.text).toBe('a')
    act(() => { result.current.reset() })
    expect(result.current.navigate('up', '', 0)?.text).toBe('a')
  })

  it('debounces per-session drafts and flushes pending writes on session switch', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(
      ({ sessionId }) => usePromptHistory(sessionId),
      { initialProps: { sessionId: 's1' as string | null } },
    )

    act(() => { result.current.persistDraft('draft one') })
    expect(localStorage.getItem(draftStorageKey('s1'))).toBeNull()
    act(() => { vi.advanceTimersByTime(300) })
    expect(localStorage.getItem(draftStorageKey('s1'))).toBe('draft one')

    // A pending write lands on the old session key when switching away.
    act(() => { result.current.persistDraft('draft one edited') })
    rerender({ sessionId: 's2' })
    expect(localStorage.getItem(draftStorageKey('s1'))).toBe('draft one edited')

    act(() => { result.current.persistDraft('draft two') })
    act(() => { vi.advanceTimersByTime(300) })
    expect(localStorage.getItem(draftStorageKey('s2'))).toBe('draft two')
    expect(result.current.loadDraft()).toBe('draft two')

    rerender({ sessionId: 's1' })
    expect(result.current.loadDraft()).toBe('draft one edited')
  })

  it('flushes a pending draft on unmount and clears drafts explicitly', () => {
    vi.useFakeTimers()
    const { result, unmount } = renderHook(() => usePromptHistory('s1'))
    act(() => { result.current.persistDraft('typed') })
    unmount()
    expect(localStorage.getItem(draftStorageKey('s1'))).toBe('typed')

    act(() => { result.current.persistDraft('typed') })
    act(() => { result.current.clearDraft() })
    act(() => { vi.advanceTimersByTime(1000) })
    expect(localStorage.getItem(draftStorageKey('s1'))).toBeNull()
  })
})
