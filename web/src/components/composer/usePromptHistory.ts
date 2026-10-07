import { useCallback, useEffect, useMemo, useRef } from 'react'

const HISTORY_KEY = 'hm.promptHistory'
const MAX_ENTRIES = 200
const DRAFT_DEBOUNCE_MS = 300

export function draftStorageKey(sessionId: string | null): string {
  return `hm.draft.${sessionId ?? 'new'}`
}

function readHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (raw === null) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((entry): entry is string => typeof entry === 'string' && entry.length > 0)
  } catch {
    return []
  }
}

function writeHistory(entries: readonly string[]): void {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(entries))
  } catch {
    // storage unavailable (private mode / quota) — history is best-effort
  }
}

function readDraft(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function writeDraft(key: string, text: string): void {
  try {
    if (text.length === 0) localStorage.removeItem(key)
    else localStorage.setItem(key, text)
  } catch {
    // best-effort, same as history
  }
}

export type HistoryNavigation = {
  text: string
  caret: 'start' | 'end'
}

export type PromptHistory = {
  /** Record a submitted prompt into the global ring (dedup, newest first, max 200). */
  record(text: string): void
  /**
   * ArrowUp/ArrowDown navigation. Returns the entry to display, or null when the
   * caret position does not allow history navigation (let the textarea default run).
   * Entering history requires an empty draft; while inside history the caret must
   * sit at the start (up) or at either edge (down). Down past the newest entry
   * restores the draft saved when navigation began.
   */
  navigate(direction: 'up' | 'down', currentText: string, caret: number): HistoryNavigation | null
  /** Drop any in-progress history navigation (typing or submitting resets it). */
  reset(): void
  isNavigating(): boolean
  /** Draft for the current session key, as persisted. */
  loadDraft(): string
  /** Debounced (300ms) per-session draft write; flushed on session switch/unmount. */
  persistDraft(text: string): void
  clearDraft(): void
}

export function usePromptHistory(sessionId: string | null): PromptHistory {
  const sessionKey = sessionId ?? 'new'
  const entriesRef = useRef<string[] | null>(null)
  const navRef = useRef<{ index: number; savedDraft: string }>({ index: -1, savedDraft: '' })
  const draftTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingDraftRef = useRef<{ key: string; text: string } | null>(null)

  if (entriesRef.current === null) entriesRef.current = readHistory()

  const flushDraft = useCallback(() => {
    if (draftTimerRef.current !== null) {
      clearTimeout(draftTimerRef.current)
      draftTimerRef.current = null
    }
    const pending = pendingDraftRef.current
    pendingDraftRef.current = null
    if (pending !== null) writeDraft(pending.key, pending.text)
  }, [])

  // A pending debounced write must land on the session it was typed in.
  useEffect(() => flushDraft, [sessionKey, flushDraft])

  const reset = useCallback(() => {
    navRef.current = { index: -1, savedDraft: '' }
  }, [])

  const record = useCallback((text: string) => {
    const trimmed = text.trim()
    navRef.current = { index: -1, savedDraft: '' }
    if (trimmed.length === 0) return
    const entries = entriesRef.current ?? []
    const next = [trimmed, ...entries.filter(entry => entry !== trimmed)].slice(0, MAX_ENTRIES)
    entriesRef.current = next
    writeHistory(next)
  }, [])

  const navigate = useCallback((direction: 'up' | 'down', currentText: string, caret: number): HistoryNavigation | null => {
    const entries = entriesRef.current ?? []
    const nav = navRef.current
    const caretAtEdge = caret === 0 || caret === currentText.length

    if (direction === 'up') {
      if (entries.length === 0) return null
      if (nav.index === -1) {
        // Entering history is reserved for an empty draft (caret implicitly 0).
        if (currentText.length !== 0 || caret !== 0) return null
        nav.savedDraft = currentText
        nav.index = 0
        return { text: entries[0]!, caret: 'start' }
      }
      if (!caretAtEdge) return null
      if (nav.index >= entries.length - 1) return null
      nav.index += 1
      return { text: entries[nav.index]!, caret: 'start' }
    }

    if (nav.index === -1 || !caretAtEdge) return null
    if (nav.index > 0) {
      nav.index -= 1
      return { text: entries[nav.index]!, caret: 'end' }
    }
    const restored = nav.savedDraft
    nav.index = -1
    nav.savedDraft = ''
    return { text: restored, caret: 'end' }
  }, [])

  const loadDraft = useCallback(() => readDraft(draftStorageKey(sessionId)), [sessionId])

  const persistDraft = useCallback((text: string) => {
    const key = draftStorageKey(sessionKey)
    pendingDraftRef.current = { key, text }
    if (draftTimerRef.current !== null) clearTimeout(draftTimerRef.current)
    draftTimerRef.current = setTimeout(() => {
      draftTimerRef.current = null
      const pending = pendingDraftRef.current
      pendingDraftRef.current = null
      if (pending !== null) writeDraft(pending.key, pending.text)
    }, DRAFT_DEBOUNCE_MS)
  }, [sessionKey])

  const clearDraft = useCallback(() => {
    if (draftTimerRef.current !== null) {
      clearTimeout(draftTimerRef.current)
      draftTimerRef.current = null
    }
    pendingDraftRef.current = null
    try {
      localStorage.removeItem(draftStorageKey(sessionKey))
    } catch {
      // best-effort
    }
  }, [sessionKey])

  const isNavigating = useCallback(() => navRef.current.index >= 0, [])

  // Stable across renders for a given session so effects can depend on it.
  return useMemo<PromptHistory>(() => ({
    record,
    navigate,
    reset,
    isNavigating,
    loadDraft,
    persistDraft,
    clearDraft,
  }), [record, navigate, reset, isNavigating, loadDraft, persistDraft, clearDraft])
}
