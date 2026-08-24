import { FormEvent, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { EventConnection, type ConnectionState } from './api/connection'
import { HomeMasterApi, HttpError, type HistoryMessage, type MemorySnapshot, type SessionSummary } from './api/http'
import { ApprovalDialog } from './components/ApprovalDialog'
import { MemoryPage } from './components/MemoryPage'
import { ReasoningRow } from './components/ReasoningRow'
import { ToolCallCard } from './components/ToolCallCard'
import { initialConversationState, reduceWebEvent } from './state/conversation'

const api = new HomeMasterApi()

export function App() {
  const [view, setView] = useState<'conversation' | 'memories'>('conversation')
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryMessage[]>([])
  const [state, dispatch] = useReducer(reduceWebEvent, initialConversationState)
  const [connectionState, setConnectionState] = useState<ConnectionState>('offline')
  const [draft, setDraft] = useState('')
  const [submitted, setSubmitted] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [approvalBusy, setApprovalBusy] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [historyCollapsed, setHistoryCollapsed] = useState(
    () => localStorage.getItem('homemaster:web:history-collapsed') === 'true',
  )
  const [memorySnapshot, setMemorySnapshot] = useState<MemorySnapshot | null>(null)
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [memoryError, setMemoryError] = useState<string | null>(null)
  const connectionRef = useRef<EventConnection | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

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
    } catch {
      setMemoryError('记忆服务暂不可用，请稍后重试。')
    } finally {
      setMemoryLoading(false)
    }
  }, [])

  const selectSession = useCallback(async (nextId: string) => {
    setView('conversation')
    connectionRef.current?.stop()
    setSessionId(nextId)
    setConnectionState('connecting')
    try {
      const restored = await api.history(nextId)
      setHistory(restored.messages)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Could not load session history.')
    }
    const connection = new EventConnection(nextId, undefined, {
      onEvent: event => { dispatch(event) },
      onStateChange: setConnectionState,
    })
    connectionRef.current = connection
    connection.start()
  }, [])

  const newSession = useCallback(async () => {
    setView('conversation')
    connectionRef.current?.stop()
    connectionRef.current = null
    setConnectionState('connecting')
    setSessionId(null)
    try {
      const created = await api.createSession()
      await refreshSessions()
      await selectSession(created.session_id)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Could not create a session.')
    }
  }, [refreshSessions, selectSession])

  useEffect(() => {
    void refreshSessions().then(existing => {
      if (existing[0] !== undefined) void selectSession(existing[0].session_id)
      else void newSession()
    }).catch(error => { setNotice(error instanceof Error ? error.message : 'Service unavailable.') })
    return () => { connectionRef.current?.stop() }
  }, [newSession, refreshSessions, selectSession])

  useEffect(() => { void refreshMemories() }, [refreshMemories])

  const turns = useMemo(() => Object.values(state.turns).filter(turn => turn.sessionId === sessionId), [sessionId, state.turns])
  const active = turns.find(turn => turn.status === 'pending' || turn.status === 'running')
  const approvalTurn = turns.find(turn => turn.approval !== null)
  const canSend = connectionState === 'connected' && sessionId !== null && active === undefined

  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [turns, history])

  const send = async (event: FormEvent): Promise<void> => {
    event.preventDefault()
    const text = draft.trim()
    if (!canSend || sessionId === null || text.length === 0) return
    const requestId = crypto.randomUUID()
    setDraft('')
    setSubmitted(items => ({ ...items, [requestId]: text }))
    try {
      await api.sendMessage(sessionId, requestId, text)
    } catch (error) {
      setNotice(error instanceof HttpError ? error.message : 'Message could not be sent.')
    }
  }

  const resolveApproval = async (outcome: 'approve' | 'reject'): Promise<void> => {
    const approval = approvalTurn?.approval
    if (approval === null || approval === undefined) return
    setApprovalBusy(true)
    try { await api.resolveApproval(approval.approvalId, outcome) }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Approval failed.') }
    finally { setApprovalBusy(false) }
  }

  const toggleHistory = () => {
    setHistoryCollapsed(value => {
      const next = !value
      localStorage.setItem('homemaster:web:history-collapsed', String(next))
      return next
    })
  }

  return (
    <div className="shell">
      <aside className="sidebar" data-open={sidebarOpen || undefined}>
        <div className="brand"><span className="brand-mark">HM</span><div><strong>HomeMaster</strong><small>Local agent console</small></div></div>
        <div className="sidebar-views" aria-label="主导航">
          <button type="button" aria-label="对话" data-active={view === 'conversation' || undefined} onClick={() => { setView('conversation'); setSidebarOpen(false) }}><span>◉</span>对话</button>
          <button type="button" aria-label="记忆管理" data-active={view === 'memories' || undefined} onClick={() => { setView('memories'); setSidebarOpen(false) }}><span>◇</span>记忆管理</button>
        </div>
        <button className="new-chat" type="button" onClick={() => { setSidebarOpen(false); void newSession() }}>＋ 新建会话</button>
        <div className="history-heading"><span>历史会话</span><button type="button" aria-label={historyCollapsed ? '展开历史会话' : '折叠历史会话'} onClick={toggleHistory}>{historyCollapsed ? '＋' : '−'}</button></div>
        {!historyCollapsed && <nav aria-label="历史会话">
          {sessions.map(session => <button type="button" aria-label={`打开会话 ${session.session_id}`} key={session.session_id} data-active={session.session_id === sessionId || undefined} onClick={() => { setSidebarOpen(false); void selectSession(session.session_id) }}><span>会话</span><small>{session.session_id.slice(0, 12)}</small></button>)}
        </nav>}
        {historyCollapsed && <div className="history-spacer" />}
        <div className="local-note"><span>●</span> Loopback only</div>
      </aside>
      <main className="workspace">
        <header className="topbar"><button className="mobile-menu" type="button" aria-label="打开侧栏" onClick={() => { setSidebarOpen(value => !value) }}>☰</button><div><strong>{view === 'memories' ? '记忆管理' : '对话'}</strong><small>{view === 'memories' ? '只读查看' : (sessionId ?? '正在启动…')}</small></div><div className="connection" data-state={connectionState}><span />{connectionState}</div></header>
        {view === 'memories' ? (
          <MemoryPage
            snapshot={memorySnapshot}
            loading={memoryLoading}
            error={memoryError}
            onRefresh={refreshMemories}
            loadHistory={memoryId => api.memoryHistory(memoryId)}
          />
        ) : <>
          <section className="conversation" aria-live="polite">
          {history.map((message, index) => <HistoryRow key={`${index}:${message.role}`} message={message} />)}
          {turns.map(turn => <article className="turn" key={turn.requestId}>
            {submitted[turn.requestId] && <div className="user-row"><div>{submitted[turn.requestId]}</div></div>}
            <div className="assistant-row">
              <ReasoningRow text={turn.thinking} running={turn.status === 'running'} />
              {Object.values(turn.tools).map(tool => <ToolCallCard key={tool.toolCallId} tool={tool} sessionId={turn.sessionId} />)}
              {turn.answer && <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.answer}</ReactMarkdown></div>}
              {turn.status === 'failed' && <div className="run-error">{turn.error?.message ?? 'Run failed.'}</div>}
              {turn.status === 'cancelled' && <div className="run-cancelled">Run cancelled. Partial output was kept.</div>}
            </div>
          </article>)}
          {history.length === 0 && turns.length === 0 && <div className="empty"><span>✦</span><h1>What should we work on?</h1><p>HomeMaster can reason, use local tools, and ask before dangerous operations.</p></div>}
          <div ref={endRef} />
          </section>
          {notice && <div className="notice" role="alert"><span>{notice}</span><button type="button" onClick={() => { setNotice(null) }}>关闭</button></div>}
          <form className="composer" onSubmit={event => { void send(event) }}>
          <textarea value={draft} onChange={event => { setDraft(event.target.value) }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder={connectionState === 'connected' ? 'Message HomeMaster…' : 'Waiting for connection…'} disabled={connectionState !== 'connected'} rows={1} />
          {active !== undefined ? <button className="stop" type="button" onClick={() => { if (sessionId) void api.cancel(sessionId) }} aria-label="Stop run">■</button> : <button className="send" type="submit" disabled={!canSend || draft.trim().length === 0} aria-label="Send message">↑</button>}
          <small>Enter to send · Shift+Enter for a new line</small>
          </form>
        </>}
      </main>
      {approvalTurn?.approval && <ApprovalDialog approval={approvalTurn.approval} busy={approvalBusy} onApprove={() => { void resolveApproval('approve') }} onReject={() => { void resolveApproval('reject') }} />}
    </div>
  )
}

function HistoryRow({ message }: { message: HistoryMessage }) {
  if (message.role === 'user') return <div className="user-row"><div>{message.text}</div></div>
  return <div className="assistant-row">{message.thinking && <ReasoningRow text={message.thinking} running={false} />}{message.text && <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown></div>}</div>
}
