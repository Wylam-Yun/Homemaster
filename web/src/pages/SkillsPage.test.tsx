import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeMasterApi } from '../api/http'
import { SkillsPage } from './SkillsPage'

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
    { name: 'cleanup', command_name: 'cleanup', description: '整理会话上下文并压缩历史', source: 'bundled', user_invocable: true, enabled: true },
    { name: 'audit-log', command_name: 'audit', description: '导出审计日志', source: 'user', user_invocable: true, enabled: false },
    { name: 'internal-probe', command_name: null, description: '内部探测，不可直接调用', source: 'project', user_invocable: false, enabled: true },
  ],
}

describe('SkillsPage', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('renders the read-only skill list with source badges and command chips', async () => {
    vi.stubGlobal('fetch', fetchRouter({ 'GET /api/skills': { body: SKILLS } }))
    render(<SkillsPage api={new HomeMasterApi()} />)

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
    render(<SkillsPage api={new HomeMasterApi()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('registry down')
    expect(screen.getByRole('heading', { name: '技能' })).toBeInTheDocument()

    fetchMock.mockImplementation((input: RequestInfo | URL) => Promise.resolve(
      new Response(JSON.stringify(SKILLS), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    ))
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }))
    expect(await screen.findByText('cleanup')).toBeInTheDocument()
  })
})
