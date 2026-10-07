export type ShortcutModifier = 'mod' | 'ctrl' | 'shift' | 'alt'

/**
 * Normalized key combo: modifiers sorted as mod+ctrl+alt+shift, then the key
 * lowercased — e.g. 'mod+f', 'shift+enter', 'escape', 'arrowup'.
 */
export type ShortcutKeys = string

export type ShortcutRegistration = {
  id: string
  keys: ShortcutKeys
  /** Human-readable line shown in the help dialog. */
  description: string
  handler?: (event: KeyboardEvent) => void
  /** Gate evaluated at dispatch time; absent or true = enabled. */
  when?: () => boolean
  /** Listed in the help page but dispatched by its owning component (Enter, Esc, arrows). */
  displayOnly?: boolean
}

export const SHORTCUTS = {
  sendMessage: 'enter',
  newline: 'shift+enter',
  historyPrevious: 'arrowup',
  historyNext: 'arrowdown',
  escape: 'escape',
  transcriptSearch: 'mod+f',
  shortcutHelp: 'mod+/',
  commandPalette: 'mod+k',
} as const

/** Built-in contract: listed in the help page even before a handler is wired. */
const BUILTIN_SHORTCUTS: ShortcutRegistration[] = [
  { id: 'composer.send', keys: SHORTCUTS.sendMessage, description: '发送消息（输入框内 Enter）', displayOnly: true },
  { id: 'composer.newline', keys: SHORTCUTS.newline, description: '换行（输入框内）', displayOnly: true },
  { id: 'composer.history-prev', keys: SHORTCUTS.historyPrevious, description: '空草稿时回放历史消息', displayOnly: true },
  { id: 'composer.history-next', keys: SHORTCUTS.historyNext, description: '历史回放中返回较新条目 / 草稿', displayOnly: true },
  { id: 'global.escape', keys: SHORTCUTS.escape, description: '逐级退出：菜单 → 中断运行 → 关闭对话框', displayOnly: true },
  { id: 'transcript.search', keys: SHORTCUTS.transcriptSearch, description: '转录内搜索' },
  { id: 'dialog.shortcut-help', keys: SHORTCUTS.shortcutHelp, description: '快捷键帮助（本页）' },
  { id: 'palette.command', keys: SHORTCUTS.commandPalette, description: '命令面板' },
]

const registrations = new Map<string, ShortcutRegistration>()

for (const builtin of BUILTIN_SHORTCUTS) registrations.set(builtin.id, builtin)

const MODIFIER_ORDER: ShortcutModifier[] = ['mod', 'ctrl', 'alt', 'shift']

export function normalizeKeys(input: string): ShortcutKeys {
  const parts = input.toLowerCase().split('+').map(part => part.trim()).filter(part => part.length > 0)
  const modifiers = MODIFIER_ORDER.filter(modifier => parts.includes(modifier))
  const key = parts.find(part => !(MODIFIER_ORDER as string[]).includes(part))
  if (key === undefined) throw new Error(`Shortcut "${input}" has no key`)
  return [...modifiers, key].join('+')
}

export function registerShortcut(registration: ShortcutRegistration): () => void {
  const existing = registrations.get(registration.id)
  // Re-registration replaces handler/when wholesale; the catalog's displayOnly
  // ownership flag is sticky so a handler can never make Enter/Esc dispatchable.
  const merged: ShortcutRegistration = {
    ...registration,
    keys: normalizeKeys(registration.keys),
    displayOnly: existing?.displayOnly ?? registration.displayOnly,
  }
  registrations.set(registration.id, merged)
  return () => {
    const current = registrations.get(registration.id)
    if (current !== merged) return
    if (existing === undefined) registrations.delete(registration.id)
    else registrations.set(registration.id, existing)
  }
}

export function listShortcuts(): ShortcutRegistration[] {
  return [...registrations.values()]
}

export function isApplePlatform(): boolean {
  return /Mac|iPhone|iPad|iPod/u.test(navigator.platform ?? '')
    || /Mac|iPhone|iPad|iPod/u.test(navigator.userAgent ?? '')
}

export function matchShortcut(keys: ShortcutKeys, event: KeyboardEvent): boolean {
  if (event.isComposing || event.keyCode === 229) return false
  const normalized = normalizeKeys(keys)
  const parts = normalized.split('+')
  const key = parts.at(-1)!
  const wants = (modifier: ShortcutModifier) => parts.includes(modifier)
  const apple = isApplePlatform()

  const wantsMod = wants('mod')
  // 'mod' = ⌘ on Apple, Ctrl elsewhere; never both pressed at once.
  if (wantsMod) {
    if (event.metaKey === event.ctrlKey) return false
    if (event.metaKey !== apple) return false
  } else if (event.metaKey || event.ctrlKey !== wants('ctrl')) {
    return false
  }
  if (event.altKey !== wants('alt')) return false
  // '/' is shift-insensitive: some layouts need Shift to produce it ('?').
  const shiftInsensitive = key === '/'
  if (!shiftInsensitive && event.shiftKey !== wants('shift')) return false

  const eventKey = event.key.toLowerCase()
  if (key === '/') return eventKey === '/' || eventKey === '?'
  return eventKey === key
}

/** Dispatch one keydown through the registry; true when a handler consumed it. */
export function dispatchShortcut(event: KeyboardEvent): boolean {
  for (const registration of registrations.values()) {
    if (registration.displayOnly || registration.handler === undefined) continue
    if (registration.when !== undefined && !registration.when()) continue
    if (!matchShortcut(registration.keys, event)) continue
    event.preventDefault()
    registration.handler(event)
    return true
  }
  return false
}

export function installGlobalShortcuts(target: Document = document): () => void {
  const onKeyDown = (event: KeyboardEvent): void => { dispatchShortcut(event) }
  target.addEventListener('keydown', onKeyDown)
  return () => { target.removeEventListener('keydown', onKeyDown) }
}

export function formatShortcutParts(keys: ShortcutKeys, apple = isApplePlatform()): string[] {
  const parts = normalizeKeys(keys).split('+')
  const key = parts.at(-1)!
  const modifierLabels: Record<ShortcutModifier, string> = apple
    ? { mod: '⌘', ctrl: '⌃', alt: '⌥', shift: '⇧' }
    : { mod: 'Ctrl', ctrl: 'Ctrl', alt: 'Alt', shift: 'Shift' }
  const keyLabels: Record<string, string> = {
    enter: apple ? '⏎' : 'Enter',
    escape: apple ? 'esc' : 'Esc',
    arrowup: '↑',
    arrowdown: '↓',
    arrowleft: '←',
    arrowright: '→',
    '/': '/',
  }
  return [
    ...(parts.slice(0, -1) as ShortcutModifier[]).map(modifier => modifierLabels[modifier]),
    keyLabels[key] ?? key.toUpperCase(),
  ]
}

export function formatShortcut(keys: ShortcutKeys, apple = isApplePlatform()): string {
  return formatShortcutParts(keys, apple).join(apple ? '' : '+')
}
