export type ArchiveState = 'none' | 'queued' | 'processing' | 'completed' | 'failed'

export type ArchiveStatus = {
  session_id?: string
  state: ArchiveState
  job_id?: string | null
  exit_reason?: string | null
  stages?: Record<string, string | null>
  failed_phase?: string | null
  error?: string | null
  collected_events?: number | null
  extracted_count?: number | null
}

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

export type ItemChoice = 'allow_once' | 'allow_session' | 'allow_always' | 'reject'

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
  display_name: string | null
  location: string | null
  action_label: string | null
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

/** POST /api/memories/implicit-feedback — admission receipt for one
 * user-triggered implicit-feedback outbox job. */
export type ImplicitFeedbackRun = {
  job_id: string
  status: string
  window?: { window_start: string; window_end: string } | null
}

/** GET /api/memories/implicit-feedback/{job_id} — live job status.
 * ``status`` is the outbox job state (accepted/processing/deferred/
 * outcome_unknown while running; completed/no_action/failed/stale terminal). */
export type ImplicitFeedbackStatus = {
  job_id: string
  status: string
  error?: string | null
  actions_count?: number | null
  raw_verified?: boolean | null
}

/** GET /api/memories/graph — nodes carry Qdrant payload fields, edges are the
 * Neo4j relationships actually stored between them. */
export type MemoryGraphNode = {
  memory_id: string
  preview: string
  memory_type: string
  status: string
  session_id: string | null
  updated_at: string | null
}

export type MemoryGraphEdge = {
  src: string
  rel: 'MENTIONS' | 'EXTRACTED_FROM' | 'RELATES_TO' | 'DERIVED_FROM' | string
  dst: string
  dst_label: string
  entity_name: string | null
}

export type MemoryGraph = {
  nodes: MemoryGraphNode[]
  entities: { entity_id: string; name: string }[]
  edges: MemoryGraphEdge[]
  sessions: { session_id: string; title: string }[]
  edges_truncated?: boolean
}

export type UiMode = 'plan' | 'act'

export type ProviderInfo = {
  name: string
  kind: string
  model: string | null
  api_key_configured: boolean
}

/**
 * Masked provider profile as returned by GET /api/providers (backend contract:
 * key material is never echoed — only counts/flags). Assignable to
 * ProviderInfo wherever the composer needs the picker view.
 */
export type ProviderProfile = {
  name: string
  kind: string
  model: string
  base_url: string
  api_format: string
  auth_type: string
  api_key_configured: boolean
  api_key_count: number
  keyless: boolean
  /** Only present on GET /api/providers list entries (write responses omit it). */
  is_default?: boolean
}

/** One selectable row from GET /api/provider-templates. */
export type ProviderTemplate = {
  key: string
  title: string
  api_format: string
  base_url: string
  model: string
  transport: string
  supports_auth_token: boolean
  keyless: boolean
}

/**
 * Write body for POST /api/providers (+ ?overwrite=) and PATCH
 * /api/providers/{name} — a subset of the backend's _PROVIDER_PAYLOAD_FIELDS;
 * `api_key` is write-only and `api_key: ""` keeps stored keys on PATCH.
 */
export type ProviderPayload = {
  name?: string
  template?: string
  api_format?: string
  transport?: string
  auth_type?: string
  base_url?: string
  model?: string
  api_key?: string
  api_keys?: string[]
  context_window_tokens?: number
  max_output_tokens?: number
  thinking_enable?: boolean
  thinking_budget?: number
  embedding_url?: string | null
  kind?: string
  set_default?: boolean
}

export type ProviderWriteResult = {
  provider: ProviderProfile
  status: string
  default?: string
}

export type ProviderDeleteResult = {
  deleted: boolean
  name: string
  default?: string
}

export type ProviderErrorKind = 'auth' | 'network' | 'model' | 'schema' | 'unknown'

/** POST /api/providers/test always answers 200 — probe outcomes ride the body. */
export type ProviderTestResult = {
  ok: boolean
  error_kind: ProviderErrorKind | null
  message: string
  latency_ms?: number
  name?: string | null
  model?: string | null
}

export type ProviderModels = {
  name: string
  models: string[]
  /** 'live' = enumerated from the provider; 'fallback' = offline static list. */
  source: string
}

/** GET /api/skills — read-only skill list (backend batch contract). */
export type SkillInfo = {
  name: string
  command_name: string | null
  description: string
  source: string
  user_invocable: boolean
  enabled: boolean
  /** Server-side delete eligibility (raw provenance === 'user'). */
  deletable?: boolean
}

/** One rejected skill source reported alongside the skill list. */
export type SkillIssueInfo = {
  source: string
  code: string
  detail: string
  path: string | null
}

export type MetaInfo = {
  version: string
  memory_mode: string
  environment: string | null
}

export type DoctorCheck = {
  name: string
  status: 'PASS' | 'WARN' | 'FAIL' | string
  message: string
  impact: string | null
  suggestion: string | null
  details: Record<string, unknown>
}

export type DoctorReport = {
  live: boolean
  config_source: string | null
  checks: DoctorCheck[]
}

export type SkillResolveResult =
  | { kind: 'plain' }
  | {
      kind: 'skill'
      name: string
      arguments: string
      prompt: string
      model_override: string | null
    }

export type SessionStatusInfo = {
  session_id: string
  generation: number
  revision: number
  status: string
  active: boolean
  cancellation_requested: boolean
  task_status: string | null
  environment_ref: string | null
  ui_mode?: UiMode | null
  context_tokens?: number
}

export type CompactResult = {
  session_id: string
  generation: number
  revision: number
  triggered: boolean
  kind: string
  before_tokens?: number | null
  after_tokens?: number | null
  context_tokens?: number | null
}

export type SendMessageAttachment = {
  attachment_id: string
  filename: string
  media_type: string
}

export type UploadedAttachment = {
  attachment_id: string
  filename: string
  media_type: string
  size: number
}

export type SendMessageOptions = {
  provider_name?: string
  model?: string
  attachments?: SendMessageAttachment[]
}

/**
 * Pending approval as returned by GET /api/sessions/{id}/approvals. The list
 * mirrors the approval.requested payload; `deadline_at` is accepted as an
 * alias of `expires_at`, and items may carry extra tool metadata.
 */
export type PendingApprovalRequest = {
  approval_id: string
  request_id?: string
  revision?: number
  intent_summary?: string
  items?: Array<Record<string, unknown>>
  expires_at?: string
  deadline_at?: string
  request_status?: string
}

export type PendingQuestion = {
  question_id: string
  question: string
  tool_call_id?: string | null
  request_id?: string | null
  run_id?: string | null
}

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

  createMemory(payload: { content: string; memory_type?: 'fact' | 'procedure' }): Promise<{ memory: ManagedMemory; status: string }> {
    return this.request('/api/memories', {
      method: 'POST',
      body: JSON.stringify({ content: payload.content, memory_type: payload.memory_type ?? 'fact' }),
    })
  }

  updateMemory(memoryId: string, payload: { content: string }): Promise<{ memory: ManagedMemory; status: string }> {
    return this.request(`/api/memories/${encodeURIComponent(memoryId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  }

  deleteMemory(memoryId: string): Promise<{ memory_id: string; deleted: boolean; status: string }> {
    return this.request(`/api/memories/${encodeURIComponent(memoryId)}`, {
      method: 'DELETE',
    })
  }

  memoryHistory(memoryId: string): Promise<MemoryHistory> {
    return this.request(`/api/memories/${encodeURIComponent(memoryId)}/history`)
  }

  memoryGraph(): Promise<MemoryGraph> {
    return this.request('/api/memories/graph')
  }

  compileMemory(memoryId: string): Promise<CompileJob> {
    return this.request(`/api/memories/${encodeURIComponent(memoryId)}/compile`, { method: 'POST' })
  }

  compileStatus(jobId: string): Promise<CompileJob> {
    return this.request(`/api/memory-compilations/${encodeURIComponent(jobId)}`)
  }

  runImplicitFeedback(): Promise<ImplicitFeedbackRun> {
    return this.request('/api/memories/implicit-feedback', { method: 'POST' })
  }

  implicitFeedbackStatus(jobId: string): Promise<ImplicitFeedbackStatus> {
    return this.request(`/api/memories/implicit-feedback/${encodeURIComponent(jobId)}`)
  }

  providers(): Promise<{ providers: ProviderProfile[] }> {
    return this.request('/api/providers')
  }

  providerTemplates(): Promise<{ templates: ProviderTemplate[] }> {
    return this.request('/api/provider-templates')
  }

  upsertProvider(payload: ProviderPayload, options: { overwrite?: boolean } = {}): Promise<ProviderWriteResult> {
    const suffix = options.overwrite === true ? '?overwrite=true' : ''
    return this.request(`/api/providers${suffix}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  patchProvider(name: string, payload: ProviderPayload): Promise<ProviderWriteResult> {
    return this.request(`/api/providers/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  }

  deleteProvider(name: string): Promise<ProviderDeleteResult> {
    return this.request(`/api/providers/${encodeURIComponent(name)}`, { method: 'DELETE' })
  }

  /** `{name}` probes a stored profile; a full ProviderPayload probes unsaved fields. */
  testProvider(payload: { name: string } | ProviderPayload): Promise<ProviderTestResult> {
    return this.request('/api/providers/test', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  providerModels(name: string): Promise<ProviderModels> {
    return this.request(`/api/providers/${encodeURIComponent(name)}/models`)
  }

  listSkills(): Promise<{ skills: SkillInfo[]; issues?: SkillIssueInfo[] }> {
    return this.request('/api/skills')
  }

  /** Import a skill folder as JSON files; paths are relative like `dir/SKILL.md`. */
  importSkill(
    files: Array<{ path: string; content: string }>,
    overwrite = false,
  ): Promise<{ status: string; skill: SkillInfo }> {
    return this.request('/api/skills/import', {
      method: 'POST',
      body: JSON.stringify({ files, overwrite }),
    })
  }

  deleteSkill(name: string): Promise<{ deleted?: boolean; resurrected?: boolean; message?: string }> {
    return this.request(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' })
  }

  resolveSkill(text: string): Promise<SkillResolveResult> {
    return this.request('/api/skills/resolve', {
      method: 'POST',
      body: JSON.stringify({ text }),
    })
  }

  meta(): Promise<MetaInfo> {
    return this.request('/api/meta')
  }

  doctor(options: { live?: boolean } = {}): Promise<DoctorReport> {
    const suffix = options.live === true ? '?live=true' : ''
    return this.request(`/api/doctor${suffix}`)
  }

  sessionStatus(sessionId: string): Promise<SessionStatusInfo> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/status`)
  }

  compactSession(sessionId: string): Promise<CompactResult> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/compact`, { method: 'POST' })
  }

  archiveSession(sessionId: string): Promise<{ session_id: string; job_id: string; status: string }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/archive`, { method: 'POST' })
  }

  /** GET /api/sessions/{id}/archive-status — latest finalization job for one session. */
  archiveStatus(sessionId: string): Promise<ArchiveStatus> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/archive-status`)
  }

  /** GET /api/archive-status — latest archive job per session, for badge restore. */
  archiveStatuses(): Promise<{ sessions: Record<string, ArchiveStatus> }> {
    return this.request('/api/archive-status')
  }

  /**
   * PATCH /api/sessions/{id} — persists a title override (backend batch).
   * Response mirrors the session summary; callers refresh the list anyway.
   */
  patchSession(sessionId: string, patch: { title: string }): Promise<SessionSummary> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  }

  /** DELETE /api/sessions/{id} → 204; 409 while the session has an active run. */
  deleteSession(sessionId: string): Promise<void> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
  }

  setUiMode(sessionId: string, uiMode: UiMode): Promise<{ session_id: string; ui_mode: UiMode }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/mode`, {
      method: 'POST',
      body: JSON.stringify({ ui_mode: uiMode }),
    })
  }

  pendingApprovals(sessionId: string): Promise<{ approvals: PendingApprovalRequest[] }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/approvals`)
  }

  pendingQuestions(sessionId: string): Promise<{ questions: PendingQuestion[] }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/questions`)
  }

  answerQuestion(sessionId: string, questionId: string, text: string): Promise<{ question_id: string; accepted?: boolean }> {
    return this.request(
      `/api/sessions/${encodeURIComponent(sessionId)}/questions/${encodeURIComponent(questionId)}/answer`,
      { method: 'POST', body: JSON.stringify({ text }) },
    )
  }

  uploadAttachment(
    sessionId: string,
    payload: { filename: string; media_type: string; data_base64: string },
  ): Promise<UploadedAttachment> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/attachments`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  sendMessage(sessionId: string, requestId: string, text: string, options: SendMessageOptions = {}): Promise<{ accepted: boolean }> {
    const body: Record<string, unknown> = { request_id: requestId, text }
    // Per-message provider/model pass-through: an in-flight turn keeps its
    // starting model; the override takes effect from the next turn.
    if (options.provider_name !== undefined && options.provider_name !== '') body.provider_name = options.provider_name
    if (options.model !== undefined && options.model !== '') body.model = options.model
    if (options.attachments !== undefined && options.attachments.length > 0) {
      body.attachments = options.attachments
    }
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: 'POST',
      body: JSON.stringify(body),
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
    // 204 (session delete) and other bodyless responses parse as `{}`.
    const text = await response.text()
    let payload: Record<string, unknown> = {}
    if (text.length > 0) {
      try {
        payload = JSON.parse(text) as Record<string, unknown>
      } catch {
        if (response.ok) throw new HttpError(response.status, 'invalid_json', '服务返回了无法解析的响应。', true)
      }
    }
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
