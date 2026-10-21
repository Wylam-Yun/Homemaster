import { afterEach, describe, expect, it, vi } from 'vitest'

import { EventConnection, type EventSourceLike } from './connection'
import type { WebEvent } from '../protocol/events'

class FakeEventSource extends EventTarget implements EventSourceLike {
  readonly OPEN = 1
  readonly CONNECTING = 0
  readyState: 0 | 1 | 2 = this.CONNECTING
  close = vi.fn(() => { this.readyState = 2 })

  open(): void {
    this.readyState = this.OPEN
    this.dispatchEvent(new Event('open'))
  }

  message(event: WebEvent, lastEventId = ''): void {
    this.dispatchEvent(new MessageEvent('message', {
      data: JSON.stringify(event),
      lastEventId,
    }))
  }

  fail(): void {
    this.readyState = 0 // EventSource re-enters CONNECTING before retrying
    this.dispatchEvent(new Event('error'))
  }
}

const frame = (text: string): WebEvent => ({
  type: 'answer.delta',
  session_id: 'session-01',
  run_id: 'run-01',
  request_id: 'request-01',
  seq: 1,
  ts: 1_700_000_000,
  payload: { text },
})

const okProbe = () => Promise.resolve(200)

describe('EventConnection', () => {
  afterEach(() => { vi.useRealTimers() })

  it('reconnects and ignores late frames from an older generation', async () => {
    vi.useFakeTimers()
    const sockets: FakeEventSource[] = []
    const received: string[] = []
    const states: string[] = []
    const connection = new EventConnection(
      'session-01',
      () => {
        const socket = new FakeEventSource()
        sockets.push(socket)
        return socket
      },
      {
        onEvent: event => { if (event.type === 'answer.delta') received.push(event.payload.text) },
        onStateChange: state => { states.push(state) },
      },
      { backoffBaseMs: 10, backoffMaxMs: 10, jitter: () => 1, probe: okProbe },
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
    const socket = new FakeEventSource()
    let calls = 0
    const connection = new EventConnection(
      'session-01',
      () => socket,
      { onEvent: () => { calls += 1; throw new Error('render failed') } },
      { probe: okProbe },
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

  it('fails fast without retrying when the stream is rejected with 404', async () => {
    vi.useFakeTimers()
    const sockets: FakeEventSource[] = []
    let rejected: number | null = null
    const states: string[] = []
    const connection = new EventConnection(
      'session-01',
      () => {
        const socket = new FakeEventSource()
        sockets.push(socket)
        return socket
      },
      {
        onReject: code => { rejected = code },
        onStateChange: state => { states.push(state) },
      },
      { backoffBaseMs: 10, backoffMaxMs: 10, jitter: () => 1, probe: () => Promise.resolve(404) },
    )

    connection.start()
    sockets[0].fail() // EventSource fires 'error' (no status) — probe finds 404
    await vi.advanceTimersByTimeAsync(1000)

    expect(rejected).toBe(404)
    expect(sockets).toHaveLength(1)
    expect(states).toEqual(['connecting', 'offline'])
    connection.stop()
  })

  it('records the SSE id and carries it into the reconnect URL', async () => {
    vi.useFakeTimers()
    const sockets: FakeEventSource[] = []
    const seenLastSeq: (string | null)[] = []
    const connection = new EventConnection(
      'session-01',
      (_sessionId, lastSeq) => {
        seenLastSeq.push(lastSeq)
        const socket = new FakeEventSource()
        sockets.push(socket)
        return socket
      },
      {},
      { backoffBaseMs: 10, backoffMaxMs: 10, jitter: () => 1, probe: okProbe },
    )

    connection.start()
    sockets[0].open()
    sockets[0].message(frame('first'), '41')
    sockets[0].fail()
    await vi.advanceTimersByTimeAsync(10)
    sockets[1].open()

    expect(seenLastSeq).toEqual([null, '41'])
    connection.stop()
  })

  it('keeps retrying (not rejecting) when the probe cannot reach the server', async () => {
    vi.useFakeTimers()
    const sockets: FakeEventSource[] = []
    const states: string[] = []
    let rejected = false
    const connection = new EventConnection(
      'session-01',
      () => {
        const socket = new FakeEventSource()
        sockets.push(socket)
        return socket
      },
      {
        onReject: () => { rejected = true },
        onStateChange: state => { states.push(state) },
      },
      { backoffBaseMs: 10, backoffMaxMs: 10, jitter: () => 1, probe: () => Promise.resolve(null) },
    )

    connection.start()
    sockets[0].fail()
    await vi.advanceTimersByTimeAsync(20)
    sockets[1].open()

    expect(rejected).toBe(false)
    expect(sockets).toHaveLength(2)
    expect(states).toEqual(['connecting', 'reconnecting', 'connected'])
    connection.stop()
  })
})
