import { act, createEvent, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ToastProvider, useToast } from '../toast'
import { ComposerPanel, type ComposerPanelProps, type MentionItem, type SlashCommand } from './ComposerPanel'

const COMMANDS: SlashCommand[] = [
  { name: 'new', description: '新建会话', group: '会话' },
  { name: 'compact', description: '压缩上下文', group: '会话' },
  { name: 'status', description: '查看状态', group: '系统' },
  { name: 'doctor', description: '环境体检', group: '系统' },
]

const noMentions = (): Promise<MentionItem[]> => Promise.resolve([])

function renderComposer(overrides: Partial<ComposerPanelProps> = {}) {
  const props: ComposerPanelProps = {
    sessionId: 'session-01',
    busy: false,
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    slashCommands: COMMANDS,
    resolveMentions: noMentions,
    ...overrides,
  }
  render(<ComposerPanel {...props} />)
  return props
}

function composerInput(): HTMLTextAreaElement {
  return screen.getByRole('textbox', { name: 'Message' })
}

describe('ComposerPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    let counter = 0
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => `blob:mock-${counter += 1}`),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('submits the trimmed text on Enter and clears the draft', () => {
    const props = renderComposer()
    const input = composerInput()

    fireEvent.change(input, { target: { value: '  去厨房看看  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(props.onSubmit).toHaveBeenCalledWith('去厨房看看', [])
    expect(input.value).toBe('')
    expect(JSON.parse(localStorage.getItem('hm.promptHistory') ?? '[]')).toEqual(['去厨房看看'])
  })

  it('keeps the input and persists the draft when onSubmit resolves false', async () => {
    const onSubmit = vi.fn(async () => false)
    renderComposer({ onSubmit })
    const input = composerInput()

    fireEvent.change(input, { target: { value: 'keep me' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => { expect(onSubmit).toHaveBeenCalledWith('keep me', []) })
    expect(input.value).toBe('keep me')
    // A failed send must not land in history nor clear the per-session draft.
    await waitFor(() => { expect(localStorage.getItem('hm.draft.session-01')).toBe('keep me') })
    expect(JSON.parse(localStorage.getItem('hm.promptHistory') ?? '[]')).toEqual([])
  })

  it('keeps the input when onSubmit rejects', async () => {
    const onSubmit = vi.fn(async (): Promise<boolean> => { throw new Error('offline') })
    renderComposer({ onSubmit })
    const input = composerInput()

    fireEvent.change(input, { target: { value: 'still here' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => { expect(onSubmit).toHaveBeenCalled() })
    await waitFor(() => { expect(localStorage.getItem('hm.draft.session-01')).toBe('still here') })
    expect(input.value).toBe('still here')
  })

  it('clears the input only after an async onSubmit resolves true', async () => {
    let resolveSend: (sent: boolean) => void = () => {}
    const onSubmit = vi.fn(() => new Promise<boolean>(resolve => { resolveSend = resolve }))
    renderComposer({ onSubmit })
    const input = composerInput()

    fireEvent.change(input, { target: { value: 'in flight' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    // The text stays put while the send is in flight.
    expect(input.value).toBe('in flight')

    await act(async () => { resolveSend(true) })
    expect(input.value).toBe('')
    expect(JSON.parse(localStorage.getItem('hm.promptHistory') ?? '[]')).toEqual(['in flight'])
  })

  it('clears staged attachments when the session changes', () => {
    const base: ComposerPanelProps = {
      sessionId: 'session-01',
      busy: false,
      onSubmit: vi.fn(),
      onCancel: vi.fn(),
      slashCommands: COMMANDS,
      resolveMentions: noMentions,
    }
    const { rerender } = render(<ComposerPanel {...base} />)
    const input = composerInput()
    const file = new File(['pixels'], 'shot.png', { type: 'image/png' })

    const paste = createEvent.paste(input)
    Object.defineProperty(paste, 'clipboardData', { value: { files: [file] } })
    fireEvent(input, paste)
    expect(screen.getByRole('button', { name: '预览 shot.png' })).toBeVisible()

    rerender(<ComposerPanel {...base} sessionId="session-02" />)
    expect(screen.queryByRole('button', { name: '预览 shot.png' })).toBeNull()
    expect(URL.revokeObjectURL).toHaveBeenCalled()
  })

  it('does not submit on Shift+Enter or while disabled/busy', () => {
    const props = renderComposer()
    const input = composerInput()
    fireEvent.change(input, { target: { value: 'draft' } })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(props.onSubmit).not.toHaveBeenCalled()
  })

  it('does not send Enter while an IME composition is active', () => {
    const props = renderComposer()
    const input = composerInput()
    fireEvent.change(input, { target: { value: '中文' } })

    fireEvent.compositionStart(input)
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(props.onSubmit).not.toHaveBeenCalled()

    fireEvent.compositionEnd(input)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(props.onSubmit).toHaveBeenCalledWith('中文', [])
  })

  it('replays prompt history on ArrowUp from an empty draft and restores the draft on ArrowDown', () => {
    const props = renderComposer()
    const input = composerInput()

    fireEvent.change(input, { target: { value: 'first prompt' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.change(input, { target: { value: 'second prompt' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(input.value).toBe('')

    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input.value).toBe('second prompt')
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input.value).toBe('first prompt')
    // Oldest entry — ArrowUp is a no-op for the composer.
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input.value).toBe('first prompt')

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input.value).toBe('second prompt')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input.value).toBe('')

    expect(props.onSubmit).toHaveBeenCalledTimes(2)
  })

  it('opens the slash menu on a leading /, navigates with arrows, and confirms with Tab', () => {
    renderComposer()
    const input = composerInput()

    fireEvent.change(input, { target: { value: '/st' } })
    const listbox = screen.getByRole('listbox', { name: '命令' })
    expect(listbox).toBeVisible()
    const options = screen.getAllByRole('option')
    expect(options.map(option => option.textContent)).toEqual(['/status查看状态'])
    expect(input).toHaveAttribute('aria-expanded', 'true')
    expect(input).toHaveAttribute('aria-activedescendant', options[0]!.id)

    fireEvent.keyDown(input, { key: 'Tab' })
    expect(input.value).toBe('/status ')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('confirms the highlighted slash command with Enter after ArrowDown', () => {
    renderComposer()
    const input = composerInput()

    fireEvent.change(input, { target: { value: '/' } })
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(4)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input).toHaveAttribute('aria-activedescendant', options[1]!.id)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(input.value).toBe('/compact ')
  })

  it('closes the slash menu on Escape without destroying the draft', () => {
    renderComposer()
    const input = composerInput()

    fireEvent.change(input, { target: { value: '/doc' } })
    expect(screen.getByRole('listbox')).toBeVisible()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(input.value).toBe('/doc')
  })

  it('opens the mention menu only when resolveMentions returns candidates', async () => {
    const resolveMentions = vi.fn(async (query: string) =>
      query === 'sr' ? [{ id: 'm1', name: 'src/app.ts', description: 'file' }] : [],
    )
    renderComposer({ resolveMentions })
    const input = composerInput()

    fireEvent.change(input, { target: { value: '看看 @sr' } })
    const option = await screen.findByRole('option', { name: /@src\/app\.ts/ })
    expect(option).toBeVisible()

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(input.value).toBe('看看 @src/app.ts ')

    fireEvent.change(input, { target: { value: 'cc @zz' } })
    await waitFor(() => { expect(resolveMentions).toHaveBeenLastCalledWith('zz') })
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('shows the Stop button and routes Esc to onCancel while busy', () => {
    const props = renderComposer({ busy: true })
    const input = composerInput()

    fireEvent.click(screen.getByRole('button', { name: 'Stop run' }))
    expect(props.onCancel).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(props.onCancel).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('button', { name: 'Send message' })).toBeNull()
  })

  it('keeps draft and attachments with an inline alert when submitting attachments without upload support', () => {
    const props = renderComposer()
    const input = composerInput()
    const file = new File(['pixels'], 'shot.png', { type: 'image/png' })

    const paste = createEvent.paste(input)
    Object.defineProperty(paste, 'clipboardData', { value: { files: [file] } })
    fireEvent(input, paste)

    expect(screen.getByRole('button', { name: '预览 shot.png' })).toBeVisible()

    fireEvent.change(input, { target: { value: '看图说话' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(props.onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('附件上传需要服务端支持（规划中）')
    expect(input.value).toBe('看图说话')
    expect(screen.getByRole('button', { name: '预览 shot.png' })).toBeVisible()
  })

  it('routes the same attachment warning through the toast provider when mounted', () => {
    const props: ComposerPanelProps = {
      sessionId: 'session-01',
      busy: false,
      onSubmit: vi.fn(),
      onCancel: vi.fn(),
      slashCommands: COMMANDS,
      resolveMentions: noMentions,
    }
    render(<ToastProvider><ComposerPanel {...props} /></ToastProvider>)
    const input = composerInput()
    const file = new File(['pixels'], 'shot.png', { type: 'image/png' })

    const paste = createEvent.paste(input)
    Object.defineProperty(paste, 'clipboardData', { value: { files: [file] } })
    fireEvent(input, paste)
    fireEvent.change(input, { target: { value: 'x' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(props.onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('附件上传需要服务端支持（规划中）')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('adds dropped files to the rail and removes them via the delete button', () => {
    renderComposer()
    const file = new File(['content'], 'notes.txt', { type: 'text/plain' })

    fireEvent.dragEnter(document, { dataTransfer: { types: ['Files'] } })
    expect(screen.getByText('松开以添加附件')).toBeVisible()
    fireEvent.drop(document, { dataTransfer: { types: ['Files'], files: [file] } })

    expect(screen.getByText('notes.txt')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '移除附件 notes.txt' }))
    expect(screen.queryByText('notes.txt')).toBeNull()
  })

  it('restores a per-session draft persisted under hm.draft.<sessionId>', () => {
    const { unmount } = render(<ComposerPanel
      sessionId="session-01"
      busy={false}
      onSubmit={vi.fn()}
      onCancel={vi.fn()}
      slashCommands={COMMANDS}
      resolveMentions={noMentions}
    />)
    fireEvent.change(composerInput(), { target: { value: 'draft for s1' } })
    // unmount flushes the pending debounced draft write
    unmount()

    renderComposer()
    expect(composerInput().value).toBe('draft for s1')
  })

  it('loads an independent draft per session id', () => {
    const { rerender } = render(<ComposerPanel
      sessionId="s1"
      busy={false}
      onSubmit={vi.fn()}
      onCancel={vi.fn()}
      slashCommands={COMMANDS}
      resolveMentions={noMentions}
    />)
    fireEvent.change(composerInput(), { target: { value: 'one' } })

    const otherProps: ComposerPanelProps = {
      sessionId: 's2',
      busy: false,
      onSubmit: vi.fn(),
      onCancel: vi.fn(),
      slashCommands: COMMANDS,
      resolveMentions: noMentions,
    }
    rerender(<ComposerPanel {...otherProps} />)
    expect(composerInput().value).toBe('')
    fireEvent.change(composerInput(), { target: { value: 'two' } })

    rerender(<ComposerPanel {...otherProps} sessionId="s1" />)
    expect(composerInput().value).toBe('one')
  })
})

describe('toast helper used by the composer', () => {
  it('useToast throws outside the provider', () => {
    function Bare() {
      useToast()
      return null
    }
    expect(() => render(<Bare />)).toThrow(/ToastProvider/)
  })
})
