import { useEffect, useId, useRef, useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'

import {
  SHORTCUTS,
  formatShortcutParts,
  installGlobalShortcuts,
  listShortcuts,
  registerShortcut,
} from '../state/shortcuts'
import { useToastOptional } from './toast'
import styles from './ShortcutHelpDialog.module.css'

export function ShortcutHelpDialog({ onClose }: { onClose: () => void }) {
  const titleId = useId()
  const closeRef = useRef<HTMLButtonElement>(null)
  const [entries] = useState(() => listShortcuts())

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        onClose()
      }
    }
    // Capture phase: the help page owns Esc over any underlying UI.
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.body.style.overflow = overflow
      previous?.focus()
    }
  }, [onClose])

  const onBackdropClick = (event: MouseEvent<HTMLDivElement>): void => {
    if (event.target === event.currentTarget) onClose()
  }

  return createPortal(
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-labelledby={titleId} onClick={onBackdropClick}>
      <section className={styles.dialog}>
        <header>
          <h2 id={titleId}>键盘快捷键</h2>
          <button ref={closeRef} type="button" aria-label="关闭快捷键帮助" onClick={onClose}>×</button>
        </header>
        <ul className={styles.list}>
          {entries.map(entry => (
            <li key={entry.id} className={styles.row}>
              <span className={styles.description}>{entry.description}</span>
              <span className={styles.keys}>
                {formatShortcutParts(entry.keys).map((part, index) => <kbd key={index}>{part}</kbd>)}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>,
    document.body,
  )
}

/**
 * Self-contained global-shortcut wiring: installs the document-level dispatch,
 * registers the built-in handlers (Mod+/ toggles this help page, Mod+F forwards
 * to the transcript search owner, Mod+K is a command-palette placeholder).
 * Mount once inside <ToastProvider>; without a provider Mod+K is a no-op.
 */
export function GlobalShortcuts({ onTranscriptSearch }: { onTranscriptSearch?: () => void }) {
  const [helpOpen, setHelpOpen] = useState(false)
  const toast = useToastOptional()

  useEffect(() => {
    const unregister = [
      registerShortcut({
        id: 'transcript.search',
        keys: SHORTCUTS.transcriptSearch,
        description: '转录内搜索',
        handler: () => { onTranscriptSearch?.() },
        when: () => onTranscriptSearch !== undefined,
      }),
      registerShortcut({
        id: 'dialog.shortcut-help',
        keys: SHORTCUTS.shortcutHelp,
        description: '快捷键帮助（本页）',
        handler: () => { setHelpOpen(open => !open) },
      }),
      registerShortcut({
        id: 'palette.command',
        keys: SHORTCUTS.commandPalette,
        description: '命令面板',
        handler: () => {
          toast?.show({ title: '命令面板即将上线', description: 'Command palette — coming soon' })
        },
        when: () => toast !== null,
      }),
    ]
    const detach = installGlobalShortcuts()
    return () => {
      detach()
      for (const off of unregister) off()
    }
  }, [onTranscriptSearch, toast])

  return helpOpen ? <ShortcutHelpDialog onClose={() => { setHelpOpen(false) }} /> : null
}
