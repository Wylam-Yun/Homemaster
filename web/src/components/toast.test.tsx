import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ToastProvider, useFaviconStatus, useToast, type ToastOptions } from './toast'

function ShowButton({ options }: { options: ToastOptions }) {
  const toast = useToast()
  return <button type="button" onClick={() => { toast.show(options) }}>show</button>
}

describe('ToastProvider', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('stacks toasts and dismisses through the close button', async () => {
    render(
      <ToastProvider>
        <ShowButton options={{ title: '会话已删除', description: '可以撤销' }} />
      </ToastProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'show' }))
    fireEvent.click(screen.getByRole('button', { name: 'show' }))
    expect(screen.getAllByRole('status')).toHaveLength(2)

    fireEvent.click(screen.getAllByRole('button', { name: '关闭通知' })[0]!)
    await vi.waitFor(() => { expect(screen.getAllByRole('status')).toHaveLength(1) })
  })

  it('runs the action callback (undo) and dismisses the toast', async () => {
    const undo = vi.fn()
    render(
      <ToastProvider>
        <ShowButton options={{ title: '已删除', action: { label: '撤销', onClick: undo } }} />
      </ToastProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'show' }))
    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(undo).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => { expect(screen.queryByRole('status')).toBeNull() })
  })

  it('auto-dismisses after the default 5s duration', () => {
    vi.useFakeTimers()
    render(
      <ToastProvider>
        <ShowButton options={{ title: '提示' }} />
      </ToastProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'show' }))
    expect(screen.getByRole('status')).toBeVisible()

    act(() => { vi.advanceTimersByTime(5000 + 250) })
    expect(screen.queryByRole('status')).toBeNull()
  })
})

function TitleProbe({ running }: { running: boolean }) {
  useFaviconStatus(running)
  return null
}

describe('useFaviconStatus', () => {
  it('prefixes document.title with ● while running and restores it after', () => {
    const original = document.title
    document.title = 'HomeMaster Console'
    const { rerender, unmount } = render(<TitleProbe running={false} />)
    expect(document.title).toBe('HomeMaster Console')

    rerender(<TitleProbe running />)
    expect(document.title).toBe('● HomeMaster Console')

    rerender(<TitleProbe running={false} />)
    expect(document.title).toBe('HomeMaster Console')

    rerender(<TitleProbe running />)
    unmount()
    expect(document.title).toBe('HomeMaster Console')
    document.title = original
  })
})
