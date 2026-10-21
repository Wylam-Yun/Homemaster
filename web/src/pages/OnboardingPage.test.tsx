import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeMasterApi } from '../api/http'
import { ToastProvider } from '../components/toast'
import { OnboardingPage } from './OnboardingPage'

type Route = { status?: number; body: unknown } | ((init: RequestInit | undefined) => { status?: number; body: unknown })

function fetchRouter(routes: Record<string, Route>) {
  const calls: Array<{ key: string; body: Record<string, unknown> | null }> = []
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const path = url.startsWith('http') ? new URL(url).pathname + new URL(url).search : url
    const key = `${init?.method ?? 'GET'} ${path}`
    calls.push({ key, body: init?.body !== undefined ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null })
    const route = routes[key]
    const resolved = route === undefined
      ? { status: 404, body: { code: 'not_found', message: `no mock for ${key}` } }
      : typeof route === 'function' ? route(init) : route
    return Promise.resolve(new Response(JSON.stringify(resolved.body), {
      status: resolved.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    }))
  })
  return { fetchMock, calls }
}

const TEMPLATES = {
  templates: [
    { key: 'anthropic', title: 'Anthropic (Claude)', api_format: 'anthropic', base_url: 'https://api.anthropic.com', model: 'claude-sonnet-4-5', transport: 'anthropic_sdk', supports_auth_token: true, keyless: false },
    { key: 'deepseek', title: 'DeepSeek', api_format: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-chat', transport: 'openai_sdk', supports_auth_token: false, keyless: false },
    { key: 'ollama', title: 'Ollama (local, keyless)', api_format: 'ollama', base_url: 'http://localhost:11434', model: 'llama3.3', transport: 'openai_sdk', supports_auth_token: false, keyless: true },
  ],
}

function renderPage(routes: Record<string, Route>) {
  const { fetchMock, calls } = fetchRouter(routes)
  vi.stubGlobal('fetch', fetchMock)
  render(
    <ToastProvider>
      <OnboardingPage api={new HomeMasterApi()} />
    </ToastProvider>,
  )
  return calls
}

describe('OnboardingPage', () => {
  afterEach(() => {
    window.history.replaceState({}, '', '/')
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders provider template cards and prefills fields on pick', async () => {
    renderPage({ 'GET /api/provider-templates': { body: TEMPLATES } })

    fireEvent.click(await screen.findByRole('button', { name: /DeepSeek/ }))

    expect((screen.getByRole('textbox', { name: 'Base URL' }) as HTMLInputElement).value).toBe('https://api.deepseek.com')
    expect((screen.getByRole('textbox', { name: 'Model' }) as HTMLInputElement).value).toBe('deepseek-chat')
    expect((screen.getByRole('textbox', { name: 'Provider 名称' }) as HTMLInputElement).value).toBe('deepseek')
  })

  it('hides the API key field for keyless templates', async () => {
    renderPage({ 'GET /api/provider-templates': { body: TEMPLATES } })

    fireEvent.click(await screen.findByRole('button', { name: /Ollama/ }))
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '下一步' }))
    expect(await screen.findByRole('button', { name: '测试连接' })).toBeInTheDocument()
  })

  it('gates save behind a passing probe and never persists on detection alone', async () => {
    const calls = renderPage({
      'GET /api/provider-templates': { body: TEMPLATES },
      'POST /api/providers/test': { body: { ok: true, error_kind: null, message: 'provider returned parseable JSON', latency_ms: 88, name: 'deepseek', model: 'deepseek-chat' } },
      'POST /api/providers': { body: { provider: {}, status: 'created', default: 'deepseek' } },
    })

    // Step 1 → 2: picking a template only prefills; nothing is written.
    fireEvent.click(await screen.findByRole('button', { name: /DeepSeek/ }))
    expect(calls.filter(call => call.key === 'POST /api/providers')).toHaveLength(0)

    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-test' } })
    fireEvent.click(screen.getByRole('button', { name: '下一步' }))

    // Step 3: probe fires with the UNSAVED payload — still no write.
    fireEvent.click(await screen.findByRole('button', { name: '测试连接' }))
    await waitFor(() => { expect(screen.getByText('连接测试通过')).toBeInTheDocument() })
    const probes = calls.filter(call => call.key === 'POST /api/providers/test')
    expect(probes).toHaveLength(1)
    expect(probes[0]!.body).toMatchObject({
      name: 'deepseek',
      template: 'deepseek',
      base_url: 'https://api.deepseek.com',
      model: 'deepseek-chat',
      api_key: 'sk-test',
    })
    expect(calls.filter(call => call.key === 'POST /api/providers')).toHaveLength(0)

    // Only 保存并进入 persists.
    fireEvent.click(screen.getByRole('button', { name: '保存并进入' }))
    await waitFor(() => {
      expect(calls.filter(call => call.key === 'POST /api/providers')).toHaveLength(1)
    })
    expect(window.location.hash).toBe('#/chat')
  })

  it('keeps the user on the test step with error_kind advice on failure', async () => {
    const calls = renderPage({
      'GET /api/provider-templates': { body: TEMPLATES },
      'POST /api/providers/test': { body: { ok: false, error_kind: 'model', message: 'model not found', latency_ms: 30, name: 'deepseek', model: 'deepseek-chat' } },
      'POST /api/providers': { body: { provider: {}, status: 'created' } },
    })

    fireEvent.click(await screen.findByRole('button', { name: /DeepSeek/ }))
    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-test' } })
    fireEvent.click(screen.getByRole('button', { name: '下一步' }))
    fireEvent.click(await screen.findByRole('button', { name: '测试连接' }))

    expect(await screen.findByText('连接测试失败')).toBeInTheDocument()
    expect(screen.getByText('model')).toBeInTheDocument()
    expect(screen.getByText(/无此模型名/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存并进入' })).not.toBeInTheDocument()
    expect(calls.filter(call => call.key === 'POST /api/providers')).toHaveLength(0)

    // 返回修改 goes back to fields without losing the draft.
    fireEvent.click(screen.getByRole('button', { name: '返回修改' }))
    expect((screen.getByRole('textbox', { name: 'Model' }) as HTMLInputElement).value).toBe('deepseek-chat')
  })

  it('offers a skip-to-chat escape', async () => {
    renderPage({ 'GET /api/provider-templates': { body: TEMPLATES } })
    expect((await screen.findByRole('link', { name: /跳过/ })).getAttribute('href')).toBe('#/chat')
  })
})
