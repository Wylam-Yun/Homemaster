import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'

import type { ProviderInfo, ProviderModels, SessionStatusInfo } from '../../api/http'
import type { UiMode, Usage } from '../../protocol/events'
import type { ApprovalState, PendingQuestionState } from '../../state/conversation'
import { useToastOptional } from '../toast'
import { IconPaperclip } from '../ui/icons'
import { AttachmentRail, type AttachmentDraft } from './AttachmentRail'
import { ContextMeter } from './ContextMeter'
import { ModeBadge } from './ModeBadge'
import { ModelPicker, type ModelSelection } from './ModelPicker'
import { ApprovalWaitingCard, QuestionCard } from './TakeoverCard'
import { draftStorageKey, usePromptHistory } from './usePromptHistory'
import styles from './ComposerPanel.module.css'

export type { AttachmentDraft } from './AttachmentRail'
export type { ModelSelection } from './ModelPicker'

export interface SlashCommand {
  name: string
  description?: string
  /** Optional group header for the popover (e.g. 'builtin' / 'custom'). */
  group?: string
}

export interface MentionItem {
  id: string
  name: string
  description?: string
  /** Text inserted after '@'; defaults to name. */
  insertText?: string
}

export interface ComposerPanelProps {
  sessionId: string | null
  busy: boolean                  // run 进行中 → 显示 Stop 按钮位 + Enter 走 onEnqueue
  disabled?: boolean
  // Resolves true once the text was accepted for send; false/rejection keeps the input.
  onSubmit(text: string, attachments: AttachmentDraft[]): void | Promise<boolean>
  onCancel(): void               // Stop 按钮
  slashCommands: SlashCommand[]  // 由父级注入 [{name, description}]
  resolveMentions(query: string): Promise<MentionItem[]>  // @ 候选（本期父级传空数组即可）
  // busy 时提交 → 进本地队列 dock，由父级在回合终态按序发送。
  onEnqueue?(text: string): void
  /** Parent-driven draft injection (消息编辑): each {seq,text} appends to the draft once. */
  injectDraft?: { seq: number; text: string } | null
  /** Plan/act mode badge + composer styling; onModeChange POSTs /{id}/mode upstream. */
  mode?: UiMode
  onModeChange?(mode: UiMode): void
  /** Provider 下拉 + 自定义 model id；选择逐消息透传给 sendMessage。 */
  providers?: ProviderInfo[]
  modelValue?: ModelSelection
  onModelChange?(selection: ModelSelection): void
  /** Lazy provider model enumeration for the picker's model dropdown. */
  loadModels?(providerName: string): Promise<ProviderModels>
  /** 上下文用量环：最近回合 usage + session status 明细 + compact 按钮。 */
  usage?: Usage | null
  sessionStatus?: SessionStatusInfo | null
  compacting?: boolean
  onCompact?(): void
  /** Composer 接管：pending question 换成问答卡；pending approval 显示等待态。 */
  pendingQuestion?: PendingQuestionState | null
  onAnswerQuestion?(questionId: string, text: string): Promise<boolean> | boolean
  pendingApproval?: ApprovalState | null
}

const MAX_TEXTAREA_HEIGHT_PX = 144 // ~6 rows at line-height 1.5
const MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024 // 10 MiB

type MenuState =
  | { kind: 'slash'; query: string; tokenStart: number }
  | { kind: 'mention'; query: string; tokenStart: number; items: MentionItem[] }

/** '/cmd' token: '/' must start the text or follow whitespace (incl. newlines). */
const SLASH_TRIGGER = /(?:^|\s)\/(\S*)$/
const MENTION_TRIGGER = /(?:^|\s)@(\S*)$/

export function ComposerPanel({
  sessionId,
  busy,
  disabled = false,
  onSubmit,
  onCancel,
  slashCommands,
  resolveMentions,
  onEnqueue,
  injectDraft,
  mode = 'act',
  onModeChange,
  providers,
  modelValue,
  onModelChange,
  loadModels,
  usage,
  sessionStatus,
  compacting = false,
  onCompact,
  pendingQuestion,
  onAnswerQuestion,
  pendingApproval,
}: ComposerPanelProps) {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([])
  const [menu, setMenu] = useState<MenuState | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const composingRef = useRef(false)
  const pendingCaretRef = useRef<number | null>(null)
  const mentionSeqRef = useRef(0)
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const listboxId = useId()
  const history = usePromptHistory(sessionId)
  const toast = useToastOptional()

  // Per-session draft restore + reset transient UI when switching sessions.
  const prevSessionIdRef = useRef<string | null>(sessionId)
  useEffect(() => {
    const previous = prevSessionIdRef.current
    prevSessionIdRef.current = sessionId
    let restored = history.loadDraft()
    let adoptTarget: string | null = null
    try { adoptTarget = localStorage.getItem('hm.draft.adopt') } catch { /* ignore */ }
    if (previous === null && sessionId !== null && adoptTarget === sessionId) {
      // 仅在草稿物化（带 hm.draft.adopt 令牌）时领养 hm.draft.new；用户在草稿态
      // 打字后直接点已有老会话不会走这里，两边草稿各自保留。
      try { localStorage.removeItem('hm.draft.adopt') } catch { /* ignore */ }
      const orphan = localStorage.getItem(draftStorageKey(null))
      if (orphan !== null) localStorage.removeItem(draftStorageKey(null))
      if (orphan !== null && orphan.length > 0) {
        restored = orphan
        history.persistDraft(orphan)
      }
    }
    setText(restored)
    setMenu(null)
    setNotice(null)
    if (noticeTimerRef.current !== null) {
      clearTimeout(noticeTimerRef.current)
      noticeTimerRef.current = null
    }
    // Attachments belong to the session they were staged in; revoke their blob
    // URLs instead of leaking them into the next session's draft.
    for (const attachment of attachmentsRef.current) URL.revokeObjectURL(attachment.blobUrl)
    setAttachments(previous => (previous.length === 0 ? previous : []))
    history.reset()
  }, [sessionId, history])

  // Clear the pending notice auto-dismiss timer on unmount.
  useEffect(() => () => {
    if (noticeTimerRef.current !== null) clearTimeout(noticeTimerRef.current)
  }, [])

  // Revoke blob URLs for attachments still held when unmounted.
  const attachmentsRef = useRef(attachments)
  attachmentsRef.current = attachments
  useEffect(() => () => {
    for (const attachment of attachmentsRef.current) URL.revokeObjectURL(attachment.blobUrl)
  }, [])

  // 消息编辑：外部注入文本进入草稿（有草稿时换行追加，不覆盖用户输入）。
  const lastInjectSeqRef = useRef(0)
  useEffect(() => {
    if (injectDraft === undefined || injectDraft === null || injectDraft.seq === lastInjectSeqRef.current) return
    lastInjectSeqRef.current = injectDraft.seq
    const base = textareaRef.current?.value ?? text
    const next = base.length === 0 ? injectDraft.text : `${base}\n${injectDraft.text}`
    pendingCaretRef.current = next.length
    setText(next)
    history.persistDraft(next)
    history.reset()
  }, [injectDraft, text, history])

  const notifyUnsupported = useCallback((message: string) => {
    if (toast !== null) {
      toast.show({ title: message })
      return
    }
    setNotice(message)
    if (noticeTimerRef.current !== null) clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = setTimeout(() => { setNotice(null) }, 5000)
  }, [toast])

  const addFiles = useCallback((files: Iterable<File>) => {
    const incoming = Array.from(files)
    if (incoming.length === 0) return
    const accepted: AttachmentDraft[] = []
    for (const file of incoming) {
      if (file.size === 0) {
        notifyUnsupported(`文件「${file.name}」为空，已跳过`)
        continue
      }
      if (file.size > MAX_ATTACHMENT_SIZE) {
        notifyUnsupported(`文件「${file.name}」超过 10 MB 大小限制`)
        continue
      }
      const blobUrl = typeof URL.createObjectURL === 'function' ? URL.createObjectURL(file) : ''
      accepted.push({
        id: crypto.randomUUID(),
        name: file.name,
        size: file.size,
        mime: file.type || 'application/octet-stream',
        blobUrl,
        file,
      })
    }
    if (accepted.length > 0) {
      setAttachments(previous => [...previous, ...accepted])
    }
  }, [notifyUnsupported])

  // Full-page drag overlay: only drags carrying Files raise it.
  useEffect(() => {
    let depth = 0
    const hasFiles = (event: DragEvent): boolean =>
      Array.from(event.dataTransfer?.types ?? []).includes('Files')
    const onDragEnter = (event: DragEvent): void => {
      if (!hasFiles(event)) return
      depth += 1
      setDragging(true)
    }
    const onDragOver = (event: DragEvent): void => {
      if (hasFiles(event)) event.preventDefault()
    }
    const onDragLeave = (event: DragEvent): void => {
      if (!hasFiles(event)) return
      depth = Math.max(0, depth - 1)
      if (depth === 0) setDragging(false)
    }
    const onDrop = (event: DragEvent): void => {
      if (!hasFiles(event)) return
      event.preventDefault()
      depth = 0
      setDragging(false)
      addFiles(event.dataTransfer?.files ?? [])
    }
    document.addEventListener('dragenter', onDragEnter)
    document.addEventListener('dragover', onDragOver)
    document.addEventListener('dragleave', onDragLeave)
    document.addEventListener('drop', onDrop)
    return () => {
      document.removeEventListener('dragenter', onDragEnter)
      document.removeEventListener('dragover', onDragOver)
      document.removeEventListener('dragleave', onDragLeave)
      document.removeEventListener('drop', onDrop)
    }
  }, [addFiles])

  const slashItems = (() => {
    if (menu?.kind !== 'slash') return []
    const query = menu.query.toLowerCase()
    const matched = slashCommands.filter(command => command.name.toLowerCase().includes(query))
    const prefix: SlashCommand[] = []
    const rest: SlashCommand[] = []
    for (const command of matched) {
      if (command.name.toLowerCase().startsWith(query)) prefix.push(command)
      else rest.push(command)
    }
    return [...prefix, ...rest]
  })()

  const optionCount = menu === null ? 0 : menu.kind === 'slash' ? slashItems.length : menu.items.length
  const activeOptionId = menu !== null && optionCount > 0 ? `${listboxId}-${activeIndex}` : undefined

  // Apply a caret position queued by history navigation / completion insertion.
  useLayoutEffect(() => {
    const position = pendingCaretRef.current
    if (position === null) return
    pendingCaretRef.current = null
    const textarea = textareaRef.current
    if (textarea === null) return
    textarea.focus()
    const clamped = Math.min(position, textarea.value.length)
    textarea.setSelectionRange(clamped, clamped)
  }, [text])

  // Multi-line autoresize (1–6 rows); jsdom reports scrollHeight 0, CSS covers it.
  useLayoutEffect(() => {
    const textarea = textareaRef.current
    if (textarea === null) return
    textarea.style.height = 'auto'
    if (textarea.scrollHeight > 0) {
      textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_TEXTAREA_HEIGHT_PX)}px`
    }
  }, [text])

  const removeAttachment = useCallback((id: string) => {
    setAttachments(previous => {
      const target = previous.find(attachment => attachment.id === id)
      if (target !== undefined) URL.revokeObjectURL(target.blobUrl)
      return previous.filter(attachment => attachment.id !== id)
    })
  }, [])

  const closeMenu = useCallback(() => {
    mentionSeqRef.current += 1
    setMenu(null)
  }, [])

  /** Recompute popover trigger from the text before the caret. */
  const updateTrigger = useCallback((value: string, caret: number) => {
    const before = value.slice(0, caret)
    const slash = SLASH_TRIGGER.exec(before)
    if (slash !== null && slashCommands.length > 0) {
      setMenu({ kind: 'slash', query: slash[1]!, tokenStart: caret - slash[1]!.length - 1 })
      setActiveIndex(0)
      return
    }
    const mention = MENTION_TRIGGER.exec(before)
    if (mention !== null) {
      const query = mention[1]!
      const tokenStart = caret - query.length - 1
      const seq = ++mentionSeqRef.current
      // A stale slash menu must not accept Enter while mention lookup is in flight.
      setMenu(previous => (previous?.kind === 'mention' ? previous : null))
      void resolveMentions(query).then(items => {
        if (seq !== mentionSeqRef.current) return
        if (items.length === 0) {
          setMenu(null)
          return
        }
        setMenu({ kind: 'mention', query, tokenStart, items })
        setActiveIndex(0)
      }, () => {
        if (seq === mentionSeqRef.current) setMenu(null)
      })
      return
    }
    mentionSeqRef.current += 1
    setMenu(null)
  }, [resolveMentions, slashCommands.length])

  const onChange = (event: ChangeEvent<HTMLTextAreaElement>): void => {
    const next = event.target.value
    setText(next)
    history.persistDraft(next)
    history.reset()
    updateTrigger(next, event.target.selectionStart ?? next.length)
  }

  const applyCompletion = (replacement: string, tokenStart: number): void => {
    const textarea = textareaRef.current
    const caret = textarea?.selectionStart ?? text.length
    const next = text.slice(0, tokenStart) + replacement + text.slice(caret)
    pendingCaretRef.current = tokenStart + replacement.length
    setText(next)
    history.persistDraft(next)
    closeMenu()
  }

  const selectActive = (): void => {
    if (menu === null || optionCount === 0) return
    if (menu.kind === 'slash') {
      const command = slashItems[Math.min(activeIndex, slashItems.length - 1)]!
      applyCompletion(`/${command.name} `, menu.tokenStart)
      return
    }
    const item = menu.items[Math.min(activeIndex, menu.items.length - 1)]!
    applyCompletion(`@${item.insertText ?? item.name} `, menu.tokenStart)
  }

  const submit = (): void => {
    if (disabled) return
    const trimmed = text.trim()
    const stagedAttachments = attachments
    if (trimmed.length === 0 && stagedAttachments.length === 0) return
    // busy 时 Enter/发送按钮把消息排进本地队列——输入不丢，回合终态自动按序发出。
    if (busy) {
      if (stagedAttachments.length > 0) {
        notifyUnsupported('附件暂不支持排队发送，请等待当前任务完成后再试。')
        return
      }
      if (onEnqueue === undefined) {
        notifyUnsupported('当前任务仍在运行，请稍候再发送。')
        return
      }
      onEnqueue(trimmed)
      history.record(trimmed)
      setText('')
      history.clearDraft()
      closeMenu()
      textareaRef.current?.focus()
      return
    }
    // Only a confirmed send clears the input; a failed/rejected send keeps the
    // text and persists it as the session draft so typing is never lost.
    const finish = (sent: boolean): void => {
      if (sent) {
        if (trimmed.length > 0) history.record(trimmed)
        const current = textareaRef.current?.value ?? text
        const leftover = current === text
          ? ''
          : current.startsWith(text) ? current.slice(text.length) : current
        setText(leftover)
        if (leftover.length === 0) history.clearDraft()
        else history.persistDraft(leftover)
        for (const item of stagedAttachments) {
          if (item.blobUrl) URL.revokeObjectURL(item.blobUrl)
        }
        setAttachments(previous => previous.filter(item => !stagedAttachments.some(s => s.id === item.id)))
        closeMenu()
      } else {
        history.persistDraft(text)
      }
      textareaRef.current?.focus()
    }
    let outcome: void | Promise<boolean>
    try {
      outcome = onSubmit(trimmed, stagedAttachments)
    } catch {
      outcome = Promise.resolve(false)
    }
    if (outcome instanceof Promise) {
      void outcome.then(sent => { finish(sent === true) }, () => { finish(false) })
      return
    }
    finish(true)
  }

  const isImeComposing = (event: ReactKeyboardEvent<HTMLTextAreaElement>): boolean =>
    composingRef.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229

  const onKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>): void => {
    // IME composition owns Enter/arrows; Shift+Enter is never an IME commit.
    if (isImeComposing(event)) return

    if (menu !== null) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        if (optionCount > 0) {
          const delta = event.key === 'ArrowDown' ? 1 : -1
          setActiveIndex(index => (index + delta + optionCount) % optionCount)
        }
        return
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault()
        if (optionCount === 0) closeMenu()
        else selectActive()
        return
      }
      if (event.key === 'Escape') {
        // Esc layer 1: close the menu, keep the draft.
        event.preventDefault()
        event.stopPropagation()
        closeMenu()
        return
      }
    }

    if (event.key === 'Enter') {
      if (event.shiftKey) return // default newline
      event.preventDefault()
      if (event.repeat) return
      submit()
      return
    }

    if (event.key === 'Escape') {
      // Esc layer 2: interrupt a running turn.
      if (busy) {
        event.preventDefault()
        onCancel()
      }
      return
    }

    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
      const textarea = event.currentTarget
      if (textarea.selectionStart !== textarea.selectionEnd) return
      const result = history.navigate(
        event.key === 'ArrowUp' ? 'up' : 'down',
        text,
        textarea.selectionStart,
      )
      if (result !== null) {
        event.preventDefault()
        pendingCaretRef.current = result.caret === 'start' ? 0 : result.text.length
        setText(result.text)
      }
    }
  }

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>): void => {
    const files = Array.from(event.clipboardData?.files ?? [])
    if (files.length === 0) return
    event.preventDefault()
    addFiles(files)
  }

  const canSend = !disabled && !busy && (text.trim().length > 0 || attachments.length > 0)

  const menuGroups = (() => {
    if (menu?.kind !== 'slash') return null
    const anyGroup = slashItems.some(command => command.group !== undefined)
    if (!anyGroup) return null
    const groups = new Map<string, SlashCommand[]>()
    for (const command of slashItems) {
      const label = command.group ?? ''
      const bucket = groups.get(label) ?? []
      bucket.push(command)
      groups.set(label, bucket)
    }
    return [...groups.entries()]
  })()

  let optionCursor = -1
  const nextOptionIndex = (): number => {
    optionCursor += 1
    return optionCursor
  }

  const selectSlash = (command: SlashCommand): void => {
    applyCompletion(`/${command.name} `, menu?.kind === 'slash' ? menu.tokenStart : 0)
  }

  const selectMention = (item: MentionItem): void => {
    applyCompletion(`@${item.insertText ?? item.name} `, menu?.kind === 'mention' ? menu.tokenStart : 0)
  }

  const renderSlashOption = (command: SlashCommand) => {
    const index = nextOptionIndex()
    const id = `${listboxId}-${index}`
    return (
      <div
        key={command.name}
        id={id}
        role="option"
        aria-selected={index === activeIndex}
        className={styles.option}
        data-active={index === activeIndex || undefined}
        onClick={() => { selectSlash(command) }}
        onPointerMove={() => { setActiveIndex(index) }}
      >
        <span className={styles.optionName}>/{command.name}</span>
        {command.description !== undefined && <span className={styles.optionHint}>{command.description}</span>}
      </div>
    )
  }

  const showChrome = providers !== undefined
    || onModeChange !== undefined
    || onCompact !== undefined
    || sessionStatus !== undefined
    || usage !== undefined
  const canEnqueue = !disabled && onEnqueue !== undefined && text.trim().length > 0 && attachments.length === 0
  const placeholder = disabled
    ? '等待连接恢复…'
    : mode === 'plan'
      ? '规划模式：描述目标，先讨论计划再执行…'
      : '输入消息…'

  return (
    <div className={styles.composer} data-mode={mode}>
      {showChrome && (
        <div className={styles.toolbar}>
          {providers !== undefined && (
            <ModelPicker
              providers={providers}
              value={modelValue ?? {}}
              disabled={disabled}
              onChange={selection => { onModelChange?.(selection) }}
              loadModels={loadModels}
            />
          )}
          <span className={styles.toolbarSpacer} />
          <ContextMeter
            usage={usage ?? null}
            status={sessionStatus ?? null}
            disabled={disabled || busy}
            compacting={compacting}
            onCompact={onCompact}
          />
          <ModeBadge mode={mode} disabled={disabled || onModeChange === undefined} onToggle={next => { onModeChange?.(next) }} />
        </div>
      )}
      <AttachmentRail attachments={attachments} onRemove={removeAttachment} />
      {pendingQuestion !== undefined && pendingQuestion !== null ? (
        <QuestionCard
          question={pendingQuestion}
          disabled={disabled || onAnswerQuestion === undefined}
          onSubmit={(questionId, text) => onAnswerQuestion?.(questionId, text) ?? false}
          onCancel={busy ? onCancel : undefined}
        />
      ) : pendingApproval !== undefined && pendingApproval !== null ? (
        <ApprovalWaitingCard itemCount={pendingApproval.items.length} />
      ) : (
      <div className={styles.editor}>
        <textarea
          ref={textareaRef}
          rows={1}
          value={text}
          disabled={disabled}
          placeholder={placeholder}
          aria-label="消息输入"
          aria-expanded={menu !== null || undefined}
          aria-controls={menu !== null ? listboxId : undefined}
          aria-activedescendant={activeOptionId}
          aria-autocomplete="list"
          onChange={onChange}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={() => { closeMenu() }}
          onCompositionStart={() => { composingRef.current = true }}
          onCompositionEnd={() => { composingRef.current = false }}
        />
        {menu !== null && (
          <div
            className={styles.popover}
            onMouseDown={event => { event.preventDefault() }}
          >
            <div className={styles.menu} role="listbox" id={listboxId} aria-label={menu.kind === 'slash' ? '命令' : '提及'}>
              {menu.kind === 'slash' && slashItems.length === 0 && (
                <div className={styles.empty}>无匹配命令</div>
              )}
              {menu.kind === 'slash' && menuGroups === null && slashItems.map(renderSlashOption)}
              {menu.kind === 'slash' && menuGroups !== null && menuGroups.map(([group, items]) => (
                <div key={group || 'default'} role="group" aria-label={group || undefined}>
                  {group !== '' && <div className={styles.groupLabel}>{group}</div>}
                  {items.map(renderSlashOption)}
                </div>
              ))}
              {menu.kind === 'mention' && menu.items.map(item => {
                const index = nextOptionIndex()
                const id = `${listboxId}-${index}`
                return (
                  <div
                    key={item.id}
                    id={id}
                    role="option"
                    aria-selected={index === activeIndex}
                    className={styles.option}
                    data-active={index === activeIndex || undefined}
                    onClick={() => { selectMention(item) }}
                    onPointerMove={() => { setActiveIndex(index) }}
                  >
                    <span className={styles.optionName}>@{item.name}</span>
                    {item.description !== undefined && <span className={styles.optionHint}>{item.description}</span>}
                  </div>
                )
              })}
            </div>
          </div>
        )}
        {busy ? (
          <>
            {onEnqueue !== undefined && (
              <button
                className={styles.queue}
                type="button"
                disabled={!canEnqueue}
                aria-label="排队发送"
                title="加入队列，当前回合结束后自动发送"
                onClick={submit}
              >
                ⇥
              </button>
            )}
            <button className={styles.stop} type="button" onClick={onCancel} aria-label="停止运行" title="停止当前回合">■</button>
          </>
        ) : (
          <button
            className={styles.send}
            type="button"
            disabled={!canSend}
            aria-label="发送消息"
            onClick={submit}
          >
            ↑
          </button>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          aria-label="选择附件文件"
          onChange={event => {
            const list = event.target.files
            if (list && list.length > 0) addFiles(list)
            event.target.value = ''
          }}
        />
        <div className={styles.bottomBar}>
          <button
            type="button"
            className={styles.attachBtn}
            disabled={disabled}
            aria-label="添加附件"
            title="上传图片、代码或文档附件（也可直接粘贴或拖拽）"
            onClick={() => { fileInputRef.current?.click() }}
          >
            <IconPaperclip size={14} /> 添加附件
          </button>
          <small className={styles.hint}>
            {busy && onEnqueue !== undefined ? 'Enter 加入队列 · Esc 中断运行' : 'Enter 发送 · Shift+Enter 换行 · 支持粘贴/拖拽文件'}
          </small>
        </div>
      </div>
      )}
      {notice !== null && <div className={styles.notice} role="alert">{notice}</div>}
      {dragging && (
        <div className={styles.dropOverlay} role="presentation">
          <div className={styles.dropBox}>松开鼠标即可添加附件</div>
        </div>
      )}
    </div>
  )
}
