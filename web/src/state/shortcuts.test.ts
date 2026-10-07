import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  SHORTCUTS,
  dispatchShortcut,
  formatShortcut,
  installGlobalShortcuts,
  listShortcuts,
  matchShortcut,
  normalizeKeys,
  registerShortcut,
} from './shortcuts'

const keydown = (init: KeyboardEventInit): KeyboardEvent =>
  new KeyboardEvent('keydown', { cancelable: true, ...init })

describe('shortcuts registry', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('ships the built-in contract entries for the help page', () => {
    const ids = listShortcuts().map(entry => entry.id)
    expect(ids).toEqual(expect.arrayContaining([
      'composer.send',
      'composer.newline',
      'global.escape',
      'transcript.search',
      'dialog.shortcut-help',
      'palette.command',
    ]))
    const search = listShortcuts().find(entry => entry.id === 'transcript.search')!
    expect(search.keys).toBe('mod+f')
  })

  it('normalizes combos and matches mod on the current platform', () => {
    expect(normalizeKeys('Shift+Enter')).toBe('shift+enter')
    expect(normalizeKeys('CTRL + shift + P')).toBe('ctrl+shift+p')

    // jsdom reports a non-Apple platform, so mod resolves to Ctrl.
    expect(matchShortcut('mod+f', keydown({ key: 'f', ctrlKey: true }))).toBe(true)
    expect(matchShortcut('mod+f', keydown({ key: 'f' }))).toBe(false)
    expect(matchShortcut('mod+f', keydown({ key: 'f', ctrlKey: true, altKey: true }))).toBe(false)
    // Composition and the Windows-mode flag must never fire shortcuts.
    expect(matchShortcut('mod+f', keydown({ key: 'f', ctrlKey: true, isComposing: true }))).toBe(false)
  })

  it('accepts ? for mod+/ because Shift+/ produces ? on some layouts', () => {
    expect(matchShortcut('mod+/', keydown({ key: '/', ctrlKey: true }))).toBe(true)
    expect(matchShortcut('mod+/', keydown({ key: '?', ctrlKey: true, shiftKey: true }))).toBe(true)
  })

  it('dispatches to registered handlers honoring the when() gate', () => {
    const handler = vi.fn()
    const off = registerShortcut({
      id: 'test.dispatch',
      keys: 'mod+shift+x',
      description: 'test',
      handler,
      when: () => false,
    })

    const event = keydown({ key: 'x', ctrlKey: true, shiftKey: true })
    expect(dispatchShortcut(event)).toBe(false)
    expect(handler).not.toHaveBeenCalled()

    const offOpen = registerShortcut({
      id: 'test.dispatch',
      keys: 'mod+shift+x',
      description: 'test',
      handler,
    })
    const event2 = keydown({ key: 'x', ctrlKey: true, shiftKey: true })
    expect(dispatchShortcut(event2)).toBe(true)
    expect(handler).toHaveBeenCalledTimes(1)
    expect(event2.defaultPrevented).toBe(true)

    offOpen()
    off()
    expect(dispatchShortcut(keydown({ key: 'x', ctrlKey: true, shiftKey: true }))).toBe(false)
  })

  it('never dispatches displayOnly entries even with a handler registered', () => {
    const handler = vi.fn()
    registerShortcut({ id: 'composer.send', keys: 'enter', description: '发送', handler })
    expect(dispatchShortcut(keydown({ key: 'Enter' }))).toBe(false)
    expect(handler).not.toHaveBeenCalled()
    // unregistering restores the built-in displayOnly row, not a hole.
    const entry = listShortcuts().find(item => item.id === 'composer.send')
    expect(entry?.displayOnly).toBe(true)
  })

  it('installGlobalShortcuts binds document keydown and detaches cleanly', () => {
    const handler = vi.fn()
    const off = registerShortcut({ id: 'test.global', keys: 'mod+g', description: 'test', handler })
    const detach = installGlobalShortcuts(document)

    const event = keydown({ key: 'g', ctrlKey: true })
    document.dispatchEvent(event)
    expect(handler).toHaveBeenCalledTimes(1)

    detach()
    document.dispatchEvent(keydown({ key: 'g', ctrlKey: true }))
    expect(handler).toHaveBeenCalledTimes(1)
    off()
  })

  it('formats combos for display on both platform families', () => {
    expect(formatShortcut(SHORTCUTS.transcriptSearch, false)).toBe('Ctrl+F')
    expect(formatShortcut(SHORTCUTS.transcriptSearch, true)).toBe('⌘F')
    expect(formatShortcut(SHORTCUTS.shortcutHelp, true)).toBe('⌘/')
    expect(formatShortcut(SHORTCUTS.newline, false)).toBe('Shift+Enter')
  })
})
