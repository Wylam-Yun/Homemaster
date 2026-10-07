import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { isNearBottom, useScrollFollow } from './useScrollFollow'

function Harness({ items, turns }: { items: number; turns: number }) {
  const follow = useScrollFollow({ itemCount: items, turnCount: turns })
  return (
    <div>
      <output data-testid="follow-state">{`${follow.following ? 'following' : 'released'}:${follow.unseenCount}`}</output>
      <section ref={follow.containerRef} data-testid="scrollbox">
        <div ref={follow.contentRef}>content</div>
      </section>
      <button type="button" onClick={() => { follow.scrollToBottom() }}>jump</button>
    </div>
  )
}

function setMetrics(el: HTMLElement, { height, top, client }: { height: number; top: number; client: number }) {
  Object.defineProperty(el, 'scrollHeight', { value: height, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: client, configurable: true })
  el.scrollTop = top
}

function stubScrollTo(el: HTMLElement) {
  const stub = vi.fn((options?: ScrollToOptions | number) => {
    if (typeof options === 'object' && options !== null && typeof options.top === 'number') {
      el.scrollTop = options.top
    }
  })
  Object.defineProperty(el, 'scrollTo', { value: stub, configurable: true })
  return stub
}

const readState = () => screen.getByTestId('follow-state').textContent

describe('isNearBottom', () => {
  it('accepts positions inside the 40px threshold and rejects positions above it', () => {
    const el = { scrollHeight: 1000, clientHeight: 200, scrollTop: 0 }
    expect(isNearBottom({ ...el, scrollTop: 800 })).toBe(true)
    expect(isNearBottom({ ...el, scrollTop: 761 })).toBe(true)
    expect(isNearBottom({ ...el, scrollTop: 759 })).toBe(false)
    expect(isNearBottom({ ...el, scrollTop: 0 })).toBe(false)
  })
})

describe('useScrollFollow', () => {
  it('releases follow on wheel-up and counts unseen items while released', () => {
    const { rerender } = render(<Harness items={3} turns={1} />)
    const box = screen.getByTestId('scrollbox')
    setMetrics(box, { height: 2000, top: 1700, client: 200 })
    const scrollTo = stubScrollTo(box)

    expect(readState()).toBe('following:0')

    fireEvent.wheel(box, { deltaY: -120 })
    expect(readState()).toBe('released:0')

    rerender(<Harness items={5} turns={1} />)
    expect(readState()).toBe('released:2')
    rerender(<Harness items={6} turns={1} />)
    expect(readState()).toBe('released:3')
  })

  it('re-engages follow when the user scrolls back to the bottom', () => {
    const { rerender } = render(<Harness items={3} turns={1} />)
    const box = screen.getByTestId('scrollbox')
    setMetrics(box, { height: 2000, top: 100, client: 200 })
    const scrollTo = stubScrollTo(box)

    fireEvent.wheel(box, { deltaY: -80 })
    rerender(<Harness items={4} turns={1} />)
    expect(readState()).toBe('released:1')

    setMetrics(box, { height: 2000, top: 1800, client: 200 })
    fireEvent.scroll(box)
    expect(readState()).toBe('following:0')
  })

  it('force-pins to the bottom when a new turn starts', () => {
    const { rerender } = render(<Harness items={3} turns={1} />)
    const box = screen.getByTestId('scrollbox')
    setMetrics(box, { height: 2000, top: 50, client: 200 })
    const scrollTo = stubScrollTo(box)

    fireEvent.wheel(box, { deltaY: -50 })
    rerender(<Harness items={4} turns={1} />)
    expect(readState()).toBe('released:1')

    rerender(<Harness items={6} turns={2} />)
    expect(readState()).toBe('following:0')
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 2000 }))
    expect(box.scrollTop).toBe(2000)
  })

  it('pins back to the bottom when the turn count shrinks (session switch)', () => {
    const { rerender } = render(<Harness items={8} turns={3} />)
    const box = screen.getByTestId('scrollbox')
    setMetrics(box, { height: 2000, top: 300, client: 200 })
    const scrollTo = stubScrollTo(box)

    fireEvent.wheel(box, { deltaY: -40 })
    expect(readState()).toBe('released:0')

    // Switching to a conversation with fewer turns must re-pin to the bottom.
    rerender(<Harness items={2} turns={1} />)
    expect(readState()).toBe('following:0')
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 2000 }))
  })

  it('scrollToBottom re-engages follow and clears the unseen counter', () => {
    const { rerender } = render(<Harness items={3} turns={1} />)
    const box = screen.getByTestId('scrollbox')
    setMetrics(box, { height: 1500, top: 0, client: 200 })
    const scrollTo = stubScrollTo(box)

    fireEvent.wheel(box, { deltaY: -10 })
    rerender(<Harness items={7} turns={1} />)
    expect(readState()).toBe('released:4')

    fireEvent.click(screen.getByRole('button', { name: 'jump' }))
    expect(readState()).toBe('following:0')
    expect(box.scrollTop).toBe(1500)
  })
})
