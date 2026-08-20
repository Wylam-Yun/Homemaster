import { afterEach, describe, expect, it, vi } from 'vitest'

import { EventConnection, type SocketLike } from './connection'
import type { WebEvent } from '../protocol/events'

class FakeSocket extends EventTarget implements SocketLike {
  readonly OPEN = 1
  readonly CONNECTING = 0
  readyState: 0 | 1 | 2 | 3 = this.CONNECTING
  close = vi.fn(() => { this.readyState = 3 })

  open(): void {
    this.readyState = this.OPEN
    this.dispatchEvent(new Event('open'))
  }

  message(event: WebEvent): void {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(event) }))
  }

  fail(): void {
    this.readyState = 3
    this.dispatchEvent(new Event('close'))
  }
}

const frame = (text: string): WebEvent => ({
  type: 'answer.delta',
  session_id: 'session-01',
  run_id: 'run-01',
  request_id: 'request-01',
  payload: { text },
})

describe('EventConnection', () => {
  afterEach(() => { vi.useRealTimers() })

  it('reconnects and ignores late frames from an older generation', async () => {
    vi.useFakeTimers()
    const sockets: FakeSocket[] = []
    const received: string[] = []
    const states: string[] = []
    const connection = new EventConnection(
      'session-01',
      () => {
        const socket = new FakeSocket()
        sockets.push(socket)
        return socket
      },
      {
        onEvent: event => { if (event.type === 'answer.delta') received.push(event.payload.text) },
        onStateChange: state => { states.push(state) },
      },
      { backoffBaseMs: 10, backoffMaxMs: 10, jitter: () => 1 },
    )

    connection.start()
    sockets[0].open()
    sockets[0].message(frame('first'))
    sockets[0].fail()
    await vi.advanceTimersByTimeAsync(10)
    sockets[1].open()
    sockets[0].message(frame('stale'))
    sockets[1].message(frame('second'))

    expect(received).toEqual(['first', 'second'])
    expect(states).toEqual(['connecting', 'connected', 'reconnecting', 'connected'])
    connection.stop()
    expect(states.at(-1)).toBe('offline')
  })

  it('drops malformed frames and isolates an event sink exception', () => {
    const socket = new FakeSocket()
    let calls = 0
    const connection = new EventConnection(
      'session-01',
      () => socket,
      { onEvent: () => { calls += 1; throw new Error('render failed') } },
    )
    connection.start()
    socket.open()
    socket.dispatchEvent(new MessageEvent('message', { data: '{broken' }))
    socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({
      ...frame('unknown'), type: 'private.provider.metadata',
    }) }))
    socket.message(frame('still delivered'))

    expect(calls).toBe(1)
    expect(socket.close).not.toHaveBeenCalled()
    connection.stop()
  })
})
