/**
 * Session event stream connection — Server-Sent Events (EventSource).
 *
 * Adapted from DeepSeek Harness connection.ts (MIT License, (c) 2026 DeepSeek),
 * reduced to one HomeMaster stream. The transport moved from WebSocket
 * ``/api/events`` to SSE ``GET /api/sessions/{id}/stream``; the consumer-facing
 * surface (onEvent/onStateChange/onReject + connection states) is unchanged.
 *
 * Notes:
 * - EventSource has no close codes, so the old 4404 "session is gone" signal
 *   becomes an HTTP 404 on the stream — and the ``error`` event carries no
 *   status, so a rejected stream is distinguished from a network drop by
 *   probing ``GET /api/sessions/{id}/status`` before retrying.
 * - EventSource's built-in auto-retry is deliberately bypassed: on ``error``
 *   the source is closed and this class drives its own backoff loop, keeping
 *   the existing reconnecting-state/hydrate semantics and jittered backoff.
 * - Browsers cap ~6 concurrent EventSource connections per origin; we hold
 *   exactly one per session selection — fine today, revisit if tabs multiply.
 * - The SSE ``id:`` field carries the envelope ``seq``; on reconnect it is
 *   echoed as ``?last_seq=`` (the server accepts and logs it; replay lands in
 *   P1 — recovery today is hydrate, same as the WS era).
 */

import { isWebEvent, type WebEvent } from '../protocol/events'

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'offline'

// Minimal structural seam: ``Pick<EventSource, 'addEventListener'>`` would
// drag in EventSource's typed overloads (``MessageEvent`` listeners), which a
// plain ``EventTarget`` test fake cannot satisfy — the connection only ever
// subscribes generic ``open``/``message``/``error`` listeners.
export type EventSourceLike = {
  readonly CONNECTING: number
  readonly OPEN: number
  readonly readyState: number
  close(): void
  addEventListener(
    type: string,
    listener: (event: Event) => void,
    options?: boolean | AddEventListenerOptions,
  ): void
  removeEventListener(type: string, listener: (event: Event) => void): void
}

export type ConnectionSinks = {
  onEvent?: (event: WebEvent) => void
  onStateChange?: (state: ConnectionState) => void
  onReject?: (code: number) => void
}

export type ConnectionConfig = {
  backoffBaseMs?: number
  backoffFactor?: number
  backoffMaxMs?: number
  jitter?: () => number
  /** HTTP status of the session (null = unreachable). Used to tell 404 apart
   *  from transient failures — EventSource's ``error`` event has no status. */
  probe?: (sessionId: string) => Promise<number | null>
}

type ResolvedConfig = Required<ConnectionConfig>

export class EventConnection {
  private generation = 0
  private attempt = 0
  private running = false
  private socket: EventSourceLike | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private lastState: ConnectionState | null = null
  private lastSeq: string | null = null
  private readonly config: ResolvedConfig

  constructor(
    private readonly sessionId: string,
    private readonly openStream: (sessionId: string, lastSeq: string | null) => EventSourceLike = createBrowserEventSource,
    private readonly sinks: ConnectionSinks = {},
    config: ConnectionConfig = {},
  ) {
    this.config = {
      backoffBaseMs: config.backoffBaseMs ?? 500,
      backoffFactor: config.backoffFactor ?? 2,
      backoffMaxMs: config.backoffMaxMs ?? 10_000,
      jitter: config.jitter ?? Math.random,
      probe: config.probe ?? probeSessionStatus,
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
    const socket = this.openStream(this.sessionId, this.lastSeq)
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
        if (typeof message.data !== 'string') throw new Error('non-text SSE data')
        const parsed: unknown = JSON.parse(message.data)
        if (!isWebEvent(parsed)) throw new Error('invalid Web event envelope')
        // ``id:`` mirrors the envelope seq — remember it for the reconnect URL.
        if (typeof message.lastEventId === 'string' && message.lastEventId !== '') {
          this.lastSeq = message.lastEventId
        }
        this.callSink(() => { this.sinks.onEvent?.(parsed) })
      } catch (error) {
        console.error('[homemaster-web] dropping malformed event frame:', error)
      }
    }
    const onError = (): void => {
      if (!active()) return
      // Take over from EventSource's internal auto-retry so 404 detection and
      // the jittered backoff stay under our control.
      socket.close()
      void this.afterDrop(generation)
    }
    socket.addEventListener('open', onOpen)
    socket.addEventListener('message', onMessage)
    socket.addEventListener('error', onError, { once: true })
  }

  private async afterDrop(generation: number): Promise<void> {
    const status = await this.probeSafely()
    if (!this.running || generation !== this.generation) return
    if (status === 404) {
      // Unknown session: retrying is pointless, fail fast so the UI recovers —
      // the SSE-era equivalent of the WS close code 4404 branch.
      this.stop()
      this.callSink(() => { this.sinks.onReject?.(404) })
      return
    }
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

  private async probeSafely(): Promise<number | null> {
    try {
      return await this.config.probe(this.sessionId)
    } catch (error) {
      console.error('[homemaster-web] session probe failed:', error)
      return null
    }
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

export function createBrowserEventSource(
  sessionId: string,
  lastSeq: string | null = null,
): EventSource {
  const url = new URL(`/api/sessions/${encodeURIComponent(sessionId)}/stream`, window.location.href)
  if (lastSeq !== null) url.searchParams.set('last_seq', lastSeq)
  return new EventSource(url)
}

/** Probe the session resource: 404 means the session is gone for good. */
export async function probeSessionStatus(sessionId: string): Promise<number | null> {
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/status`)
    return response.status
  } catch {
    return null
  }
}
