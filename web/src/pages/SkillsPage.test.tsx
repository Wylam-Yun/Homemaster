import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeMasterApi } from '../api/http'
import { ToastProvider } from '../components/toast'
import { SkillsPage, type SkillsApi } from './SkillsPage'

function fetchRouter(routes: Record<string, { status?: number; body: unknown }>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const key = `${init?.method ?? 'GET'} ${url}`
    const route = routes[key]
    const resolved = route ?? { status: 404, body: { code: 'not_found', message: `no mock for ${key}` } }
    return Promise.resolve(new Response(JSON.stringify(resolved.body), {
      status: resolved.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    }))
  })
  return fetchMock
}

const SKILLS = {
  skills: [
    { name: 'cleanup', command_name: 'cleanup', description: '整理会话上下文并压缩历史', source: 'bundled', user_invocable: true, enabled: true, deletable: false },
    { name: 'audit-log', command_name: 'audit', description: '导出审计日志', source: 'user', user_invocable: true, enabled: false, deletable: true },
    { name: 'internal-probe', command_name: null, description: '内部探测，不可直接调用', source: 'project', user_invocable: false, enabled: true, deletable: false },
  ],
}

function renderPage(api: SkillsApi = new HomeMasterApi()) {
  render(
    <ToastProvider>
      <SkillsPage api={api} />
    </ToastProvider>,
  )
}

describe('SkillsPage', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('renders the skill list with source badges and command chips', async () => {
    vi.stubGlobal('fetch', fetchRouter({ 'GET /api/skills': { body: SKILLS } }))
    renderPage()

    expect(await screen.findByText('cleanup')).toBeInTheDocument()
    expect(screen.getByText('整理会话上下文并压缩历史')).toBeInTheDocument()
    expect(screen.getByText('/cleanup')).toBeInTheDocument()
    expect(screen.getByText('bundled')).toBeInTheDocument()
    expect(screen.getByText('audit-log')).toBeInTheDocument()
    expect(screen.getByText('已停用')).toBeInTheDocument()
    expect(screen.getByText('internal-probe')).toBeInTheDocument()
    expect(screen.getByText('不可直接调用')).toBeInTheDocument()
  })

  it('shows a retryable error card when the endpoint fails', async () => {
    const fetchMock = fetchRouter({
      'GET /api/skills': { status: 503, body: { code: 'skill_registry_unavailable', message: 'registry down' } },
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('registry down')
    expect(screen.getByRole('heading', { name: '技能' })).toBeInTheDocument()

    fetchMock.mockImplementation((input: RequestInfo | URL) => Promise.resolve(
      new Response(JSON.stringify(SKILLS), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    ))
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }))
    expect(await screen.findByText('cleanup')).toBeInTheDocument()
  })

  it('shows the delete action only for user-source skills and confirms inline', async () => {
    const fetchMock = fetchRouter({
      'GET /api/skills': { body: SKILLS },
      'DELETE /api/skills/audit-log': { status: 204, body: '' },
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()

    await screen.findByText('audit-log')
    const deleteButtons = screen.getAllByRole('button', { name: '删除' })
    expect(deleteButtons).toHaveLength(1)

    fireEvent.click(deleteButtons[0]!)
    expect(screen.getByText(/确认删除「audit-log」/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input, init]) =>
        String(input).endsWith('/api/skills/audit-log') && init?.method === 'DELETE',
      )).toBe(true)
    })
  })

  it('collects a picked folder and posts it to the import endpoint', async () => {
    const imported = { status: 'imported', skill: { name: 'housekeeping', command_name: 'housekeeping', description: 'x', source: 'user', user_invocable: true, enabled: true, deletable: true } }
    const fetchMock = fetchRouter({
      'GET /api/skills': { body: SKILLS },
      'POST /api/skills/import': { body: imported },
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()

    await screen.findByText('audit-log')
    const file = new File(['---\nname: housekeeping\n---\nbody'], 'SKILL.md')
    Object.defineProperty(file, 'webkitRelativePath', { value: 'housekeeping/SKILL.md' })
    const input = document.querySelector('input[type="file"]')!
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input_, init]) =>
        String(input_).endsWith('/api/skills/import') && init?.method === 'POST',
      )
      expect(call).toBeDefined()
      const payload = JSON.parse(String(call![1]?.body)) as { files: Array<{ path: string }>; overwrite: boolean }
      expect(payload.files[0]!.path).toBe('housekeeping/SKILL.md')
      expect(payload.overwrite).toBe(false)
    })
  })
})
