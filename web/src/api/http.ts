export type SessionSummary = { session_id: string }
export type HistoryMessage = {
  role: string
  text: string
  thinking?: string
  tool_call_id?: string
  name?: string
}

export type MemoryStats = {
  active_count: number
  archived_count: number
  total_count: number
  session_group_count: number
}

export type ManagedMemory = {
  memory_id: string
  content: string
  memory_type: string
  memory_type_label: string
  status: 'active' | 'archived'
  session_id: string | null
  created_at: string | null
  updated_at: string | null
  archived_at: string | null
  archive_reason: string | null
  record: Record<string, unknown> | null
  structure_status: 'plain' | 'valid' | 'invalid'
  has_history: boolean
}

export type MemoryGroup = {
  session_id: string | null
  title: string
  active_count: number
  archived_count: number
  memories: ManagedMemory[]
}

export type MemorySnapshot = { stats: MemoryStats; groups: MemoryGroup[] }
export type MemoryHistory = { memory_id: string; versions: ManagedMemory[] }

export class HttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable: boolean,
  ) {
    super(message)
    this.name = 'HttpError'
  }
}

export class HomeMasterApi {
  createSession(sessionId?: string): Promise<{ session_id: string }> {
    return this.request('/api/sessions', {
      method: 'POST',
      body: sessionId === undefined ? undefined : JSON.stringify({ session_id: sessionId }),
    })
  }

  listSessions(): Promise<{ sessions: SessionSummary[] }> {
    return this.request('/api/sessions')
  }

  history(sessionId: string): Promise<{ session_id: string; messages: HistoryMessage[] }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/history`)
  }

  memories(): Promise<MemorySnapshot> {
    return this.request('/api/memories')
  }

  memoryHistory(memoryId: string): Promise<MemoryHistory> {
    return this.request(`/api/memories/${encodeURIComponent(memoryId)}/history`)
  }

  sendMessage(sessionId: string, requestId: string, text: string): Promise<{ accepted: boolean }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId, text }),
    })
  }

  cancel(sessionId: string): Promise<{ cancelled: boolean }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/cancel`, { method: 'POST' })
  }

  resolveApproval(approvalId: string, outcome: 'approve' | 'reject'): Promise<{ approved: boolean }> {
    return this.request(`/api/approvals/${encodeURIComponent(approvalId)}`, {
      method: 'POST',
      body: JSON.stringify({ outcome }),
    })
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...init.headers },
    })
    const payload = await response.json() as Record<string, unknown>
    if (!response.ok) {
      throw new HttpError(
        response.status,
        typeof payload.code === 'string' ? payload.code : 'http_error',
        typeof payload.message === 'string' ? payload.message : 'Request failed.',
        payload.retryable === true,
      )
    }
    return payload as T
  }
}
