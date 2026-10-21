import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  HttpError,
  type ProviderModels,
  type ProviderPayload,
  type ProviderProfile,
  type ProviderTemplate,
  type ProviderTestResult,
} from '../api/http'
import { useToast } from '../components/toast'
import { Link } from '../router'
import { ProviderTestResultCard } from './ProviderTestResult'
import styles from './SettingsPage.module.css'

/** Structural subset of HomeMasterApi this page consumes (contract: web/app.py). */
export type SettingsApi = {
  providers(): Promise<{ providers: ProviderProfile[] }>
  providerTemplates(): Promise<{ templates: ProviderTemplate[] }>
  upsertProvider(payload: ProviderPayload, options?: { overwrite?: boolean }): Promise<{ provider: ProviderProfile; status: string; default?: string }>
  patchProvider(name: string, payload: ProviderPayload): Promise<{ provider: ProviderProfile; status: string; default?: string }>
  deleteProvider(name: string): Promise<{ deleted: boolean; name: string; default?: string }>
  testProvider(payload: { name: string } | ProviderPayload): Promise<ProviderTestResult>
  providerModels(name: string): Promise<ProviderModels>
}

type Props = {
  api: SettingsApi
  /** Let the shell refresh composer pickers after a write. */
  onProvidersChanged?: () => void
}

type FormState = {
  target: 'new' | { name: string }
  templateKey: string
  name: string
  kind: string
  baseUrl: string
  model: string
  apiKey: string
  authType: string
  embeddingUrl: string
  setDefault: boolean
  saving: boolean
  testing: boolean
  testResult: ProviderTestResult | null
  error: string | null
}

function blankForm(templates: ProviderTemplate[], hasProviders: boolean): FormState {
  const template = templates[0]
  return {
    target: 'new',
    templateKey: template?.key ?? '',
    name: '',
    kind: 'chat',
    baseUrl: template?.base_url ?? '',
    model: template?.model ?? '',
    apiKey: '',
    authType: 'api_key',
    embeddingUrl: '',
    // Mirror the backend: the first configured provider becomes the default.
    setDefault: !hasProviders,
    saving: false,
    testing: false,
    testResult: null,
    error: null,
  }
}

function formFromProfile(provider: ProviderProfile): FormState {
  return {
    target: { name: provider.name },
    templateKey: '',
    name: provider.name,
    kind: provider.kind || 'chat',
    baseUrl: provider.base_url,
    model: provider.model,
    apiKey: '',
    authType: provider.auth_type || 'api_key',
    embeddingUrl: '',
    setDefault: false,
    saving: false,
    testing: false,
    testResult: null,
    error: null,
  }
}

export function SettingsPage({ api, onProvidersChanged }: Props) {
  const toast = useToast()
  const [providers, setProviders] = useState<ProviderProfile[] | null>(null)
  const [templates, setTemplates] = useState<ProviderTemplate[]>([])
  const [listError, setListError] = useState<string | null>(null)
  const [form, setForm] = useState<FormState | null>(null)
  const [rowTests, setRowTests] = useState<Record<string, ProviderTestResult | 'running'>>({})
  const [modelsPanel, setModelsPanel] = useState<{ name: string; loading: boolean; models: string[]; source: string | null; error: string | null } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const listed = await api.providers()
      setProviders(listed.providers)
      setListError(null)
    } catch (error) {
      setListError(error instanceof Error ? error.message : 'Provider 列表加载失败。')
    }
  }, [api])

  useEffect(() => {
    void refresh()
    void api.providerTemplates().then(result => { setTemplates(result.templates) }).catch(() => {})
  }, [api, refresh])

  const selectedTemplate = useMemo(
    () => templates.find(template => template.key === form?.templateKey) ?? null,
    [form?.templateKey, templates],
  )
  const isNew = form?.target === 'new'
  const editingName = form !== null && form.target !== 'new' ? form.target.name : null
  const keyless = isNew && selectedTemplate?.keyless === true
  const showAuthType = selectedTemplate?.supports_auth_token === true || form?.authType === 'auth_token'

  const openNew = useCallback(() => {
    setForm(blankForm(templates, (providers ?? []).length > 0))
    setConfirmDelete(null)
  }, [providers, templates])

  const openEdit = useCallback((provider: ProviderProfile) => {
    setForm(formFromProfile(provider))
    setConfirmDelete(null)
  }, [])

  const update = (patch: Partial<FormState>): void => {
    setForm(current => (current === null ? current : { ...current, ...patch }))
  }

  const pickTemplate = (key: string): void => {
    const template = templates.find(item => item.key === key) ?? null
    update({
      templateKey: key,
      baseUrl: template?.base_url ?? '',
      model: template?.model ?? '',
      // keyless templates (Ollama) take no key; stale input must not leak in.
      apiKey: template?.keyless === true ? '' : form?.apiKey ?? '',
      authType: 'api_key',
    })
  }

  const buildPayload = useCallback((): ProviderPayload => {
    if (form === null) return {}
    const payload: ProviderPayload = {
      kind: form.kind,
      base_url: form.baseUrl.trim(),
      model: form.model.trim(),
      auth_type: form.authType,
      set_default: form.kind !== 'embedding' && form.setDefault,
    }
    if (form.kind === 'embedding' && form.embeddingUrl.trim().length > 0) {
      payload.embedding_url = form.embeddingUrl.trim()
    }
    if (form.target === 'new') {
      payload.name = form.name.trim()
      if (form.templateKey !== '') payload.template = form.templateKey
      if (form.apiKey.trim().length > 0) payload.api_key = form.apiKey.trim()
    } else {
      // PATCH: api_key is write-only; "" keeps the stored keys untouched.
      if (form.apiKey.trim().length > 0) payload.api_key = form.apiKey.trim()
    }
    return payload
  }, [form])

  const runTest = useCallback(async (mode: 'row' | 'form', name: string) => {
    if (mode === 'row') {
      setRowTests(current => ({ ...current, [name]: 'running' }))
      try {
        const result = await api.testProvider({ name })
        setRowTests(current => ({ ...current, [name]: result }))
      } catch (error) {
        setRowTests(current => ({
          ...current,
          [name]: { ok: false, error_kind: 'unknown', message: error instanceof Error ? error.message : '测试请求失败' },
        }))
      }
      return
    }
    update({ testing: true, testResult: null })
    try {
      const payload = buildPayload()
      if (payload.name === undefined && editingName !== null) payload.name = editingName
      const result = await api.testProvider(payload)
      update({ testing: false, testResult: result })
    } catch (error) {
      update({
        testing: false,
        testResult: { ok: false, error_kind: 'unknown', message: error instanceof Error ? error.message : '测试请求失败' },
      })
    }
  }, [api, buildPayload, editingName])

  const listModels = useCallback(async (name: string) => {
    setModelsPanel({ name, loading: true, models: [], source: null, error: null })
    try {
      const result = await api.providerModels(name)
      setModelsPanel({ name, loading: false, models: result.models, source: result.source, error: null })
    } catch (error) {
      setModelsPanel({ name, loading: false, models: [], source: null, error: error instanceof Error ? error.message : '枚举失败' })
    }
  }, [api])

  const save = useCallback(async () => {
    if (form === null) return
    const payload = buildPayload()
    if (isNew && (payload.name === undefined || payload.name === '')) {
      update({ error: '请填写 provider 名称。' })
      return
    }
    update({ saving: true, error: null })
    const write = async (overwrite: boolean): Promise<{ status: string; default?: string }> => {
      if (form.target === 'new') return api.upsertProvider(payload, { overwrite })
      return api.patchProvider(form.target.name, payload)
    }
    try {
      let result: { status: string; default?: string }
      try {
        result = await write(false)
      } catch (error) {
        // 409 provider_conflict: same name, different settings — offer overwrite.
        const conflict = error instanceof HttpError && error.status === 409
        if (!conflict || form.target !== 'new' ||
          !window.confirm(`provider「${form.name}」已存在且配置不同，覆盖保存？`)) {
          throw error
        }
        result = await write(true)
      }
      setForm(null)
      toast.show({
        title: '已保存并生效，无需重启',
        description: `provider「${form.name}」${result.status === 'created' ? '已创建' : '已更新'}`,
      })
      await refresh()
      onProvidersChanged?.()
    } catch (error) {
      update({ saving: false, error: error instanceof Error ? error.message : '保存失败' })
    }
  }, [api, buildPayload, form, isNew, onProvidersChanged, refresh, toast])

  const remove = useCallback(async (provider: ProviderProfile) => {
    setDeleting(true)
    try {
      const result = await api.deleteProvider(provider.name)
      setConfirmDelete(null)
      toast.show({
        title: `已删除 provider「${provider.name}」`,
        description: result.default !== undefined ? `当前默认 provider：${result.default}` : undefined,
      })
      await refresh()
      onProvidersChanged?.()
    } catch (error) {
      toast.show({
        title: `删除 provider「${provider.name}」失败`,
        description: error instanceof Error ? error.message : undefined,
      })
    } finally {
      setDeleting(false)
    }
  }, [api, onProvidersChanged, refresh, toast])

  const predictedDefault = useCallback((deletingName: string): string | null => {
    // Backend picks the first remaining chat item as the replacement default.
    const next = (providers ?? []).find(item => item.name !== deletingName && item.kind !== 'embedding')
    return next?.name ?? null
  }, [providers])

  return (
    <section className={styles.page} aria-labelledby="settings-page-title">
      <div className={styles.headingRow}>
        <div>
          <p className={styles.eyebrow}>设置</p>
          <h1 id="settings-page-title">Provider 设置</h1>
          <p>管理 chat / embedding provider。保存即时生效，无需重启。</p>
        </div>
        <button type="button" className={styles.primary} onClick={openNew} disabled={form !== null}>
          ＋ 新增 provider
        </button>
      </div>

      {listError !== null && (
        <div className={styles.error} role="alert">
          <div><strong>Provider 列表加载失败</strong><span>{listError}</span></div>
          <button type="button" onClick={() => { void refresh() }}>重新加载</button>
        </div>
      )}

      {providers !== null && providers.length === 0 && form === null && (
        <div className={styles.empty}>
          还没有配置 provider。<Link to="#/onboarding">打开引导向导</Link>，或点「新增 provider」手动配置。
        </div>
      )}

      <div className={styles.list}>
        {(providers ?? []).map(provider => {
          const rowTest = rowTests[provider.name]
          const modelsOpen = modelsPanel?.name === provider.name
          return (
            <article className={styles.card} key={provider.name}>
              <div className={styles.cardMain}>
                <div className={styles.cardTitle}>
                  <strong>{provider.name}</strong>
                  <span className={styles.badge}>{provider.kind || 'chat'}</span>
                  <span className={styles.keyState} data-configured={provider.api_key_configured}>
                    {provider.api_key_configured ? `已配置 Key ×${provider.api_key_count}` : '未配置 Key'}
                  </span>
                </div>
                <div className={styles.cardMeta}>
                  <span>{provider.api_format} · {provider.auth_type}</span>
                  <span className={styles.mono}>{provider.model}</span>
                  <span className={styles.mono}>{provider.base_url}</span>
                </div>
                <div className={styles.cardActions}>
                  <button type="button" onClick={() => { void runTest('row', provider.name) }} disabled={rowTest === 'running'}>
                    {rowTest === 'running' ? '测试中…' : '测试连接'}
                  </button>
                  <button type="button" onClick={() => { void listModels(provider.name) }}>
                    {modelsOpen ? '刷新模型列表' : '枚举可用模型'}
                  </button>
                  <button type="button" onClick={() => { openEdit(provider) }}>编辑</button>
                  <button
                    type="button"
                    className={styles.danger}
                    onClick={() => { setConfirmDelete(current => (current === provider.name ? null : provider.name)); setForm(null) }}
                  >删除</button>
                </div>
              </div>

              {confirmDelete === provider.name && (
                <div className={styles.confirm} role="alert">
                  <span>
                    确认删除「{provider.name}」？此操作不可撤销。
                    {predictedDefault(provider.name) !== null && ` 若它是默认 provider，默认将移到 ${predictedDefault(provider.name)}。`}
                  </span>
                  <button type="button" className={styles.danger} disabled={deleting} onClick={() => { void remove(provider) }}>
                    {deleting ? '删除中…' : '确认删除'}
                  </button>
                  <button type="button" onClick={() => { setConfirmDelete(null) }}>取消</button>
                </div>
              )}

              {rowTest !== undefined && rowTest !== 'running' && <ProviderTestResultCard result={rowTest} />}

              {modelsOpen && (
                <div className={styles.models} aria-label={`${provider.name} 可用模型`}>
                  {modelsPanel.loading && <span className={styles.muted}>正在枚举模型…</span>}
                  {modelsPanel.error !== null && <span className={styles.errorText}>{modelsPanel.error}</span>}
                  {!modelsPanel.loading && modelsPanel.error === null && (
                    <>
                      {modelsPanel.source === 'fallback' && <span className={styles.fallbackBadge}>离线兜底列表</span>}
                      {modelsPanel.models.length === 0
                        ? <span className={styles.muted}>无可用模型</span>
                        : modelsPanel.models.map(model => <span className={styles.modelChip} key={model}>{model}</span>)}
                    </>
                  )}
                </div>
              )}
            </article>
          )
        })}
      </div>

      {form !== null && (
        <article className={styles.form} aria-label={isNew ? '新增 provider' : `编辑 provider ${editingName ?? ''}`}>
          <h2>{isNew ? '新增 provider' : `编辑 provider「${editingName}」`}</h2>
          <div className={styles.grid}>
            {isNew && (
              <label className={styles.field}>
                <span>模板</span>
                <select value={form.templateKey} onChange={event => { pickTemplate(event.target.value) }} aria-label="Provider 模板">
                  {templates.map(template => (
                    <option key={template.key} value={template.key}>{template.title}</option>
                  ))}
                  {templates.length === 0 && <option value="">自定义</option>}
                </select>
              </label>
            )}
            <label className={styles.field}>
              <span>名称</span>
              <input
                type="text"
                value={form.name}
                disabled={!isNew}
                placeholder={selectedTemplate?.title ?? 'provider 名称'}
                aria-label="Provider 名称"
                onChange={event => { update({ name: event.target.value }) }}
              />
            </label>
            <label className={styles.field}>
              <span>用途 kind</span>
              <select value={form.kind} onChange={event => { update({ kind: event.target.value }) }} aria-label="Provider 用途">
                <option value="chat">chat</option>
                <option value="embedding">embedding</option>
              </select>
            </label>
            <label className={styles.field}>
              <span>base_url</span>
              <input
                type="text"
                value={form.baseUrl}
                placeholder="https://api.example.com/v1"
                aria-label="Base URL"
                onChange={event => { update({ baseUrl: event.target.value }) }}
              />
            </label>
            <label className={styles.field}>
              <span>model</span>
              <div className={styles.inline}>
                <input
                  type="text"
                  value={form.model}
                  placeholder="模型 id"
                  aria-label="Model"
                  onChange={event => { update({ model: event.target.value }) }}
                />
                {!isNew && editingName !== null && (
                  <button type="button" onClick={() => { void listModels(editingName) }}>枚举可用模型</button>
                )}
              </div>
            </label>
            {!keyless && (
              <label className={styles.field}>
                <span>API Key</span>
                <input
                  type="password"
                  value={form.apiKey}
                  placeholder={isNew ? 'sk-…' : '已配置，留空保持不变'}
                  aria-label="API Key"
                  autoComplete="off"
                  onChange={event => { update({ apiKey: event.target.value }) }}
                />
              </label>
            )}
            {keyless && <p className={styles.hint}>该模板为本地无 Key 服务（如 Ollama），无需 API Key。</p>}
            {showAuthType && (
              <label className={styles.field}>
                <span>auth_type</span>
                <select value={form.authType} onChange={event => { update({ authType: event.target.value }) }} aria-label="Auth type">
                  <option value="api_key">api_key</option>
                  <option value="auth_token">auth_token</option>
                </select>
              </label>
            )}
            {form.kind === 'embedding' && (
              <label className={styles.field}>
                <span>embedding_url</span>
                <input
                  type="text"
                  value={form.embeddingUrl}
                  placeholder="http://localhost:8080"
                  aria-label="Embedding URL"
                  onChange={event => { update({ embeddingUrl: event.target.value }) }}
                />
              </label>
            )}
            {form.kind !== 'embedding' && (
              <label className={styles.check}>
                <input
                  type="checkbox"
                  checked={form.setDefault}
                  onChange={event => { update({ setDefault: event.target.checked }) }}
                />
                设为默认 provider
              </label>
            )}
          </div>

          {!isNew && editingName !== null && modelsPanel !== null && modelsPanel.name === editingName && (
            <div className={styles.models} aria-label={`${editingName} 可用模型`}>
              {modelsPanel.loading && <span className={styles.muted}>正在枚举模型…</span>}
              {modelsPanel.error !== null && <span className={styles.errorText}>{modelsPanel.error}</span>}
              {!modelsPanel.loading && modelsPanel.error === null && (
                <>
                  {modelsPanel.source === 'fallback' && <span className={styles.fallbackBadge}>离线兜底列表</span>}
                  {modelsPanel.models.length === 0
                    ? <span className={styles.muted}>无可用模型</span>
                    : modelsPanel.models.map(model => (
                      <button
                        type="button"
                        className={styles.modelChip}
                        key={model}
                        title="点选填入 model"
                        onClick={() => { update({ model }) }}
                      >{model}</button>
                    ))}
                </>
              )}
            </div>
          )}

          {form.error !== null && <div className={styles.errorText} role="alert">{form.error}</div>}
          {form.testResult !== null && <ProviderTestResultCard result={form.testResult} />}

          <div className={styles.formActions}>
            <button type="button" onClick={() => { void runTest('form', '') }} disabled={form.testing || form.saving}>
              {form.testing ? '测试中…' : '测试连接'}
            </button>
            <button type="button" className={styles.primary} onClick={() => { void save() }} disabled={form.saving || form.testing}>
              {form.saving ? '保存中…' : '保存'}
            </button>
            <button type="button" onClick={() => { setForm(null) }}>取消</button>
          </div>
        </article>
      )}
    </section>
  )
}
