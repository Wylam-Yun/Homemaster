import { useCallback, useEffect, useRef, useState, type RefCallback } from 'react'

const BOTTOM_THRESHOLD_PX = 40

export function isNearBottom(el: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD_PX
}

function scrollElementTo(el: HTMLElement, top: number, behavior: ScrollBehavior): void {
  if (typeof el.scrollTo === 'function') {
    el.scrollTo({ top, behavior })
    return
  }
  el.scrollTop = top
}

export type ScrollFollow = {
  containerRef: RefCallback<HTMLElement>
  contentRef: RefCallback<HTMLDivElement>
  following: boolean
  unseenCount: number
  scrollToBottom: (behavior?: ScrollBehavior) => void
  releaseFollow: () => void
}

/**
 * Scroll contract for the transcript: follow the tail while streaming, release when the
 * user scrolls up, re-engage when they return to the bottom, count unseen items while
 * released, and force-pin on a new turn.
 *
 * Adapted from cline chat-view useScrollBehavior.ts (wheel-up releases, bottom re-engages),
 * minus the virtuoso bits — this transcript is a plain scroll container.
 */
export function useScrollFollow({ itemCount, turnCount }: { itemCount: number; turnCount: number }): ScrollFollow {
  const [containerEl, setContainerEl] = useState<HTMLElement | null>(null)
  const [contentEl, setContentEl] = useState<HTMLDivElement | null>(null)
  const containerElRef = useRef<HTMLElement | null>(null)
  const followRef = useRef(true)
  const countsRef = useRef({ items: itemCount, turns: turnCount })
  const [following, setFollowing] = useState(true)
  const [unseenCount, setUnseenCount] = useState(0)

  // Callback refs instead of plain objects: the conversation section unmounts with the
  // view switch, so observers and listeners must rebind when the elements return.
  const containerRef = useCallback((el: HTMLElement | null) => {
    containerElRef.current = el
    setContainerEl(el)
  }, [])
  const contentRef = useCallback((el: HTMLDivElement | null) => {
    setContentEl(el)
  }, [])

  const setFollow = useCallback((value: boolean) => {
    followRef.current = value
    setFollowing(value)
    if (value) setUnseenCount(0)
  }, [])

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    const el = containerElRef.current
    setFollow(true)
    if (el === null) return
    scrollElementTo(el, el.scrollHeight, behavior)
  }, [setFollow])

  const releaseFollow = useCallback(() => { setFollow(false) }, [setFollow])

  // Wheel-up releases the follow pin; landing back at the bottom re-engages it.
  useEffect(() => {
    if (containerEl === null) return
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY < 0) setFollow(false)
    }
    const onScroll = () => {
      if (isNearBottom(containerEl)) setFollow(true)
    }
    containerEl.addEventListener('wheel', onWheel, { passive: true })
    containerEl.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      containerEl.removeEventListener('wheel', onWheel)
      containerEl.removeEventListener('scroll', onScroll)
    }
  }, [containerEl, setFollow])

  // Item delta accounting: a new turn force-pins to the bottom; new items while released
  // accumulate into the back-to-bottom badge. A *decreasing* turn count means the user
  // switched to a shorter conversation — pin to the bottom there too.
  useEffect(() => {
    const previous = countsRef.current
    countsRef.current = { items: itemCount, turns: turnCount }
    if (turnCount !== previous.turns) {
      scrollToBottom('auto')
      return
    }
    if (itemCount > previous.items && !followRef.current) {
      setUnseenCount(count => count + (itemCount - previous.items))
    }
  }, [itemCount, turnCount, scrollToBottom])

  // Keep the tail pinned across content growth and expand/collapse while following;
  // ResizeObserver covers cases item counts miss (streaming deltas, image load, folds).
  useEffect(() => {
    if (contentEl === null || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      const el = containerElRef.current
      if (el !== null && followRef.current) scrollElementTo(el, el.scrollHeight, 'auto')
    })
    observer.observe(contentEl)
    return () => { observer.disconnect() }
  }, [contentEl])

  return { containerRef, contentRef, following, unseenCount, scrollToBottom, releaseFollow }
}
