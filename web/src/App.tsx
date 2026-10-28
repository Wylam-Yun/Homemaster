import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { EventConnection, type ConnectionState } from './api/connection'
import { HomeMasterApi, HttpError, type ArchiveStatus, type HistoryMessage, type ItemChoice, type MemorySnapshot, type MetaInfo, type ProviderModels, type ProviderProfile, type SendMessageAttachment, type SessionStatusInfo, type SessionSummary, type SkillInfo, type UiMode } from './api/http'
import type { ApprovalDecisions } from './components/ApprovalDialog'
import { ApprovalDialog } from './components/ApprovalDialog'
import { ComposerPanel, type AttachmentDraft, type ModelSelection } from './components/composer/ComposerPanel'
import { QueueDock, type QueuedPrompt } from './components/composer/QueueDock'
import { MemoryPage } from './components/MemoryPage'
import { FeedbackRunsPage } from './components/FeedbackRunsPage'
import { PermissionsPage } from './components/PermissionsPage'
import { ReasoningRow } from './components/ReasoningRow'
import { GlobalShortcuts, ShortcutHelpDialog } from './components/ShortcutHelpDialog'
import { ToastProvider, useFaviconStatus, useToast } from './components/toast'
import { ToolGroup } from './components/ToolGroup'
import { TranscriptSearch, type TranscriptSearchEntry } from './components/TranscriptSearch'
import {
  IconArchive,
  IconChat,
  IconCheck,
  IconCopy,
  IconEdit,
  IconMemory,
  IconMoon,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconSettings,
  IconShield,
  IconSparkles,
  IconSun,
  IconTrash,
} from './components/ui/icons'
import { WelcomePanel } from './components/WelcomePanel'
import { useScrollFollow } from './hooks/useScrollFollow'
import { useTheme } from './hooks/useTheme'
import { OnboardingPage } from './pages/OnboardingPage'
import { SessionsPage } from './pages/SessionsPage'
import { SettingsPage } from './pages/SettingsPage'
import { SkillsPage } from './pages/SkillsPage'
import { chatHash, navigate, parseHash, useRoute } from './router'
import type { WebEvent } from './protocol/events'
import { initialConversationState, reduceConversation } from './state/conversation'
import { projectSessionTurns, toolArgSummary, type ToolRecord, type Turn } from './state/projection'
import { CommandPalette, type CommandPaletteAction } from './components/CommandPalette'
import { formatShortcut, SHORTCUTS } from './state/shortcuts'

const api = new HomeMasterApi()

// Stable wrapper: ModelPicker caches per provider name and needs a stable ref.
const loadProviderModels = (name: string): Promise<ProviderModels> => api.providerModels(name)

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
  if (title.length > 0 && title !== '会话 session-') return title
  const stripped = session.session_id.replace(/^session[-_]/i, '')
  const shortId = (stripped.length > 0 ? stripped : session.session_id).slice(0, 8)
  return `会话 ${shortId}`
}

function archiveBadgeText(status: ArchiveStatus | undefined): string | null {
  if (status === undefined) return null
  switch (status.state) {
    case 'queued':
    case 'processing':
      return '整理中…'
    case 'completed':
      return status.extracted_count !== null && status.extracted_count !== undefined && status.extracted_count > 0
        ? `已提取 ${status.extracted_count} 条记忆`
        : '已整理（无新记忆）'
    case 'failed':
      return '整理失败'
    default:
      return null
  }
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

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  const chunk = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk))
  }
  return btoa(binary)
}

type SessionSignal = 'running' | 'awaiting' | 'done'

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: '连接中',
  connected: '已连接',
  reconnecting: '重连中',
  offline: '未连接',
}

export function App() {
  return (
    <ToastProvider>
      <AppShell />
    </ToastProvider>
  )
}

function AppShell() {
  const toast = useToast()
  const { theme, toggleTheme } = useTheme()
  const displayOptions = useMemo(readDisplayOptions, [])
  const route = useRoute()
  const routeRef = useRef(route)
  routeRef.current = route
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionsLoaded, setSessionsLoaded] = useState(false)
  const [archiveStates, setArchiveStates] = useState<Record<string, ArchiveStatus>>({})
  const archivePollTimers = useRef<Record<string, number>>({})
  const [sessionQuery, setSessionQuery] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryMessage[]>([])
  const [state, dispatch] = useReducer(reduceConversation, initialConversationState)
  const [connectionState, setConnectionState] = useState<ConnectionState>('offline')
  const [submitted, setSubmitted] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
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
  const [compacting, setCompacting] = useState(false)
  const compactingRef = useRef(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [draftNewChat, setDraftNewChat] = useState(false)
  const [draftUiMode, setDraftUiMode] = useState<UiMode>('act')
  const connectionRef = useRef<EventConnection | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const sendingRef = useRef(false)
  const ensuringChatRef = useRef(false)

  const dismissNotice = useCallback(() => {
    if (noticeTimerRef.current !== null) clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = null
    setNotice(null)
  }, [])

  const showNotice = useCallback((message: string, options?: { sticky?: boolean }) => {
    if (noticeTimerRef.current !== null) clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = null
    setNotice(message)
    if (options?.sticky === true) return
    noticeTimerRef.current = setTimeout(() => {
      noticeTimerRef.current = null
      setNotice(null)
    }, 8000)
  }, [])

  useEffect(() => () => {
    if (noticeTimerRef.current !== null) clearTimeout(noticeTimerRef.current)
  }, [])

  useEffect(() => {
    userTextsRef.current = {}
    setSubmitted({})
    setQueued([])
    setSessionStatus(null)
    setModelSelection(readJson<ModelSelection>(`hm.model.${sessionId ?? 'new'}`, {}))
  }, [sessionId])

  useEffect(() => {
    void api.providers().then(listed => {
      setProviders(listed.providers)
      const hasChatProvider = listed.providers.some(
        item => item.kind !== 'embedding' && (item.api_key_configured || item.keyless),
      )
      if (!hasChatProvider && routeRef.current.kind !== 'onboarding') navigate('#/onboarding')
    }).catch(() => {})
    void api.meta().then(setMeta).catch(() => {})
    void api.listSkills().then(listed => { setSkills(listed.skills) }).catch(() => {})
  }, [])

  const refreshProviders = useCallback(async () => {
    try {
      setProviders((await api.providers()).providers)
    } catch { /* picker keeps the stale list */ }
  }, [])

  const hydratePending = useCallback(async (sid: string) => {
    const [status, approvals, questions] = await Promise.all([
      api.sessionStatus(sid).catch(() => null),
      api.pendingApprovals(sid).catch(() => null),
      api.pendingQuestions(sid).catch(() => null),
    ])
    if (sessionIdRef.current !== sid) return
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
    setSessionsLoaded(true)
    return listed.sessions
  }, [])

  const refreshArchiveStates = useCallback(async () => {
    try {
      const statuses = await api.archiveStatuses()
      setArchiveStates(current => ({ ...statuses.sessions, ...current }))
    } catch {
      // files tier or older backends have no archive status — hide the badges.
    }
  }, [])

  const refreshMemories = useCallback(async () => {
    setMemoryLoading(true)
    try {
      setMemorySnapshot(await api.memories())
      setMemoryError(null)
      setMemoryUnavailable(false)
    } catch (error) {
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

  const handleWebEvent = useCallback((event: WebEvent) => {
    if (event.type === 'permission.grants_changed') setGrantsSignal(value => value + 1)
    if (event.type === 'context.compacted') {
      if (typeof event.payload.after_tokens === 'number') {
        setSessionStatus(current => current !== null && current.session_id === event.session_id
          ? { ...current, context_tokens: event.payload.after_tokens! }
          : current)
      }
    }
    if (event.type === 'session.mode_changed' && event.payload.ui_mode !== undefined) {
      const mode = event.payload.ui_mode
      setSessionStatus(current => current !== null && current.session_id === event.session_id
        ? { ...current, ui_mode: mode }
        : current)
    }
    if (event.type.startsWith('memory.work.')) {
      const payload = event.payload as Record<string, unknown>
      const state: ArchiveStatus['state'] =
        event.type === 'memory.work.completed' ? 'completed'
        : event.type === 'memory.work.failed' ? 'failed'
        : 'processing'
      setArchiveStates(current => ({
        ...current,
        [event.session_id]: {
          ...current[event.session_id],
          state,
          job_id: typeof payload.job_id === 'string' ? payload.job_id : current[event.session_id]?.job_id,
          extracted_count: typeof payload.extracted_count === 'number' ? payload.extracted_count : current[event.session_id]?.extracted_count,
          error: typeof payload.error === 'string' ? payload.error : null,
        },
      }))
      if (event.type === 'memory.work.completed') {
        const extracted = typeof payload.extracted_count === 'number' ? payload.extracted_count : null
        toast.show({
          title: '会话整理完成',
          description: extracted !== null && extracted > 0 ? `已提取 ${extracted} 条记忆` : '没有提取出新的记忆。',
        })
        void refreshMemories()
        void refreshArchiveStates()
      }
      if (event.type === 'memory.work.failed') {
        toast.show({
          title: '会话整理失败',
          description: typeof payload.error === 'string' ? payload.error : '归档任务执行失败。',
        })
        void refreshArchiveStates()
      }
    }
    dispatch(event)
    if (
      event.type === 'run.completed' ||
      event.type === 'run.failed' ||
      event.type === 'run.cancelled'
    ) {
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
  }, [refreshArchiveStates, refreshMemories, toast])

  const startDraft = useCallback(() => {
    connectionRef.current?.stop()
    connectionRef.current = null
    sessionIdRef.current = null
    setSessionId(null)
    setHistory([])
    setSearchOpen(false)
    setDraftNewChat(true)
    setSidebarOpen(false)
    navigate('#/chat')
  }, [])

  const selectSession = useCallback(async (nextId: string) => {
    connectionRef.current?.stop()
    setDraftNewChat(false)
    sessionIdRef.current = nextId
    setSessionId(nextId)
    setConnectionState('connecting')
    if (parseHash(window.location.hash).kind === 'chat') navigate(chatHash(nextId))
    setSessionSignals(signals => {
      if (!(nextId in signals)) return signals
      const updated = { ...signals }
      delete updated[nextId]
      return updated
    })
    try {
      const restored = await api.history(nextId)
      // A later selectSession may have superseded this one while the request
      // was in flight — never paint a stale transcript over the live session.
      if (sessionIdRef.current === nextId) setHistory(restored.messages)
    } catch (error) {
      if (sessionIdRef.current === nextId) {
        showNotice(error instanceof Error ? error.message : '会话历史加载失败。')
      }
    }
    if (sessionIdRef.current !== nextId) return
    const connection = new EventConnection(nextId, undefined, {
      onEvent: handleWebEvent,
      onStateChange: next => {
        setConnectionState(next)
        if (next === 'connected') void hydratePending(nextId)
      },
      onReject: () => {
        showNotice('该会话在服务端已不存在，已回到新会话草稿。')
        startDraft()
      },
    })
    connectionRef.current = connection
    connection.start()
  }, [handleWebEvent, hydratePending, showNotice, startDraft])

  const createSessionInflightRef = useRef<Promise<string | null> | null>(null)
  const createDraftSession = useCallback(async (): Promise<string | null> => {
    const inflight = createSessionInflightRef.current
    if (inflight) return inflight
    const task = (async () => {
      connectionRef.current?.stop()
      connectionRef.current = null
      setConnectionState('connecting')
      setSessionId(null)
      try {
        const created = await api.createSession()
        await refreshSessions()
        if (sessionIdRef.current !== null) return null
        const modelPick = localStorage.getItem('hm.model.new')
        if (modelPick !== null) {
          localStorage.setItem(`hm.model.${created.session_id}`, modelPick)
          localStorage.removeItem('hm.model.new')
        }
        try { localStorage.setItem('hm.draft.adopt', created.session_id) } catch { /* ignore */ }
        await selectSession(created.session_id)
        return created.session_id
      } catch (error) {
        showNotice(error instanceof Error ? error.message : '创建会话失败。')
        return null
      }
    })().finally(() => { createSessionInflightRef.current = null })
    createSessionInflightRef.current = task
    return task
  }, [refreshSessions, selectSession, showNotice])

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
    }).catch(error => { showNotice(error instanceof Error ? error.message : '服务不可用。') })
    return () => { connectionRef.current?.stop() }
  }, [displayOptions.requestedSessionId, refreshSessions, selectSession, showNotice])

  const ensureChatSession = useCallback(async () => {
    if (ensuringChatRef.current) return
    ensuringChatRef.current = true
    try {
      const listed = await refreshSessions()
      const first = listed[0]
      if (first !== undefined) await selectSession(first.session_id)
      else setDraftNewChat(true)
    } finally {
      ensuringChatRef.current = false
    }
  }, [refreshSessions, selectSession])

  useEffect(() => {
    if (route.kind !== 'chat') {
      setDraftNewChat(false)
      return
    }
    if (route.sessionId !== null) {
      if (route.sessionId !== sessionIdRef.current) void selectSession(route.sessionId)
      return
    }
    if (sessionIdRef.current === null && !draftNewChat) void ensureChatSession()
  }, [route, ensureChatSession, selectSession, draftNewChat])

  useEffect(() => { void refreshMemories() }, [refreshMemories])
  useEffect(() => { void refreshArchiveStates() }, [refreshArchiveStates])

  const normalizedSessionQuery = sessionQuery.trim().toLocaleLowerCase()
  const visibleSessions = useMemo(() => {
    if (normalizedSessionQuery.length === 0) return sessions
    return sessions.filter(session =>
      sessionTitle(session).toLocaleLowerCase().includes(normalizedSessionQuery),
    )
  }, [normalizedSessionQuery, sessions])

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
  const draftReady = draftNewChat && sessionId === null && active === undefined
  useFaviconStatus(active !== undefined)

  const pendingQuestion = sessionId === null
    ? null
    : Object.values(state.questions).find(question => question.sessionId === sessionId) ?? null
  const uiMode: UiMode = sessionId !== null ? state.uiModes[sessionId] ?? 'act' : draftUiMode
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

  const sendText = useCallback(async (text: string, attachments: AttachmentDraft[] = []): Promise<boolean> => {
    const trimmed = text.trim()
    if ((trimmed.length === 0 && attachments.length === 0) || sendingRef.current) return false
    if (!canSend && !draftReady) return false
    sendingRef.current = true
    try {
      let sid = sessionIdRef.current
      if (sid === null) {
        sid = await createDraftSession()
        if (sid === null) return false
        const connected = await connectionRef.current?.waitConnected() ?? false
        if (sessionIdRef.current !== sid) return false
        if (!connected) {
          showNotice('连接未建立，消息未发送，请重试。')
          return false
        }
        if (draftUiMode !== 'act') {
          try {
            await api.setUiMode(sid, draftUiMode)
            dispatch({
              type: 'session.mode_changed',
              session_id: sid,
              run_id: '',
              request_id: '',
              seq: 0,
              ts: Date.now() / 1000,
              payload: { ui_mode: draftUiMode },
            })
            setSessionStatus(current => current?.session_id === sid ? { ...current, ui_mode: draftUiMode } : current)
          } catch { /* mode is best-effort */ }
        }
      }
      const uploadedAttachments: SendMessageAttachment[] = []
      if (attachments.length > 0 && typeof api.uploadAttachment === 'function') {
        try {
          for (const item of attachments) {
            const dataBase64 = item.file !== undefined ? await fileToBase64(item.file) : ''
            const uploaded = await api.uploadAttachment(sid, {
              filename: item.name,
              media_type: item.mime || 'application/octet-stream',
              data_base64: dataBase64,
            })
            uploadedAttachments.push({
              attachment_id: uploaded.attachment_id,
              filename: uploaded.filename,
              media_type: uploaded.media_type,
            })
          }
        } catch (error) {
          showNotice(error instanceof Error ? error.message : '附件上传失败。')
          return false
        }
      }
      const requestId = crypto.randomUUID()
      const attachmentNote = attachments.length > 0
        ? `\n${attachments.map(item => `📎 ${item.name}`).join('  ')}`
        : ''
      const displayUserText = `${trimmed}${attachmentNote}`.trim()
      userTextsRef.current[requestId] = displayUserText
      setSubmitted(items => ({ ...items, [requestId]: displayUserText }))
      try {
        await api.sendMessage(sid, requestId, trimmed, {
          provider_name: modelSelection.provider_name,
          model: modelSelection.model,
          ...(uploadedAttachments.length > 0 ? { attachments: uploadedAttachments } : {}),
        })
      } catch (error) {
        delete userTextsRef.current[requestId]
        setSubmitted(items => {
          if (!(requestId in items)) return items
          const next = { ...items }
          delete next[requestId]
          return next
        })
        showNotice(error instanceof HttpError ? error.message : '消息发送失败。')
        return false
      }
      return true
    } finally {
      sendingRef.current = false
    }
  }, [canSend, draftReady, createDraftSession, draftUiMode, modelSelection, showNotice])

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
      if (sent) setQueued(list => list.filter(item => item.id !== head.id))
      if (flushInflightRef.current === head.id) flushInflightRef.current = null
    })
  }, [canSend, queued, sendText])

  const enqueueOrSend = useCallback((text: string) => {
    if (canSend || draftReady) {
      void sendText(text)
      return
    }
    if (connectionState === 'connected' && sessionId !== null) {
      enqueue(text)
      return
    }
    showNotice('当前未连接，无法发送。')
  }, [canSend, connectionState, draftReady, enqueue, sendText, sessionId, showNotice])

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
      seq: 0,
      ts: Date.now() / 1000,
      payload: { question_id: questionId },
    })
    return true
  }, [sessionId, showNotice])

  const setUiMode = useCallback(async (next: UiMode) => {
    if (sessionId === null) {
      setDraftUiMode(next)
      return
    }
    try {
      await api.setUiMode(sessionId, next)
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '模式切换失败。')
      return
    }
    dispatch({
      type: 'session.mode_changed',
      session_id: sessionId,
      run_id: '',
      request_id: '',
      seq: 0,
      ts: Date.now() / 1000,
      payload: { ui_mode: next },
    })
    setSessionStatus(current => current?.session_id === sessionId ? { ...current, ui_mode: next } : current)
  }, [sessionId, showNotice])

  const compactNow = useCallback(async () => {
    if (sessionId === null) {
      toast.show({ title: '当前为新对话草稿', description: '发送消息后再使用上下文压缩功能。' })
      return
    }
    if (compactingRef.current) return
    compactingRef.current = true
    setCompacting(true)
    showNotice('正在调用模型压缩对话上下文，对话较长时可能需要一分钟，请耐心等待…', { sticky: true })
    try {
      const result = await api.compactSession(sessionId)
      dismissNotice()
      if (typeof result.after_tokens === 'number' && result.after_tokens > 0) {
        setSessionStatus(current => current !== null && current.session_id === sessionId
          ? { ...current, context_tokens: result.after_tokens! }
          : current)
      } else if (typeof result.context_tokens === 'number' && result.context_tokens > 0) {
        setSessionStatus(current => current !== null && current.session_id === sessionId
          ? { ...current, context_tokens: result.context_tokens! }
          : current)
      }
      if (result.triggered) {
        try {
          const restored = await api.history(sessionId)
          if (sessionIdRef.current === sessionId) setHistory(restored.messages)
        } catch { /* best-effort history refresh */ }
        const kindLabel = result.kind === 'manual_summary' ? '已生成对话摘要' : `压缩方式：${result.kind}`
        const tokenDiff = typeof result.before_tokens === 'number' && typeof result.after_tokens === 'number'
          ? `${kindLabel}，上下文用量 ${result.before_tokens.toLocaleString()} → ${result.after_tokens.toLocaleString()} tokens`
          : kindLabel
        toast.show({
          title: '上下文压缩完成',
          description: tokenDiff,
          duration: 8000,
        })
      } else if (result.kind !== 'none') {
        // A stage-1 hygiene pass (micro) ran without a summary — real work,
        // not "nothing to compact". The backend only persists before/after
        // numbers for summaries, so describe the kind instead.
        const action = result.kind.endsWith('micro')
          ? '已完成轻量整理（精简工具结果与旧附件）'
          : `已完成压缩（${result.kind}）`
        toast.show({
          title: '上下文已整理',
          description: `${action}，本次未生成摘要。`,
          duration: 8000,
        })
      } else {
        toast.show({
          title: '当前没有需要压缩的内容',
          description: '对话上下文仍然精简，本次未生成摘要。',
          duration: 8000,
        })
      }
    } catch (error) {
      showNotice(error instanceof Error ? error.message : '压缩上下文失败。', { sticky: true })
    } finally {
      compactingRef.current = false
      setCompacting(false)
    }
  }, [dismissNotice, sessionId, showNotice, toast])

  const [archivingIds, setArchivingIds] = useState<ReadonlySet<string>>(new Set())

  const pollArchiveStatus = useCallback((sid: string) => {
    if (archivePollTimers.current[sid] !== undefined) return
    archivePollTimers.current[sid] = window.setInterval(async () => {
      try {
        const status = await api.archiveStatus(sid)
        setArchiveStates(current => ({ ...current, [sid]: status }))
        if (status.state === 'completed' || status.state === 'failed' || status.state === 'none') {
          window.clearInterval(archivePollTimers.current[sid])
          delete archivePollTimers.current[sid]
        }
      } catch {
        window.clearInterval(archivePollTimers.current[sid])
        delete archivePollTimers.current[sid]
      }
    }, 3000)
  }, [])

  useEffect(() => () => {
    Object.values(archivePollTimers.current).forEach(timer => window.clearInterval(timer))
  }, [])

  const archiveSession = useCallback(async (session: SessionSummary) => {
    setArchivingIds(current => new Set(current).add(session.session_id))
    try {
      const receipt = await api.archiveSession(session.session_id)
      setArchiveStates(current => ({
        ...current,
        [session.session_id]: { state: 'queued', job_id: receipt.job_id },
      }))
      pollArchiveStatus(session.session_id)
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
  }, [pollArchiveStatus, showNotice, toast])

  const persistModelSelection = useCallback((selection: ModelSelection) => {
    setModelSelection(selection)
    writeJson(`hm.model.${sessionId ?? 'new'}`, selection)
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
      const message = error instanceof Error ? error.message : '审批提交失败。'
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
      showNotice(error instanceof Error ? error.message : '审批取消失败。')
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

  const openTranscriptSearch = useCallback(() => {
    if (route.kind !== 'chat') return
    setSearchOpen(true)
    releaseFollow()
  }, [releaseFollow, route.kind])

  const currentSessionTitle = useMemo(() => {
    if (sessionId === null) return null
    const found = sessions.find(session => session.session_id === sessionId)
    return found !== undefined ? sessionTitle(found) : sessionId
  }, [sessions, sessionId])

  const toggleHelp = useCallback(() => { setHelpOpen(open => !open) }, [])
  const togglePalette = useCallback(() => { setPaletteOpen(open => !open) }, [])

  const paletteActions = useMemo<CommandPaletteAction[]>(() => {
    const go = (hash: string) => () => {
      setSidebarOpen(false)
      navigate(hash)
    }
    const runDoctor = async () => {
      showNotice('正在运行系统自检…', { sticky: true })
      try {
        const report = await api.doctor()
        const counts = { pass: 0, warn: 0, fail: 0 }
        for (const check of report.checks) {
          if (check.status === 'PASS') counts.pass += 1
          else if (check.status === 'WARN') counts.warn += 1
          else counts.fail += 1
        }
        const headline = counts.fail > 0
          ? `系统自检发现 ${counts.fail} 项失败`
          : counts.warn > 0
            ? `系统自检完成，${counts.warn} 项警告`
            : '系统自检全部通过'
        showNotice(`${headline}（${counts.pass} 通过 · ${counts.warn} 警告 · ${counts.fail} 失败），详情见「模型与设置」页`, { sticky: true })
      } catch (error) {
        showNotice(`系统自检失败：${error instanceof Error ? error.message : '无法连接后端'}`, { sticky: true })
      }
    }
    return [
      { id: 'chat.new', label: '新建会话', keywords: 'new chat session draft', run: () => { setSidebarOpen(false); startDraft() } },
      { id: 'nav.chat', label: '前往：对话', hint: '#/chat', keywords: 'chat home', run: go('#/chat') },
      { id: 'nav.memory', label: '前往：记忆管理', hint: '#/memory', keywords: 'memory', run: go('#/memory') },
      { id: 'nav.skills', label: '前往：技能', hint: '#/skills', keywords: 'skills', run: go('#/skills') },
      { id: 'nav.permissions', label: '前往：权限', hint: '#/permissions', keywords: 'permissions approval', run: go('#/permissions') },
      { id: 'nav.sessions', label: '前往：会话管理', hint: '#/sessions', keywords: 'sessions history manage', run: go('#/sessions') },
      { id: 'nav.settings', label: '前往：模型与设置', hint: '#/settings', keywords: 'settings provider model', run: go('#/settings') },
      {
        id: 'theme.toggle',
        label: theme === 'light' ? '切换到深色模式' : '切换到浅色模式',
        keywords: 'theme dark light mode',
        run: toggleTheme,
      },
      { id: 'system.doctor', label: '运行系统自检', hint: 'GET /api/doctor', keywords: 'doctor health check diagnostics', run: () => { void runDoctor() } },
    ]
  }, [navigate, showNotice, startDraft, theme, toggleTheme])

  const isChatRoute = route.kind === 'chat'
  const memoryTotal = memorySnapshot?.stats.total_count

  return (
    <div className="shell" data-recording={displayOptions.recording || undefined}>
      {sidebarOpen && <div className="sidebar-backdrop" aria-hidden="true" onClick={() => { setSidebarOpen(false) }} />}
      <aside className="sidebar" data-open={sidebarOpen || undefined}>
        <div className="brand-row">
          <div className="brand">
            <span className="brand-mark">HM</span>
            <div>
              <strong>HomeMaster</strong>
              <small>智能助手工作台</small>
            </div>
          </div>
          <button
            type="button"
            className="theme-toggle"
            aria-label={theme === 'light' ? '切换到深色模式' : '切换到浅色模式'}
            title={theme === 'light' ? '切换到深色模式' : '切换到浅色模式'}
            onClick={toggleTheme}
          >
            {theme === 'light' ? <IconMoon size={15} /> : <IconSun size={15} />}
          </button>
        </div>

        <button className="new-chat" type="button" onClick={startDraft}>
          <IconPlus size={16} /> 新建会话
        </button>

        <div className="sidebar-views" role="navigation" aria-label="主导航">
          <button type="button" aria-label="对话" aria-current={route.kind === 'chat' ? 'page' : undefined} data-active={route.kind === 'chat' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/chat') }}>
            <span className="nav-icon"><IconChat size={16} /></span>对话
          </button>
          <button type="button" aria-label="记忆管理" aria-current={route.kind === 'memory' ? 'page' : undefined} data-active={route.kind === 'memory' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/memory') }}>
            <span className="nav-icon"><IconMemory size={16} /></span>记忆管理
            {memoryTotal !== undefined && memoryTotal > 0 && <span className="nav-badge">{memoryTotal}</span>}
          </button>
          <button type="button" aria-label="记忆整理" aria-current={route.kind === 'feedback' ? 'page' : undefined} data-active={route.kind === 'feedback' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/feedback') }}>
            <span className="nav-icon"><IconRefresh size={16} /></span>记忆整理
          </button>
          <button type="button" aria-label="技能" aria-current={route.kind === 'skills' ? 'page' : undefined} data-active={route.kind === 'skills' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/skills') }}>
            <span className="nav-icon"><IconSparkles size={16} /></span>技能
            {skills.length > 0 && <span className="nav-badge">{skills.length}</span>}
          </button>
          <button type="button" aria-label="权限" aria-current={route.kind === 'permissions' ? 'page' : undefined} data-active={route.kind === 'permissions' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/permissions') }}>
            <span className="nav-icon"><IconShield size={16} /></span>权限
          </button>
          <button type="button" aria-label="Provider" aria-current={route.kind === 'settings' ? 'page' : undefined} data-active={route.kind === 'settings' || undefined} onClick={() => { setSidebarOpen(false); navigate('#/settings') }}>
            <span className="nav-icon"><IconSettings size={16} /></span>模型与设置
          </button>
        </div>

        <div className="history-heading">
          <span>历史会话</span>
          <div className="history-heading-actions">
            <button
              type="button"
              aria-label="会话管理"
              title="打开会话批量管理页"
              onClick={() => { setSidebarOpen(false); navigate('#/sessions') }}
            >
              管理
            </button>
            <button
              type="button"
              aria-label={historyCollapsed ? '展开历史会话' : '折叠历史会话'}
              title={historyCollapsed ? '展开历史会话' : '折叠历史会话'}
              onClick={toggleHistory}
            >
              {historyCollapsed ? '＋' : '−'}
            </button>
          </div>
        </div>
        {!historyCollapsed && <>
          <label className="session-search">
            <IconSearch size={14} />
            <input
              type="search"
              value={sessionQuery}
              onChange={event => { setSessionQuery(event.target.value) }}
              placeholder="搜索会话…"
              aria-label="搜索会话"
            />
            <button
              type="button"
              className="session-search-palette"
              title={`命令面板（${formatShortcut(SHORTCUTS.commandPalette)}）`}
              aria-label={`命令面板（${formatShortcut(SHORTCUTS.commandPalette)}）`}
              onClick={() => { setPaletteOpen(true) }}
            >
              {formatShortcut(SHORTCUTS.commandPalette)}
            </button>
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
                  <span
                    className="session-dot"
                    data-status={signal ?? 'idle'}
                    title={signal === 'running' ? '运行中' : signal === 'awaiting' ? '等待你的操作' : signal === 'done' ? '已完成' : undefined}
                    aria-hidden="true"
                  />
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
                      aria-current={session.session_id === sessionId ? 'true' : undefined}
                      onClick={() => { setSidebarOpen(false); navigate(chatHash(session.session_id)) }}
                    >
                      <span>{title}</span>
                      <small>{itemMeta.length > 0 ? itemMeta : session.session_id.slice(0, 12)}</small>
                      {archiveBadgeText(archiveStates[session.session_id]) !== null && (
                        <small data-archive-state={archiveStates[session.session_id]?.state}>{archiveBadgeText(archiveStates[session.session_id])}</small>
                      )}
                    </button>
                  )}
                  <span className="session-actions">
                    {meta !== null && meta.memory_mode === 'full' && (
                      <button
                        type="button"
                        aria-label={`归档会话 ${title}`}
                        title="提炼并归档本会话的长期记忆"
                        disabled={archivingIds.has(session.session_id)}
                        onClick={() => { void archiveSession(session) }}
                      >{archivingIds.has(session.session_id) ? '…' : <IconArchive size={13} />}</button>
                    )}
                    <button type="button" aria-label={`重命名会话 ${title}`} title="重命名会话" onClick={() => { startRename(session) }}><IconEdit size={13} /></button>
                    <button
                      type="button"
                      data-danger
                      aria-label={`删除会话 ${title}`}
                      title="彻底删除该会话（不可撤销）"
                      onClick={() => { void removeSession(session) }}
                    ><IconTrash size={13} /></button>
                  </span>
                </div>
              )
            })}
            {visibleSessions.length === 0 && (
              <div className="session-empty">
                {!sessionsLoaded
                  ? '加载中…'
                  : normalizedSessionQuery.length > 0
                    ? '没有匹配的会话'
                    : '还没有会话——点上方「新建会话」开始'}
              </div>
            )}
          </nav>
        </>}
        {historyCollapsed && <div className="history-spacer" />}
        <div className="sidebar-footer">
          <div className="local-note">
            <span>●</span> 本地运行{meta !== null ? ` · v${meta.version}` : ''}
          </div>
          <button type="button" className="topbar-action" title={`键盘快捷键（${formatShortcut(SHORTCUTS.shortcutHelp)}）`} aria-label="键盘快捷键" onClick={toggleHelp}>?</button>
        </div>
      </aside>
      <main className="workspace" data-subpage={!isChatRoute || undefined}>
        {isChatRoute && (
          <header className="topbar">
            <button className="mobile-menu" type="button" aria-label="打开侧栏" onClick={() => { setSidebarOpen(value => !value) }}>☰</button>
            <div>
              <strong>{draftNewChat ? '新对话' : (currentSessionTitle ?? '对话')}</strong>
              <small>{draftNewChat ? '新会话（发送首条消息后创建）' : '与智能助手多轮交互、调用工具与技能'}</small>
            </div>
            <div className="topbar-side">
              <button
                type="button"
                className="topbar-action"
                title={`搜索对话内容（${formatShortcut(SHORTCUTS.transcriptSearch)}）`}
                aria-label="搜索对话内容"
                onClick={openTranscriptSearch}
              >
                <IconSearch size={14} /> 搜索
              </button>
              {!draftNewChat && (
                <div className="connection" data-state={connectionState}><span />{CONNECTION_LABEL[connectionState]}</div>
              )}
            </div>
          </header>
        )}
        {notice !== null && (
          <div className="notice" role="alert"><span>{notice}</span><button type="button" onClick={dismissNotice}>关闭</button></div>
        )}
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
            loadGraph={() => api.memoryGraph()}
            compileMemory={memoryId => api.compileMemory(memoryId)}
            compileStatus={jobId => api.compileStatus(jobId)}
            createMemory={typeof api.createMemory === 'function' ? async payload => {
              await api.createMemory(payload)
              toast.show({ title: '已新增长期记忆' })
            } : undefined}
            updateMemory={typeof api.updateMemory === 'function' ? async (id, content) => {
              const res = await api.updateMemory(id, { content })
              toast.show({ title: '记忆内容已更新' })
              return res.memory
            } : undefined}
            deleteMemory={typeof api.deleteMemory === 'function' ? async id => {
              await api.deleteMemory(id)
              toast.show({ title: '已删除（归档）该记忆' })
            } : undefined}
          />
        ) : route.kind === 'feedback' ? (
          <FeedbackRunsPage
            loadRuns={() => api.feedbackRuns()}
            loadDetail={jobId => api.feedbackRunDetail(jobId)}
            sessions={sessions}
            unavailable={memoryUnavailable}
            runImplicitFeedback={typeof api.runImplicitFeedback === 'function'
              ? () => api.runImplicitFeedback() : undefined}
          />
        ) : route.kind === 'skills' ? (
          <SkillsPage
            api={api}
            onUseSkill={commandName => {
              navigate('#/chat')
              editMessage(`/${commandName} `)
            }}
          />
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
            archiveStates={archiveStates}
            archiveBadgeText={archiveBadgeText}
            renamingId={renamingId}
            renameDraft={renameDraft}
            onRenameDraft={setRenameDraft}
            onStartRename={startRename}
            onCommitRename={commitRename}
            onCancelRename={() => { setRenamingId(null) }}
            onArchive={session => { void archiveSession(session) }}
            onDelete={session => { void removeSession(session) }}
            archivingIds={archivingIds}
            canArchive={meta !== null && meta.memory_mode === 'full'}
          />
        ) : <>
          <section className="conversation" aria-live="polite" ref={follow.containerRef}>
            <div className="conversation-inner" ref={follow.contentRef}>
            {renderHistoryItems(history, displayOptions.recording, editMessage, enqueueOrSend)}
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
                providers={providers}
                meta={meta}
                memoryCount={memoryTotal}
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
          <QueueDock
            items={queued}
            onRemove={id => { setQueued(list => list.filter(item => item.id !== id)) }}
            onClear={() => { if (window.confirm('清空全部排队消息？')) setQueued([]) }}
          />
          <ComposerPanel
            sessionId={sessionId}
            busy={active !== undefined}
            disabled={!draftNewChat && (connectionState !== 'connected' || sessionId === null)}
            onSubmit={(text, attachments) => sendText(text, attachments)}
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
            loadModels={loadProviderModels}
            usage={latestUsage}
            sessionStatus={sessionStatus}
            compacting={compacting}
            onCompact={sessionId !== null ? () => { void compactNow() } : undefined}
            pendingQuestion={pendingQuestion}
            onAnswerQuestion={answerQuestion}
            pendingApproval={approvalTurn?.approval ?? null}
          />
        </>}
      </main>
      {approvalTurn?.approval && <ApprovalDialog approval={approvalTurn.approval} busy={approvalBusy} error={approvalError} onSubmit={(decisions, submissionId) => { void submitApproval(decisions, submissionId) }} onClose={(submissionId) => { void cancelApproval(submissionId) }} />}
      <GlobalShortcuts
        onTranscriptSearch={route.kind === 'chat' ? openTranscriptSearch : undefined}
        onToggleHelp={toggleHelp}
        onCommandPalette={togglePalette}
      />
      {helpOpen && <ShortcutHelpDialog onClose={() => { setHelpOpen(false) }} />}
      {paletteOpen && (
        <CommandPalette
          actions={paletteActions}
          sessions={sessions}
          onSelectSession={(id) => { navigate(chatHash(id)) }}
          onClose={() => { setPaletteOpen(false) }}
        />
      )}
    </div>
  )
}

function CopyablePre({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false)
  const preRef = useRef<HTMLPreElement>(null)

  let lang = 'code'
  if (children && typeof children === 'object' && 'props' in children) {
    const cls = (children as { props?: { className?: string } }).props?.className ?? ''
    const match = /language-([\w-]+)/.exec(cls)
    if (match?.[1]) lang = match[1]
  }

  const copy = () => {
    const text = preRef.current?.textContent ?? ''
    if (!text) return
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true)
      window.setTimeout(() => { setCopied(false) }, 1800)
    }).catch(() => {})
  }

  return (
    <div className="code-block-wrap">
      <div className="code-block-header">
        <span>{lang}</span>
        <button type="button" className="code-copy-btn" onClick={copy} aria-label="复制代码">
          {copied ? <><IconCheck size={12} /> 已复制</> : <><IconCopy size={12} /> 复制代码</>}
        </button>
      </div>
      <pre ref={preRef}>{children}</pre>
    </div>
  )
}

function MarkdownView({ text, searchKey }: { text: string; searchKey?: string }) {
  return (
    <div className="markdown" data-search-key={searchKey}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: ({ children }) => <CopyablePre>{children}</CopyablePre>,
        }}
      >
        {text}
      </ReactMarkdown>
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
              return <MarkdownView key={key} searchKey={key} text={step.text} />
            case 'error':
              return <div className={step.status === 'failed' ? 'run-error' : 'run-cancelled'} data-search-key={key} key={key}>{step.message}</div>
          }
        })}
      </div>
    </article>
  )
}

function historyToolRecord(message: HistoryMessage, fallbackId: string): ToolRecord {
  const name = message.name ?? 'tool'
  const raw = (message.text ?? '').trim()
  let args: Record<string, unknown> = {}
  let output = raw
  let status: 'completed' | 'failed' = 'completed'

  if (raw.startsWith('{')) {
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        for (const key of ['command', 'path', 'file_path', 'query', 'pattern', 'base_dir', 'target']) {
          if (parsed[key] !== undefined && parsed[key] !== null) {
            args[key] = parsed[key]
          }
        }
        if (typeof parsed['text'] === 'string') output = parsed['text']
        else if (typeof parsed['content'] === 'string') output = parsed['content']
        else output = JSON.stringify(parsed, null, 2)
        if (
          (typeof parsed['error_code'] === 'string' && parsed['error_code'].length > 0) ||
          (typeof parsed['returncode'] === 'number' && parsed['returncode'] !== 0) ||
          parsed['status'] === 'failure' ||
          parsed['status'] === 'error'
        ) {
          status = 'failed'
        }
      }
    } catch {
      // Keep raw output when not valid JSON.
    }
  }

  const toolCallId = message.tool_call_id ?? fallbackId
  const summary = toolArgSummary(name, args)
  const firstLine = output.split('\n').find(line => line.trim().length > 0) ?? ''
  return {
    toolCallId,
    name,
    args,
    argSummary: summary,
    status,
    outputPreview: firstLine.slice(0, 160),
    error: status === 'failed' ? output : null,
    durationMs: null,
    tool: {
      toolCallId,
      name,
      arguments: args,
      status,
      output,
      artifacts: [],
    },
  }
}

function renderHistoryItems(
  messages: HistoryMessage[],
  defaultExpanded: boolean,
  onEdit: (text: string) => void,
  onResend: (text: string) => void,
): ReactNode[] {
  const nodes: ReactNode[] = []
  let index = 0
  while (index < messages.length) {
    const message = messages[index]!
    if (message.role === 'tool') {
      const start = index
      const records: ToolRecord[] = []
      while (index < messages.length && messages[index]!.role === 'tool') {
        const toolMsg = messages[index]!
        records.push(historyToolRecord(toolMsg, `h${index}`))
        index += 1
      }
      nodes.push(
        <div className="assistant-row" key={`h-tools:${start}`} data-search-key={`h${start}`}>
          <ToolGroup tools={records} defaultOpen={defaultExpanded} />
        </div>,
      )
      continue
    }
    nodes.push(
      <HistoryRow
        key={`${index}:${message.role}`}
        message={message}
        searchKey={`h${index}`}
        defaultExpanded={defaultExpanded}
        onEdit={onEdit}
        onResend={onResend}
      />,
    )
    index += 1
  }
  return nodes
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
  if (message.role === 'tool') {
    const record = historyToolRecord(message, searchKey ?? 'hist-tool')
    return (
      <div className="assistant-row" data-search-key={searchKey}>
        <ToolGroup tools={[record]} defaultOpen={defaultExpanded} />
      </div>
    )
  }
  if (!message.thinking && !message.text) return null
  return (
    <div className="assistant-row">
      {message.thinking && <ReasoningRow text={message.thinking} running={false} defaultExpanded={defaultExpanded} searchKey={searchKey} />}
      {message.text && <MarkdownView searchKey={searchKey} text={message.text} />}
    </div>
  )
}
