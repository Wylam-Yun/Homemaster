import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Link, navigate, parseHash, routeHash, useRoute } from './router'

function RouteProbe() {
  const route = useRoute()
  return <output data-testid="route">{route.kind}{route.kind === 'chat' ? `:${route.sessionId ?? ''}` : ''}</output>
}

describe('hash router', () => {
  afterEach(() => {
    window.history.replaceState({}, '', '/')
  })

  it('parses every supported route and falls back to chat', () => {
    expect(parseHash('')).toEqual({ kind: 'chat', sessionId: null })
    expect(parseHash('#/')).toEqual({ kind: 'chat', sessionId: null })
    expect(parseHash('#/chat')).toEqual({ kind: 'chat', sessionId: null })
    expect(parseHash('#/chat/session%2001')).toEqual({ kind: 'chat', sessionId: 'session 01' })
    expect(parseHash('#/sessions')).toEqual({ kind: 'sessions' })
    expect(parseHash('#/memory')).toEqual({ kind: 'memory' })
    expect(parseHash('#/permissions')).toEqual({ kind: 'permissions' })
    expect(parseHash('#/skills')).toEqual({ kind: 'skills' })
    expect(parseHash('#/settings')).toEqual({ kind: 'settings' })
    expect(parseHash('#/onboarding')).toEqual({ kind: 'onboarding' })
    expect(parseHash('#/nope')).toEqual({ kind: 'chat', sessionId: null })
    expect(routeHash({ kind: 'chat', sessionId: 'session 01' })).toBe('#/chat/session%2001')
    expect(routeHash({ kind: 'settings' })).toBe('#/settings')
  })

  it('re-renders on navigate() and on Link clicks', async () => {
    render(<div><RouteProbe /><Link to="#/settings">open settings</Link></div>)

    expect(screen.getByTestId('route')).toHaveTextContent('chat:')
    act(() => { navigate('#/skills') })
    await waitFor(() => { expect(screen.getByTestId('route')).toHaveTextContent('skills') })

    fireEvent.click(screen.getByRole('link', { name: 'open settings' }))
    await waitFor(() => { expect(screen.getByTestId('route')).toHaveTextContent('settings') })
    expect(window.location.hash).toBe('#/settings')
  })
})
