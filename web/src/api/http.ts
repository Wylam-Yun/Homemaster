export type SessionSummary = {
  session_id: string
  title: string
  message_count: number
  updated_at: string | null
}
export type HistoryMessage = {
  role: string
  text: string
  thinking?: string
  tool_call_id?: string
  name?: string
}

export type ItemChoice = 'allow_once' | 'allow_always' | 'reject'

export type ApprovalDecision = {
  item_id: string
  choice: ItemChoice
}

export type ApprovalSubmission = {
  protocol_version: 2
  submission_id: string
  request_revision: number
  decisions: ApprovalDecision[]
}

export type ApprovalResolutionItem = {
  item_id: string
  choice: string
}

export type ApprovalResolution = {
  approval_id: string
  request_id: string
  request_status: string
  execution_started: boolean
  persisted_grant_ids: string[]
  items: ApprovalResolutionItem[]
}

export type ApprovalCancel = {
  submission_id: string
  request_revision: number
}

export type StoredApprovalItem = {
  item_id: string
  display_name: string
  location: string
  action_label: string
  resource_kind: string
  resource_id: string
  action: string
  decision: string | null
  matched_grant_id: string | null
  step_ids: string[]
}

export type StoredApproval = {
  approval_id: string
  request_id: string
  environment_id: string
  revision: number
  intent_summary: string
  request_status: string
  created_at: string
  deadline_at: string
  resolved_at: string | null
  items: StoredApprovalItem[]
}

export type Grant = {
  grant_id: string
  environment_id: string
  resource_kind: string
  resource_id: string
  action: string
  created_at: string
  created_by: string
  source_request_id: string
  source_item_id: string
  revoked_at: string | null
  revoked_by: string | null
  revision: number
  status: 'active' | 'revoked'
}

export type GrantRevocation = {
  submission_id: string
  expected_revision: number
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
  domain?: string | null
  outcome?: 'success' | 'failure' | 'unknown' | null
  classification?: string | null
  goal_type?: string | null
  episode_id?: string | null
  taskset_id?: string | null
  subtask_index?: number | null
  is_executable?: boolean | null
  source_trajectory_id?: string | null
  derived_memory_id?: string | null
  compile_status?: string | null
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
export type CompileJob = { job_id: string; status: string; memory_id?: string; derived_memory_id?: string; error?: string }

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

  compileMemory(memoryId: string): Promise<CompileJob> {
    return this.request(`/api/memories/${encodeURIComponent(memoryId)}/compile`, { method: 'POST' })
  }

  compileStatus(jobId: string): Promise<CompileJob> {
    return this.request(`/api/memory-compilations/${encodeURIComponent(jobId)}`)
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

  submitApproval(approvalId: string, submission: ApprovalSubmission): Promise<ApprovalResolution> {
    return this.request(`/api/approvals/${encodeURIComponent(approvalId)}`, {
      method: 'POST',
      body: JSON.stringify(submission),
    })
  }

  readApproval(approvalId: string): Promise<StoredApproval> {
    return this.request(`/api/approvals/${encodeURIComponent(approvalId)}`)
  }

  cancelApproval(approvalId: string, cancel: ApprovalCancel): Promise<{ approval_id: string; request_status: string }> {
    return this.request(`/api/approvals/${encodeURIComponent(approvalId)}/cancel`, {
      method: 'POST',
      body: JSON.stringify(cancel),
    })
  }

  listGrants(params: {
    resource_kind?: 'object' | 'area'
    status?: string
    environment_id?: string
    cursor?: string | null
    limit?: number
  } = {}): Promise<{ grants: Grant[]; next_cursor: string | null }> {
    const query = new URLSearchParams()
    if (params.resource_kind !== undefined) query.set('resource_kind', params.resource_kind)
    if (params.status !== undefined) query.set('status', params.status)
    if (params.environment_id !== undefined) query.set('environment_id', params.environment_id)
    if (params.cursor !== undefined && params.cursor !== null) query.set('cursor', params.cursor)
    if (params.limit !== undefined) query.set('limit', String(params.limit))
    const suffix = query.size > 0 ? `?${query.toString()}` : ''
    return this.request(`/api/permissions/grants${suffix}`)
  }

  revokeGrant(grantId: string, revocation: GrantRevocation): Promise<Grant> {
    return this.request(`/api/permissions/grants/${encodeURIComponent(grantId)}/revoke`, {
      method: 'POST',
      body: JSON.stringify(revocation),
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
