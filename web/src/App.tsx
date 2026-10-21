import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { EventConnection, type ConnectionState } from './api/connection'
import { HomeMasterApi, HttpError, type HistoryMessage, type ItemChoice, type MemorySnapshot, type MetaInfo, type ProviderProfile, type SessionStatusInfo, type SessionSummary, type SkillInfo, type UiMode } from './api/http'
import type { ApprovalDecisions } from './components/ApprovalDialog'
import { ApprovalDialog } from './components/ApprovalDialog'
import { ComposerPanel, type ModelSelection } from './components/composer/ComposerPanel'
import { QueueDock, type QueuedPrompt } from './components/composer/QueueDock'
import { MemoryPage } from './components/MemoryPage'
import { PermissionsPage } from './components/PermissionsPage'
import { ReasoningRow } from './components/ReasoningRow'
import { GlobalShortcuts } from './components/ShortcutHelpDialog'
import { ToastProvider, useFaviconStatus, useToast } from './components/toast'
import { ToolGroup } from './components/ToolGroup'
import { TranscriptSearch, type TranscriptSearchEntry } from './components/TranscriptSearch'
import { WelcomePanel } from './components/WelcomePanel'
import { useScrollFollow } from './hooks/useScrollFollow'
import { OnboardingPage } from './pages/OnboardingPage'
import { SessionsPage } from './pages/SessionsPage'
import { SettingsPage } from './pages/SettingsPage'
import { SkillsPage } from './pages/SkillsPage'
import { chatHash, navigate, parseHash, useRoute } from './router'
import type { WebEvent } from './protocol/events'
import { initialConversationState, reduceConversation } from './state/conversation'
import { projectSessionTurns, type Turn } from './state/projection'

const api = new HomeMasterApi()

type DisplayOptions = { recording: boolean; requestedSessionId: string | null }

function readDisplayOptions(): DisplayOptions {
  const params = new URLSearchParams(window.location.search)
  return {
    recording: params.get('record') === '1',
    requestedSessionId: params.get('session_id'),
  }
}

function sessionTitle(session: SessionSummary): string {
  const title = session.title.trim()
  if (title.length > 0) return title
  return `会话 ${session.session_id.slice(0, 8)}`
}

function formatRelativeTime(value: string | null): string {
  if (value === null) return ''
  const time = new Date(value).valueOf()
  if (Number.isNaN(time)) return ''
  const diff = Date.now() - time
  if (diff < 0) return ''
  const minute = 60_000
  if (diff < minute) return '刚刚'
  if (diff < 60 * minute) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < 24 * 60 * minute) return `${Math.floor(diff / (60 * minute))} 小时前`
  if (diff < 30 * 24 * 60 * minute) return `${Math.floor(diff / (24 * 60 * minute))} 天前`
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit' }).format(new Date(time))
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? fallback : JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // localStorage may be unavailable; sidebar cosmetics are best-effort.
  }
}

type SessionSignal = 'running' | 'awaiting' | 'done'

export function App() {
  return (
    <ToastProvider>
      <AppShell />
    </ToastProvider>
  )
}

function AppShell() {
  const toast = useToast()
  const displayOptions = useMemo(readDisplayOptions, [])
  const route = useRoute()
  const routeRef = useRef(route)
  routeRef.current = route
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionQuery, setSessionQuery] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryMessage[]>([])
  const [state, dispatch] = useReducer(reduceConversation, initialConversationState)
  const [connectionState, setConnectionState] = useState<ConnectionState>('offline')
  const [submitted, setSubmitted] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // userTextsRef holds the sent text for turns whose terminal event already
  // retired their `submitted` entry — the user bubble must survive the cleanup.
  const userTextsRef = useRef<Record<string, string>>({})
  const [approvalBusy, setApprovalBusy] = useState(false)
  const [approvalError, setApprovalError] = useState<string | null>(null)
  const [grantsSignal, setGrantsSignal] = useState(0)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [historyCollapsed, setHistoryCollapsed] = useState(
    () => localStorage.getItem('homemaster:web:history-collapsed') === 'true',
  )
  const [memorySnapshot, setMemorySnapshot] = useState<MemorySnapshot | null>(null)
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [memoryError, setMemoryError] = useState<string | null>(null)
  // files 档下 /api/memories 503 → 降级说明卡而非整页错误。
  const [memoryUnavailable, setMemoryUnavailable] = useState(false)
  const [sessionSignals, setSessionSignals] = useState<Record<string, SessionSignal>>({})
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [providers, setProviders] = useState<ProviderProfile[]>([])
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [meta, setMeta] = useState<MetaInfo | null>(null)
  const [sessionStatus, setSessionStatus] = useState<SessionStatusInfo | null>(null)
  const [modelSelection, setModelSelection] = useState<ModelSelection>({})
  const [queued, setQueued] = useState<QueuedPrompt[]>([])
  const [draftSignal, setDraftSignal] = useState<{ seq: number; text: string } | null>(null)
  const [contextLimit, setContextLimit] = useState<number | null>(null)
  const connectionRef = useRef<EventConnection | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const newSessionRef = useRef<() => void>(() => {})
  const ensuringChatRef = useRef(false)

  const dismissNotice = useCallback(() => {
    if (noticeTimerRef.current !== null) clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = null
    setNotice(null)
  }, [])

  const showNotice = useCallback((message: string) => {
    if (noticeTimerRef.current !== null) clearTimeout(noticeTimerRef.current)
    setNotice(message)
    noticeTimerRef.current = setTimeout(() => {
      noticeTimerRef.current = null
      setNotice(null)
    }, 8000)
  }, [])

  // Clear a pending auto-dismiss timer on unmount.
  useEffect(() => () => {
    if (noticeTimerRef.current !== null) clearTimeout(noticeTimerRef.current)
  }, [])

  // Sent texts are session-scoped; switching sessions drops both the staging
  // map and the rendered-text cache (the target reloads via history()).
  // Session chrome (queue/status/meter/model pick) is per-session too.
  useEffect(() => {
    userTextsRef.current = {}
    setSubmitted({})
    setQueued([])
    setSessionStatus(null)
    setContextLimit(null)
    setModelSelection(sessionId === null ? {} : readJson<ModelSelection>(`hm.model.${sessionId}`, {}))
  }, [sessionId])

  // Provider list + meta + skills are session-independent; one fetch at mount.
  useEffect(() => {
    void api.providers().then(listed => {
      setProviders(listed.providers)
      // 首启无已配置 chat provider → 引导页（detection≠consent：只此一次自动跳转）。
      const hasChatProvider = listed.providers.some(
        item => item.kind !== 'embedding' && (item.api_key_configured || item.keyless),
      )
      if (!hasChatProvider && routeRef.current.kind !== 'onboarding') navigate('#/onboarding')
    }).catch(() => {})
    void api.meta().then(setMeta).catch(() => {})
    void api.listSkills().then(listed => { setSkills(listed.skills) }).catch(() => {})
  }, [])

  /** Composer pickers refresh after Settings/Onboarding writes land. */
  const refreshProviders = useCallback(async () => {
    try {
      setProviders((await api.providers()).providers)
    } catch { /* picker keeps the stale list */ }
  }, [])

  /**
   * 断连重连后回补：WS 重新 connected 时把 pending approvals/questions 合入
   * state（REST 结果对该 session 是权威的，hydrate 会清掉已决议的回补项）。
   */
  const hydratePending = useCallback(async (sid: string) => {
    const [status, approvals, questions] = await Promise.all([
      api.sessionStatus(sid).catch(() => null),
      api.pendingApprovals(sid).catch(() => null),
      api.pendingQuestions(sid).catch(() => null),
    ])
    if (sessionIdRef.current !== sid) return // stale fetch after a session switch
    if (status !== null) setSessionStatus(status)
    dispatch({
      type: 'session.hydrate_pending',
      sessionId: sid,
      approvals: approvals?.approvals ?? [],
      questions: questions?.questions ?? [],
      uiMode: status?.ui_mode ?? null,
    })
  }, [])

  const refreshSessions = useCallback(async () => {
    const listed = await api.listSessions()
    setSessions(listed.sessions)
    return listed.sessions
  }, [])

  const refreshMemories = useCallback(async () => {
    setMemoryLoading(true)
    try {
      setMemorySnapshot(await api.memories())
      setMemoryError(null)
      setMemoryUnavailable(false)
    } catch (error) {
      // files 档的 503/memory_unavailable 走降级说明卡；其余错误仍是错误横幅。
      if (error instanceof HttpError && (error.status === 503 || error.code === 'memory_unavailable')) {
        setMemoryUnavailable(true)
        setMemoryError(null)
      } else {
        setMemoryUnavailable(false)
        setMemoryError('记忆服务暂不可用，请稍后重试。')
      }
    } finally {
      setMemoryLoading(false)
    }
  }, [])

  useEffect(() => { sessionIdRef.current = sessionId }, [sessionId])

  // Session status dots derive locally from the WS events we already receive.
  const handleWebEvent = useCallback((event: WebEvent) => {
    if (event.type === 'permission.grants_changed') setGrantsSignal(value => value + 1)
    if (event.type === 'context.compacted' && typeof event.payload.before_tokens === 'number') {
      // The auto-compaction ceiling approximates the real context window.
      setContextLimit(event.payload.before_tokens)
    }
    if (event.type === 'session.mode_changed' && event.payload.ui_mode !== undefined) {
      const mode = event.payload.ui_mode
      setSessionStatus(current => current !== null && current.session_id === event.session_id
        ? { ...current, ui_mode: mode }
        : current)
    }
    dispatch(event)
    if (
      event.type === 'run.completed' ||
      event.type === 'run.failed' ||
      event.type === 'run.cancelled'
    ) {
      // Retire the staging entry once its turn terminates so `submitted` only
      // tracks in-flight sends; userTextsRef keeps the bubble's text alive.
      setSubmitted(items => {
        if (!(event.request_id in items)) return items
        const next = { ...items }
        delete next[event.request_id]
        return next
      })
    }
    setSessionSignals(signals => {
      let next: SessionSignal | null = null
      switch (event.type) {
        case 'run.started':
          next = 'running'
          break
        case 'approval.requested':
        case 'question.asked':
          next = 'awaiting'
          break
        case 'run.completed':
        case 'run.failed':
        case 'run.cancelled':
          // A terminal event on the session currently on screen is already "read".
          next = event.session_id === sessionIdRef.current ? null : 'done'
          break
        default:
          return signals
      }
      if ((signals[event.session_id] ?? null) === next) return signals
      const updated = { ...signals }
      if (next === null) delete updated[event.session_id]
      else updated[event.session_id] = next
      return updated
    })
  }, [])

  const selectSession = useCallback(async (nextId: string) => {
    connectionRef.current?.stop()
    sessionIdRef.current = nextId
    setSessionId(nextId)
    setConnectionState('connecting')
    // URL is the source of truth: keep the hash on the opened session — but
    // only inside the chat route. Background adoption (e.g. '#/chat' resolving
    // after a first-launch '#/onboarding' redirect) must never yank the user
    // off a non-chat page.
    if (parseHash(window.location.hash).kind === 'chat') navigate(chatHash(nextId))
    setSessionSignals(signals => {
      if (!(nextId in signals)) return signals
      const updated = { ...signals }
      delete updated[nextId]
      return updated
    })
    try {
      const restored = await api.history(nextId)
      setHistory(restored.messages)
    } catch (error) {
      showNotice(error instanceof Error ? error.message : 'Could not load session history.')
    }
    const connection = new EventConnection(nextId, undefined, {
      onEvent: handleWebEvent,
      onStateChange: next => {
        setConnectionState(next)
        // 每次（重）连上都回补 pending 审批/问题/状态——服务端是权威。
        if (next === 'connected') void hydratePending(nextId)
      },
      onReject: () => {
        showNotice('该会话在服务端已不存在，已为你新建会话。')
        newSessionRef.current()
      },
    })
    connectionRef.current = connection
    connection.start()
  }, [handleWebEvent, hydratePending, showNotice])

  // Dedupe concurrent creators: '#/chat' with an empty list triggers
  // ensureChatSession → newSession at the same moment a sidebar '新建会话'
  // click does — without a shared in-flight promise that posts two
  // createSession calls and leaves an orphan empty session.
  const newSessionInflightRef = useRef<Promise<void> | null>(null)
  const newSession = useCallback(async () => {
    const inflight = newSessionInflightRef.current
    if (inflight) return inflight
    const task = (async () => {
      connectionRef.current?.stop()
      connectionRef.current = null
      setConnectionState('connecting')
      setSessionId(null)
      try {
        const created = await api.createSession()
        await refreshSessions()
        await selectSession(created.session_id)
      } catch (error) {
        showNotice(error instanceof Error ? error.message : 'Could not create a session.')
      }
    })().finally(() => { newSessionInflightRef.current = null })
    newSessionInflightRef.current = task
    return task
  }, [refreshSessions, selectSession, showNotice])

  useEffect(() => { newSessionRef.current = () => { void newSession() } }, [newSession])

  // Boot once: populate the sidebar list and honor the legacy ?session_id=
  // deep-link. Session selection itself belongs solely to the route-sync
  // effect below — a single owner avoids double-connect races.
  useEffect(() => {
    void refreshSessions().then(existing => {
      if (displayOptions.requestedSessionId !== null) {
        if (existing.some(session => session.session_id === displayOptions.requestedSessionId)) {
          void selectSession(displayOptions.requestedSessionId)
        } else {
          showNotice(`指定会话不存在：${displayOptions.requestedSessionId}`)
        }
        return
      }
      const landing = routeRef.current
      if (landing.kind === 'chat' && landing.sessionId !== null &&
        !existing.some(session => session.session_id === landing.sessionId)) {
        showNotice(`指定会话不存在：${landing.sessionId}`)
      }
    }).catch(error => { showNotice(error instanceof Error ? error.message : 'Service unavailable.') })
    return () => { connectionRef.current?.stop() }
  }, [displayOptions.requestedSessionId, refreshSessions, selectSession, showNotice])

  // Route → session: '#/chat/{id}' selects it; a bare '#/chat' keeps the
  // current one or adopts the first/new session (e.g. after landing on a
  // non-chat route or deleting the selected session).
  const ensureChatSession = useCallback(async () => {
    if (ensuringChatRef.current) return
    ensuringChatRef.current = true
    try {
      const listed = await refreshSessions()
      const first = listed[0]
      if (first !== undefined) await selectSession(first.session_id)
      else await newSession()
    } finally {
      ensuringChatRef.current = false
    }
  }, [newSession, refreshSessions, selectSession])

  useEffect(() => {
    if (route.kind !== 'chat') return
    if (route.sessionId !== null) {
      if (route.sessionId !== sessionIdRef.current) void selectSession(route.sessionId)
      return
    }
    if (sessionIdRef.current === null) void ensureChatSession()
  }, [route, ensureChatSession, selectSession])

  useEffect(() => { void refreshMemories() }, [refreshMemories])

  const normalizedSessionQuery = sessionQuery.trim().toLocaleLowerCase()
  const visibleSessions = useMemo(() => {
    if (normalizedSessionQuery.length === 0) return sessions
    return sessions.filter(session =>
      sessionTitle(session).toLocaleLowerCase().includes(normalizedSessionQuery),
    )
  }, [normalizedSessionQuery, sessions])

  // Composer slash menu: only enabled, user-invocable skills complete as /cmd.
  const slashCommands = useMemo(
    () => skills
      .filter(skill => skill.enabled && skill.user_invocable)
      .map(skill => ({
        name: skill.command_name ?? skill.name,
        description: skill.description,
        group: 'skills',
      })),
    [skills],
  )

  const turns = useMemo(
    () => projectSessionTurns(state, sessionId, { ...userTextsRef.current, ...submitted }),
    [sessionId, state, submitted],
  )
  const active = turns.find(turn => turn.status === 'pending' || turn.status === 'running')
  const approvalTurn = turns.find(turn => turn.approval !== null)
  const canSend = connectionState === 'connected' && sessionId !== null && active === undefined
  useFaviconStatus(active !== undefined)

  const pendingQuestion = sessionId === null
    ? null
    : Object.values(state.questions).find(question => question.sessionId === sessionId) ?? null
  const uiMode: UiMode = sessionId !== null ? state.uiModes[sessionId] ?? 'act' : 'act'
  const latestUsage = useMemo(() => {
    for (let index = turns.length - 1; index >= 0; index -= 1) {
      const usage = turns[index]!.usage
      if (usage !== null) return usage
    }
    return null
  }, [turns])

  const itemCount = useMemo(
    () => history.length + turns.reduce((count, turn) => count + turn.steps.length + (turn.userText === null ? 0 : 1), 0),
    [history, turns],
  )
  const follow = useScrollFollow({ itemCount, turnCount: turns.length })
  const releaseFollow = follow.releaseFollow

  const sendText = useCallback(async (text: string): Promise<boolean> => {
    const trimmed = text.trim()
    if (!canSend || sessionId === null || trimmed.length === 0) return false
    const requestId = crypto.randomUUID()
    userTextsRef.current[requestId] = trimmed
    setSubmitted(items => ({ ...items, [requestId]: trimmed }))
    try {
      // 逐消息透传 provider/model 选择；未选择时不带字段，服务端用默认 provider。
      await api.sendMessage(sessionId, requestId, trimmed, {
        provider_name: modelSelection.provider_name,
        model: modelSelection.model,
      })
    } catch (error) {
      // The request never reached the server: drop the optimistic entries (no
      // turn will ever arrive for this requestId) and report failure so the
      // composer keeps the typed input.
      delete userTextsRef.current[requestId]
      setSubmitted(items => {
        if (!(requestId in items)) return items
        const next = { ...items }
        delete next[requestId]
        return next
      })
      showNotice(error instanceof HttpError ? error.message : 'Message could not be sent.')
      return false
    }
    return true
  }, [canSend, sessionId, modelSelection, showNotice])

  // QueueDock：busy 时 composer 把消息排进本地队列，回合终态按序自动发送。
  const enqueue = useCallback((text: string) => {
    setQueued(list => [...list, { id: crypto.randomUUID(), text }])
  }, [])

  const flushInflightRef = useRef<string | null>(null)
  useEffect(() => {
    if (!canSend || queued.length === 0) return
    const head = queued[0]!
    if (flushInflightRef.current === head.id) return
    flushInflightRef.current = head.id
    void sendText(head.text).then(sent => {
      // 失败时消息留在队列里，等下一个 canSend 边沿重试（session_busy 等
      // 409 说明服务端回合其实还没完）。
      if (sent) setQueued(list => list.filter(item => item.id !== head.id))
      if (flushInflightRef.current === head.id) flushInflightRef.current = null
    })
  }, [canSend, queued, sendText])

  const enqueueOrSend = useCallback((text: string) => {
    if (canSend) {
      void sendText(text)
      return
    }
    if (connectionState === 'connected' && sessionId !== null) {
      enqueue(text)
      return
    }
    showNotice('当前未连接，无法发送。')
  }, [canSend, connectionState, enqueue, sendText, sessionId, showNotice])

  // Composer 接管：pending question 换成问答卡，answer 走 POST 后乐观移除
  // （服务端会再广播 question.answered，幂等）。
  const answerQuestion = useCallback(async (questionId: string, text: string): Promise<boolean> => {
    if (sessionId === null) return false
    try {
      await api.answerQuestion(sessionId, questionId, text)
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '回答提交失败。')
      return false
    }
    dispatch({
      type: 'question.answered',
      session_id: sessionId,
      run_id: '',
      request_id: '',
      seq: 0, // client-synthesized echo — not a server-stamped wire event
      ts: Date.now() / 1000,
      payload: { question_id: questionId },
    })
    return true
  }, [sessionId, showNotice])

  const setUiMode = useCallback(async (next: UiMode) => {
    if (sessionId === null) return
    try {
      await api.setUiMode(sessionId, next)
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '模式切换失败。')
      return
    }
    // 乐观回显；多端同步以服务端广播的 session.mode_changed 为准。
    dispatch({
      type: 'session.mode_changed',
      session_id: sessionId,
      run_id: '',
      request_id: '',
      seq: 0, // client-synthesized echo — not a server-stamped wire event
      ts: Date.now() / 1000,
      payload: { ui_mode: next },
    })
    setSessionStatus(current => current?.session_id === sessionId ? { ...current, ui_mode: next } : current)
  }, [sessionId, showNotice])

  const compactNow = useCallback(async () => {
    if (sessionId === null) return
    try {
      const result = await api.compactSession(sessionId)
      toast.show(result.triggered
        ? { title: '已触发上下文压缩', description: `kind: ${result.kind}` }
        : { title: '当前无需压缩', description: `kind: ${result.kind}` })
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '压缩上下文失败。')
    }
  }, [sessionId, showNotice, toast])

  const [archivingIds, setArchivingIds] = useState<ReadonlySet<string>>(new Set())

  const archiveSession = useCallback(async (session: SessionSummary) => {
    setArchivingIds(current => new Set(current).add(session.session_id))
    try {
      await api.archiveSession(session.session_id)
      toast.show({
        title: `已归档会话 ${sessionTitle(session)}`,
        description: '记忆总结与整理在后台执行',
      })
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '归档会话失败。')
    } finally {
      setArchivingIds(current => {
        const next = new Set(current)
        next.delete(session.session_id)
        return next
      })
    }
  }, [showNotice, toast])

  const persistModelSelection = useCallback((selection: ModelSelection) => {
    setModelSelection(selection)
    if (sessionId !== null) writeJson(`hm.model.${sessionId}`, selection)
  }, [sessionId])

  const editMessage = useCallback((text: string) => {
    setDraftSignal(signal => ({ seq: (signal?.seq ?? 0) + 1, text }))
  }, [])

  const submitApproval = async (decisions: ApprovalDecisions, submissionId: string): Promise<void> => {
    const approval = approvalTurn?.approval
    if (approval === null || approval === undefined) return
    setApprovalBusy(true)
    setApprovalError(null)
    try {
      const resolution = await api.submitApproval(approval.approvalId, {
        protocol_version: 2,
        submission_id: submissionId,
        request_revision: approval.revision,
        decisions: approval.items.map(item => ({
          item_id: item.item_id,
          choice: decisions[item.item_id] as ItemChoice,
        })),
      })
      if (resolution.request_status === 'blocked') {
        showNotice('本次调用未执行：部分申请被拒绝，已完成的步骤不受影响。')
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Approval failed.'
      setApprovalError(message)
      showNotice(message)
    } finally {
      setApprovalBusy(false)
    }
  }

  const cancelApproval = async (submissionId: string): Promise<void> => {
    const approval = approvalTurn?.approval
    if (approval === null || approval === undefined) return
    setApprovalBusy(true)
    try {
      await api.cancelApproval(approval.approvalId, {
        submission_id: submissionId,
        request_revision: approval.revision,
      })
    } catch (error) {
      showNotice(error instanceof Error ? error.message : 'Approval failed.')
    } finally {
      setApprovalBusy(false)
    }
  }

  const toggleHistory = () => {
    setHistoryCollapsed(value => {
      const next = !value
      localStorage.setItem('homemaster:web:history-collapsed', String(next))
      return next
    })
  }

  // Session delete is server-side now (DELETE /api/sessions/{id} → 204); an
  // active run makes the backend answer 409, surfaced as a notice.
  const removeSession = useCallback(async (session: SessionSummary) => {
    const title = sessionTitle(session)
    if (!window.confirm(`删除会话「${title}」？此操作不可撤销。`)) return
    try {
      await api.deleteSession(session.session_id)
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '删除会话失败。')
      return
    }
    setSessions(list => list.filter(item => item.session_id !== session.session_id))
    toast.show({ title: `已删除会话「${title}」` })
    if (session.session_id === sessionIdRef.current) {
      connectionRef.current?.stop()
      connectionRef.current = null
      sessionIdRef.current = null
      setSessionId(null)
      setHistory([])
      navigate('#/sessions')
    }
  }, [showNotice, toast])

  const startRename = useCallback((session: SessionSummary) => {
    setRenamingId(session.session_id)
    setRenameDraft(sessionTitle(session))
  }, [])

  // Rename persists via PATCH /api/sessions/{id} {title}; the list entry is
  // updated optimistically and rolled back on failure.
  const commitRename = useCallback((session: SessionSummary) => {
    const value = renameDraft.trim()
    setRenamingId(null)
    if (value.length === 0 || value === sessionTitle(session)) return
    const previous = session.title
    setSessions(list => list.map(item =>
      item.session_id === session.session_id ? { ...item, title: value } : item,
    ))
    void api.patchSession(session.session_id, { title: value }).catch(error => {
      setSessions(list => list.map(item =>
        item.session_id === session.session_id ? { ...item, title: previous } : item,
      ))
      showNotice(error instanceof Error ? error.message : '重命名会话失败。')
    })
  }, [renameDraft, showNotice])

  const searchEntries = useMemo(() => {
    const entries: TranscriptSearchEntry[] = []
    history.forEach((message, index) => {
      entries.push({ key: `h${index}`, text: `${message.thinking ?? ''} ${message.text}` })
    })
    for (const turn of turns) {
      if (turn.userText !== null) entries.push({ key: `${turn.requestId}:user`, text: turn.userText })
      turn.steps.forEach((step, index) => {
        const key = `${turn.requestId}:${index}`
        if (step.kind === 'tool_group') {
          step.tools.forEach(tool => {
            entries.push({ key: `${key}:${tool.toolCallId}`, text: `${tool.name} ${tool.argSummary} ${tool.outputPreview} ${tool.error ?? ''}` })
          })
        } else {
          entries.push({ key, text: step.kind === 'error' ? step.message : step.text })
        }
      })
    }
    return entries
  }, [history, turns])

  // Mod+F dispatches through the global shortcuts registry (B, state/shortcuts.ts).
  const openTranscriptSearch = useCallback(() => {
    if (route.kind !== 'chat') return
    setSearchOpen(true)
    releaseFollow()
  }, [releaseFollow, route.kind])

  return (
    <div className="shell" data-recording={displayOptions.recording || undefined}>
      <aside className="sidebar" data-open={sidebarOpen || undefined}>
        <div className="brand"><span className="brand-mark">HM</span><div><strong>Console</strong><small>Local agent console</small></div></div>
        <div className="sidebar-views" aria-label="主导航">
          <button type="button" aria-label="对话" data-active={route.kind === 'chat' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/chat') }}><span>◉</span>对话</button>
          <button type="button" aria-label="会话列表" data-active={route.kind === 'sessions' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/sessions') }}><span>☰</span>会话</button>
          <button type="button" aria-label="记忆管理" data-active={route.kind === 'memory' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/memory') }}><span>◇</span>记忆管理</button>
          <button type="button" aria-label="权限" data-active={route.kind === 'permissions' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/permissions') }}><span>▣</span>权限</button>
          <button type="button" aria-label="技能" data-active={route.kind === 'skills' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/skills') }}><span>✦</span>技能</button>
          <button type="button" aria-label="设置" data-active={route.kind === 'settings' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/settings') }}><span>⚙</span>设置</button>
        </div>
        <button className="new-chat" type="button" onClick={() => { setSidebarOpen(false); navigate('#/chat'); void newSession() }}>＋ 新建会话</button>
        <div className="history-heading"><span>历史会话</span><button type="button" aria-label={historyCollapsed ? '展开历史会话' : '折叠历史会话'} onClick={toggleHistory}>{historyCollapsed ? '＋' : '−'}</button></div>
        {!historyCollapsed && <>
          <label className="session-search">
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              value={sessionQuery}
              onChange={event => { setSessionQuery(event.target.value) }}
              placeholder="搜索会话…"
              aria-label="搜索会话"
            />
          </label>
          <nav aria-label="历史会话">
            {visibleSessions.map(session => {
              const title = sessionTitle(session)
              const itemMeta = [formatRelativeTime(session.updated_at), `${session.message_count} 条`]
                .filter(part => part.length > 0)
                .join(' · ')
              const signal = sessionSignals[session.session_id]
              return (
                <div
                  className="session-item"
                  key={session.session_id}
                  data-active={session.session_id === sessionId || undefined}
                >
                  <span className="session-dot" data-status={signal ?? 'idle'} aria-hidden="true" />
                  {renamingId === session.session_id ? (
                    <input
                      className="session-rename"
                      type="text"
                      value={renameDraft}
                      aria-label={`重命名会话 ${title}`}
                      autoFocus
                      onChange={event => { setRenameDraft(event.target.value) }}
                      onBlur={() => { commitRename(session) }}
                      onKeyDown={event => {
                        if (event.key === 'Enter') {
                          event.preventDefault()
                          commitRename(session)
                        } else if (event.key === 'Escape') {
                          setRenamingId(null)
                        }
                      }}
                    />
                  ) : (
                    <button
                      type="button"
                      className="session-open"
                      aria-label={`打开会话 ${title}`}
                      onClick={() => { setSidebarOpen(false); navigate(chatHash(session.session_id)) }}
                    >
                      <span>{title}</span>
                      <small>{itemMeta.length > 0 ? itemMeta : session.session_id.slice(0, 12)}</small>
                    </button>
                  )}
                  <span className="session-actions">
                    {meta !== null && meta.memory_mode === 'full' && (
                      <button
                        type="button"
                        aria-label={`归档会话 ${title}`}
                        title="归档：触发记忆总结与整理"
                        disabled={archivingIds.has(session.session_id)}
                        onClick={() => { void archiveSession(session) }}
                      >{archivingIds.has(session.session_id) ? '…' : '⤓'}</button>
                    )}
                    <button type="button" aria-label={`重命名会话 ${title}`} onClick={() => { startRename(session) }}>✎</button>
                    <button
                      type="button"
                      aria-label={`删除会话 ${title}`}
                      title="彻底删除该会话（不可撤销）"
                      onClick={() => { void removeSession(session) }}
                    >✕</button>
                  </span>
                </div>
              )
            })}
            {visibleSessions.length === 0 && <div className="session-empty">没有匹配的会话</div>}
          </nav>
        </>}
        {historyCollapsed && <div className="history-spacer" />}
        <div className="local-note"><span>●</span> Loopback only{meta !== null ? ` · v${meta.version}` : ''}</div>
      </aside>
      <main className="workspace">
        <header className="topbar"><button className="mobile-menu" type="button" aria-label="打开侧栏" onClick={() => { setSidebarOpen(value => !value) }}>☰</button><div><strong>{route.kind === 'memory' ? '记忆管理' : route.kind === 'permissions' ? '权限管理' : route.kind === 'skills' ? '技能' : route.kind === 'settings' ? '设置' : route.kind === 'onboarding' ? '首次配置' : route.kind === 'sessions' ? '全部会话' : '对话'}</strong><small>{route.kind === 'memory' ? '只读查看' : route.kind === 'permissions' ? '长期授权可撤销' : route.kind === 'chat' ? (sessionId ?? '正在启动…') : ''}</small></div><div className="connection" data-state={connectionState}><span />{connectionState}</div></header>
        {route.kind === 'permissions' ? (
          <PermissionsPage api={api} refreshSignal={grantsSignal} />
        ) : route.kind === 'memory' ? (
          <MemoryPage
            snapshot={memorySnapshot}
            loading={memoryLoading}
            error={memoryError}
            unavailable={memoryUnavailable}
            onRefresh={refreshMemories}
            loadHistory={memoryId => api.memoryHistory(memoryId)}
            compileMemory={memoryId => api.compileMemory(memoryId)}
            compileStatus={jobId => api.compileStatus(jobId)}
          />
        ) : route.kind === 'skills' ? (
          <SkillsPage api={api} />
        ) : route.kind === 'settings' ? (
          <SettingsPage api={api} onProvidersChanged={() => { void refreshProviders() }} />
        ) : route.kind === 'onboarding' ? (
          <OnboardingPage api={api} onDone={() => { void refreshProviders() }} />
        ) : route.kind === 'sessions' ? (
          <SessionsPage
            sessions={sessions}
            sessionId={sessionId}
            signals={sessionSignals}
            sessionTitle={sessionTitle}
            formatTime={formatRelativeTime}
          />
        ) : <>
          <section className="conversation" aria-live="polite" ref={follow.containerRef}>
            <div className="conversation-inner" ref={follow.contentRef}>
            {history.map((message, index) => (
              <HistoryRow
                key={`${index}:${message.role}`}
                message={message}
                searchKey={`h${index}`}
                defaultExpanded={displayOptions.recording}
                onEdit={editMessage}
                onResend={enqueueOrSend}
              />
            ))}
            {turns.map(turn => (
              <TurnView
                key={turn.requestId}
                turn={turn}
                recording={displayOptions.recording}
                onEdit={editMessage}
                onResend={enqueueOrSend}
              />
            ))}
            {history.length === 0 && turns.length === 0 && (
              <WelcomePanel
                recentSessions={sessions.filter(session => session.session_id !== sessionId).slice(0, 3)}
                sessionTitle={sessionTitle}
                onSuggestion={text => { void sendText(text) }}
                onContinue={id => { navigate(chatHash(id)) }}
              />
            )}
            </div>
          </section>
          {!follow.following && (
            <button
              type="button"
              className="to-bottom"
              onClick={() => { follow.scrollToBottom('smooth') }}
            >
              ↓ 回到底部{follow.unseenCount > 0 ? ` · ${follow.unseenCount} 条新消息` : ''}
            </button>
          )}
          <TranscriptSearch open={searchOpen} entries={searchEntries} onClose={() => { setSearchOpen(false) }} />
          {notice && <div className="notice" role="alert"><span>{notice}</span><button type="button" onClick={dismissNotice}>关闭</button></div>}
          <QueueDock
            items={queued}
            onRemove={id => { setQueued(list => list.filter(item => item.id !== id)) }}
            onClear={() => { setQueued([]) }}
          />
          <ComposerPanel
            sessionId={sessionId}
            busy={active !== undefined}
            disabled={connectionState !== 'connected' || sessionId === null}
            onSubmit={text => sendText(text)}
            onCancel={() => { if (sessionId) void api.cancel(sessionId) }}
            slashCommands={slashCommands}
            resolveMentions={() => Promise.resolve([])}
            onEnqueue={enqueue}
            injectDraft={draftSignal}
            mode={uiMode}
            onModeChange={next => { void setUiMode(next) }}
            providers={providers}
            modelValue={modelSelection}
            onModelChange={persistModelSelection}
            usage={latestUsage}
            sessionStatus={sessionStatus}
            contextLimit={contextLimit}
            onCompact={() => { void compactNow() }}
            pendingQuestion={pendingQuestion}
            onAnswerQuestion={answerQuestion}
            pendingApproval={approvalTurn?.approval ?? null}
          />
        </>}
      </main>
      {approvalTurn?.approval && <ApprovalDialog approval={approvalTurn.approval} busy={approvalBusy} error={approvalError} onSubmit={(decisions, submissionId) => { void submitApproval(decisions, submissionId) }} onClose={(submissionId) => { void cancelApproval(submissionId) }} />}
      <GlobalShortcuts onTranscriptSearch={openTranscriptSearch} />
    </div>
  )
}

function UserBubble({
  text,
  searchKey,
  onEdit,
  onResend,
}: {
  text: string
  searchKey?: string
  onEdit: (text: string) => void
  onResend: (text: string) => void
}) {
  return (
    <div className="user-row" data-search-key={searchKey}>
      <div className="user-bubble">
        <div className="user-text">{text}</div>
        <div className="user-actions">
          <button type="button" aria-label="编辑这条消息" title="回到输入框继续编辑（不改历史）" onClick={() => { onEdit(text) }}>编辑</button>
          <button type="button" aria-label="重发这条消息" title="原样再发一次" onClick={() => { onResend(text) }}>重发</button>
        </div>
      </div>
    </div>
  )
}

function TurnView({ turn, recording, onEdit, onResend }: {
  turn: Turn
  recording: boolean
  onEdit: (text: string) => void
  onResend: (text: string) => void
}) {
  let lastReasoning = -1
  turn.steps.forEach((step, index) => { if (step.kind === 'reasoning') lastReasoning = index })
  return (
    <article className="turn">
      {turn.userText !== null && (
        <UserBubble text={turn.userText} searchKey={`${turn.requestId}:user`} onEdit={onEdit} onResend={onResend} />
      )}
      <div className="assistant-row">
        {turn.steps.map((step, index) => {
          const key = `${turn.requestId}:${index}`
          switch (step.kind) {
            case 'reasoning':
              return (
                <ReasoningRow
                  key={key}
                  searchKey={key}
                  text={step.text}
                  running={turn.status === 'running' && index === lastReasoning}
                  defaultExpanded={recording}
                />
              )
            case 'tool_group':
              return <ToolGroup key={key} searchKeyPrefix={key} tools={step.tools} sessionId={turn.sessionId} defaultOpen={recording} />
            case 'answer':
              return <div className="markdown" data-search-key={key} key={key}><ReactMarkdown remarkPlugins={[remarkGfm]}>{step.text}</ReactMarkdown></div>
            case 'error':
              return <div className={step.status === 'failed' ? 'run-error' : 'run-cancelled'} data-search-key={key} key={key}>{step.message}</div>
          }
        })}
      </div>
    </article>
  )
}

function HistoryRow({ message, searchKey, defaultExpanded = false, onEdit, onResend }: {
  message: HistoryMessage
  searchKey?: string
  defaultExpanded?: boolean
  onEdit: (text: string) => void
  onResend: (text: string) => void
}) {
  if (message.role === 'user') {
    return <UserBubble text={message.text} searchKey={searchKey} onEdit={onEdit} onResend={onResend} />
  }
  return <div className="assistant-row">{message.thinking && <ReasoningRow text={message.thinking} running={false} defaultExpanded={defaultExpanded} searchKey={searchKey} />}{message.text && <div className="markdown" data-search-key={searchKey}><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown></div>}</div>
}
