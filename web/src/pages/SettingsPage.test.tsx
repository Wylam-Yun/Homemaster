import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeMasterApi } from '../api/http'
import { ToastProvider } from '../components/toast'
import { SettingsPage } from './SettingsPage'

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

const PROVIDERS = {
  providers: [
    { name: 'primary', kind: 'chat', model: 'claude-sonnet-4', base_url: 'https://api.anthropic.com', api_format: 'anthropic', auth_type: 'api_key', api_key_configured: true, api_key_count: 1 },
    { name: 'embedder', kind: 'embedding', model: 'bge-m3', base_url: 'http://localhost:8080', api_format: 'openai', auth_type: 'api_key', api_key_configured: false, api_key_count: 0 },
  ],
}

const TEMPLATES = {
  templates: [
    { key: 'anthropic', title: 'Anthropic (Claude)', api_format: 'anthropic', base_url: 'https://api.anthropic.com', model: 'claude-sonnet-4-5', transport: 'anthropic_sdk', supports_auth_token: true, keyless: false },
    { key: 'ollama', title: 'Ollama (local, keyless)', api_format: 'ollama', base_url: 'http://localhost:11434', model: 'llama3.3', transport: 'openai_sdk', supports_auth_token: false, keyless: true },
  ],
}

function renderPage(routes: Record<string, Route>) {
  const { fetchMock, calls } = fetchRouter(routes)
  vi.stubGlobal('fetch', fetchMock)
  render(
    <ToastProvider>
      <SettingsPage api={new HomeMasterApi()} />
    </ToastProvider>,
  )
  return calls
}

describe('SettingsPage', () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('lists providers with kind/model/base_url and key state, never key material', async () => {
    renderPage({
      'GET /api/providers': { body: PROVIDERS },
      'GET /api/provider-templates': { body: TEMPLATES },
    })

    expect(await screen.findByText('primary')).toBeInTheDocument()
    expect(screen.getByText('claude-sonnet-4')).toBeInTheDocument()
    expect(screen.getByText('https://api.anthropic.com')).toBeInTheDocument()
    expect(screen.getByText('已配置 Key ×1')).toBeInTheDocument()
    expect(screen.getByText('embedder')).toBeInTheDocument()
    expect(screen.getByText('embedding')).toBeInTheDocument()
    expect(screen.getByText('未配置 Key')).toBeInTheDocument()
  })

  it('prefills fields from the picked template and saves via POST /api/providers', async () => {
    const calls = renderPage({
      'GET /api/providers': { body: PROVIDERS },
      'GET /api/provider-templates': { body: TEMPLATES },
      'POST /api/providers': { body: { provider: PROVIDERS.providers[0], status: 'created', default: 'primary' } },
    })

    fireEvent.click(await screen.findByRole('button', { name: /新增 provider/ }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Provider 模板' }), { target: { value: 'ollama' } })

    // Template defaults flow into the form; keyless templates hide the key field.
    expect((screen.getByRole('textbox', { name: 'Base URL' }) as HTMLInputElement).value).toBe('http://localhost:11434')
    expect((screen.getByRole('textbox', { name: 'Model' }) as HTMLInputElement).value).toBe('llama3.3')
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: 'Provider 名称' }), { target: { value: 'local-ollama' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      const saved = calls.filter(call => call.key === 'POST /api/providers')
      expect(saved).toHaveLength(1)
      expect(saved[0]!.body).toMatchObject({
        name: 'local-ollama',
        template: 'ollama',
        base_url: 'http://localhost:11434',
        model: 'llama3.3',
        kind: 'chat',
      })
      expect(saved[0]!.body).not.toHaveProperty('api_key')
    })
    expect(await screen.findByText('已保存并生效，无需重启')).toBeInTheDocument()
  })

  it('shows the probe result card with error_kind advice on failure', async () => {
    const calls = renderPage({
      'GET /api/providers': { body: PROVIDERS },
      'GET /api/provider-templates': { body: TEMPLATES },
      'POST /api/providers/test': { body: { ok: false, error_kind: 'auth', message: 'invalid x-api-key', latency_ms: 42, name: 'primary', model: 'claude-sonnet-4' } },
    })

    const testButtons = await screen.findAllByRole('button', { name: '测试连接' })
    fireEvent.click(testButtons[0]!)

    expect(await screen.findByText('连接测试失败')).toBeInTheDocument()
    expect(screen.getByText('invalid x-api-key')).toBeInTheDocument()
    expect(screen.getByText('auth')).toBeInTheDocument()
    expect(screen.getByText(/检查 API key/)).toBeInTheDocument()
    expect(calls.some(call => call.key === 'POST /api/providers/test' && call.body?.name === 'primary')).toBe(true)
  })

  it('enumerates models with a live source badge', async () => {
    const calls = renderPage({
      'GET /api/providers': { body: PROVIDERS },
      'GET /api/provider-templates': { body: TEMPLATES },
      'GET /api/providers/primary/models': { body: { name: 'primary', models: ['claude-sonnet-4', 'claude-haiku-4'], source: 'live' } },
    })

    fireEvent.click((await screen.findAllByRole('button', { name: '枚举可用模型' }))[0]!)

    expect(await screen.findByText('claude-haiku-4')).toBeInTheDocument()
    expect(screen.queryByText('在线枚举不可用，显示兜底列表')).not.toBeInTheDocument()
    expect(calls.some(call => call.key === 'GET /api/providers/primary/models')).toBe(true)
  })

  it('marks the fallback model list as offline', async () => {
    renderPage({
      'GET /api/providers': { body: PROVIDERS },
      'GET /api/provider-templates': { body: TEMPLATES },
      'GET /api/providers/embedder/models': { body: { name: 'embedder', models: ['bge-m3'], source: 'fallback' } },
    })

    fireEvent.click((await screen.findAllByRole('button', { name: '枚举可用模型' }))[1]!)
    expect(await screen.findByText('在线枚举不可用，显示兜底列表')).toBeInTheDocument()
  })

  it('deletes a provider only after the inline confirm', async () => {
    const calls = renderPage({
      'GET /api/providers': { body: PROVIDERS },
      'GET /api/provider-templates': { body: TEMPLATES },
      'DELETE /api/providers/embedder': { body: { deleted: true, name: 'embedder', default: 'primary' } },
    })

    fireEvent.click((await screen.findAllByRole('button', { name: '删除' }))[1]!)
    expect(await screen.findByText(/确认删除「embedder」/)).toBeInTheDocument()
    expect(screen.getByText(/默认将移到 primary/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(calls.some(call => call.key.startsWith('DELETE'))).toBe(false)

    fireEvent.click((await screen.findAllByRole('button', { name: '删除' }))[1]!)
    fireEvent.click(await screen.findByRole('button', { name: '确认删除' }))
    await waitFor(() => {
      expect(calls.some(call => call.key === 'DELETE /api/providers/embedder')).toBe(true)
    })
    expect(await screen.findByText(/已删除 provider「embedder」/)).toBeInTheDocument()
    expect(screen.getByText(/当前默认 provider：primary/)).toBeInTheDocument()
  })

  it('switches to the system diagnostics tab and renders /api/doctor checks', async () => {
    const calls = renderPage({
      'GET /api/providers': { body: PROVIDERS },
      'GET /api/provider-templates': { body: TEMPLATES },
      'GET /api/meta': { body: { version: '0.2.0', memory_mode: 'full', environment: 'home' } },
      'GET /api/doctor': {
        body: {
          live: false,
          config_source: 'config/homemaster.yaml',
          checks: [
            { name: 'python_environment', status: 'PASS', message: 'python ok', impact: null, suggestion: null, details: {} },
            { name: 'memory_backend', status: 'FAIL', message: 'neo4j unreachable', impact: null, suggestion: 'Start Neo4j first.', details: {} },
          ],
        },
      },
    })

    fireEvent.click(await screen.findByRole('tab', { name: '系统状态与环境诊断' }))

    expect(await screen.findByText('Python 运行环境')).toBeInTheDocument()
    expect(screen.getByText('长期记忆数据库 (Neo4j / Qdrant)')).toBeInTheDocument()
    expect(screen.getByText('建议：Start Neo4j first.')).toBeInTheDocument()
    expect(calls.some(call => call.key === 'GET /api/doctor')).toBe(true)
  })
})
