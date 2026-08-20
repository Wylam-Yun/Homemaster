/**
 * Adapted from DeepSeek Harness connection.ts and web-api-client.ts.
 * MIT License, Copyright (c) 2026 DeepSeek. Reduced to one HomeMaster WebSocket.
 */

import { isWebEvent, type WebEvent } from '../protocol/events'

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'offline'

export type SocketLike = Pick<
  WebSocket,
  'OPEN' | 'CONNECTING' | 'readyState' | 'close' | 'addEventListener' | 'removeEventListener'
>

export type ConnectionSinks = {
  onEvent?: (event: WebEvent) => void
  onStateChange?: (state: ConnectionState) => void
}

export type ConnectionConfig = {
  backoffBaseMs?: number
  backoffFactor?: number
  backoffMaxMs?: number
  jitter?: () => number
}

type ResolvedConfig = Required<ConnectionConfig>

export class EventConnection {
  private generation = 0
  private attempt = 0
  private running = false
  private socket: SocketLike | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private lastState: ConnectionState | null = null
  private readonly config: ResolvedConfig

  constructor(
    private readonly sessionId: string,
    private readonly openSocket: (sessionId: string) => SocketLike = createBrowserSocket,
    private readonly sinks: ConnectionSinks = {},
    config: ConnectionConfig = {},
  ) {
    this.config = {
      backoffBaseMs: config.backoffBaseMs ?? 500,
      backoffFactor: config.backoffFactor ?? 2,
      backoffMaxMs: config.backoffMaxMs ?? 10_000,
      jitter: config.jitter ?? Math.random,
    }
  }

  start(): void {
    if (this.running) return
    this.running = true
    this.emitState('connecting')
    this.connect()
  }

  stop(): void {
    if (!this.running) return
    this.running = false
    this.generation += 1
    if (this.retryTimer !== null) clearTimeout(this.retryTimer)
    this.retryTimer = null
    const socket = this.socket
    this.socket = null
    if (socket !== null && (socket.readyState === socket.CONNECTING || socket.readyState === socket.OPEN)) {
      socket.close()
    }
    this.emitState('offline')
  }

  private connect(): void {
    if (!this.running) return
    const generation = ++this.generation
    const socket = this.openSocket(this.sessionId)
    this.socket = socket

    const active = (): boolean => this.running && generation === this.generation
    const onOpen = (): void => {
      if (!active()) return
      this.attempt = 0
      this.emitState('connected')
    }
    const onMessage = (raw: Event): void => {
      if (!active()) return
      try {
        const message = raw as MessageEvent<unknown>
        if (typeof message.data !== 'string') throw new Error('binary WebSocket frame')
        const parsed: unknown = JSON.parse(message.data)
        if (!isWebEvent(parsed)) throw new Error('invalid Web event envelope')
        this.callSink(() => { this.sinks.onEvent?.(parsed) })
      } catch (error) {
        console.error('[homemaster-web] dropping malformed event frame:', error)
      }
    }
    const onClose = (): void => {
      if (!active()) return
      this.emitState('reconnecting')
      this.attempt += 1
      const cap = Math.min(
        this.config.backoffMaxMs,
        this.config.backoffBaseMs * this.config.backoffFactor ** Math.max(0, this.attempt - 1),
      )
      const delay = cap / 2 + this.config.jitter() * cap / 2
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null
        this.connect()
      }, delay)
    }
    socket.addEventListener('open', onOpen)
    socket.addEventListener('message', onMessage)
    socket.addEventListener('close', onClose, { once: true })
  }

  private emitState(state: ConnectionState): void {
    if (state === this.lastState) return
    this.lastState = state
    this.callSink(() => { this.sinks.onStateChange?.(state) })
  }

  private callSink(fn: () => void): void {
    try {
      fn()
    } catch (error) {
      console.error('[homemaster-web] connection sink threw:', error)
    }
  }
}

export function createBrowserSocket(sessionId: string): WebSocket {
  const url = new URL('/api/events', window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('session_id', sessionId)
  return new WebSocket(url)
}
