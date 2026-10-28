import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent, type ReactNode } from 'react'

/**
 * Minimal hash router — the console is a single-page shell, so routes live in
 * the URL fragment (`#/chat/{sessionId}`, `#/sessions`, `#/memory`,
 * `#/permissions`, `#/skills`, `#/settings`, `#/onboarding`) instead of a
 * routing dependency. `useRoute` re-renders on hashchange; `Link` is a plain
 * anchor that routes through `navigate` for deterministic hashchange delivery.
 */
export type Route =
  | { kind: 'chat'; sessionId: string | null }
  | { kind: 'sessions' }
  | { kind: 'memory' }
  | { kind: 'feedback' }
  | { kind: 'permissions' }
  | { kind: 'skills' }
  | { kind: 'settings' }
  | { kind: 'onboarding' }

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value)
  } catch {
    // Malformed escapes in a hand-typed hash must not crash the router.
    return value
  }
}

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, '').replace(/^\/+|\/+$/g, '')
  const [head, rest] = path.split('/', 2)
  switch (head) {
    case 'chat':
      return { kind: 'chat', sessionId: rest === undefined || rest === '' ? null : safeDecode(rest) }
    case 'sessions':
      return { kind: 'sessions' }
    case 'memory':
      return { kind: 'memory' }
    case 'feedback':
      return { kind: 'feedback' }
    case 'permissions':
      return { kind: 'permissions' }
    case 'skills':
      return { kind: 'skills' }
    case 'settings':
      return { kind: 'settings' }
    case 'onboarding':
      return { kind: 'onboarding' }
    default:
      return { kind: 'chat', sessionId: null }
  }
}

export function routeHash(route: Route): string {
  switch (route.kind) {
    case 'chat':
      return route.sessionId === null ? '#/chat' : `#/chat/${encodeURIComponent(route.sessionId)}`
    default:
      return `#/${route.kind}`
  }
}

export function chatHash(sessionId: string): string {
  return `#/chat/${encodeURIComponent(sessionId)}`
}

function routesEqual(a: Route, b: Route): boolean {
  return a.kind === b.kind && (a.kind !== 'chat' || (b.kind === 'chat' && a.sessionId === b.sessionId))
}

/**
 * Write a `#/…` fragment and synchronously notify subscribers. Browsers also
 * fire a native hashchange afterwards — `useRoute` dedupes identical routes,
 * and jsdom (which delivers hashchange asynchronously, if at all) stays
 * deterministic under Testing Library.
 */
export function navigate(hash: string): void {
  if (window.location.hash !== hash) window.location.hash = hash
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
  useEffect(() => {
    const onChange = (): void => {
      setRoute(current => {
        const next = parseHash(window.location.hash)
        return routesEqual(current, next) ? current : next
      })
    }
    window.addEventListener('hashchange', onChange)
    return () => { window.removeEventListener('hashchange', onChange) }
  }, [])
  return route
}

type LinkProps = {
  to: string
  children: ReactNode
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href' | 'onClick'>

export function Link({ to, children, ...rest }: LinkProps) {
  const onClick = (event: MouseEvent<HTMLAnchorElement>): void => {
    // Modified clicks keep native anchor semantics (new tab, copy link…).
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return
    event.preventDefault()
    navigate(to)
  }
  return <a href={to} onClick={onClick} {...rest}>{children}</a>
}
