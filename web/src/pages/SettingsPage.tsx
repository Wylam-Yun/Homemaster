import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  HttpError,
  type DoctorCheck,
  type DoctorReport,
  type MetaInfo,
  type ProviderModels,
  type ProviderPayload,
  type ProviderProfile,
  type ProviderTemplate,
  type ProviderTestResult,
} from '../api/http'
import { useToast } from '../components/toast'
import { PageHeader } from '../components/ui/PageHeader'
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
  meta?(): Promise<MetaInfo>
  doctor?(options?: { live?: boolean }): Promise<DoctorReport>
}

type Props = {
  api: SettingsApi
  /** Let the shell refresh composer pickers after a write. */
  onProvidersChanged?: () => void
}

type SettingsTab = 'providers' | 'diagnostics'

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

const KIND_HUMAN_LABEL: Record<string, string> = {
  chat: '对话推理',
  embedding: '记忆向量',
}

const DOCTOR_CHECK_LABELS: Record<string, string> = {
  python_environment: 'Python 运行环境',
  memory_mode: '记忆系统模式',
  mindmemos_runtime: '记忆引擎安装状态',
  config_source: '模型与系统配置文件',
  embedding_endpoint: '向量模型 (Embedding) 接口',
  memory_backend: '长期记忆数据库 (Neo4j / Qdrant)',
  ignored_runtime_paths: '运行数据目录隔离',
  chat_provider_live: '对话模型实时连通性',
  embedding_provider_live: '向量模型实时连通性',
}

function doctorCheckTitle(name: string): string {
  if (name.startsWith('import:')) {
    return `依赖库检查：${name.slice('import:'.length)}`
  }
  return DOCTOR_CHECK_LABELS[name] ?? name
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
  const [tab, setTab] = useState<SettingsTab>('providers')
  const [providers, setProviders] = useState<ProviderProfile[] | null>(null)
  const [templates, setTemplates] = useState<ProviderTemplate[]>([])
  const [listError, setListError] = useState<string | null>(null)
  const [form, setForm] = useState<FormState | null>(null)
  const [rowTests, setRowTests] = useState<Record<string, ProviderTestResult | 'running'>>({})
  const [modelsPanel, setModelsPanel] = useState<{ name: string; loading: boolean; models: string[]; source: string | null; error: string | null } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  // System diagnostics tab state
  const [meta, setMeta] = useState<MetaInfo | null>(null)
  const [doctorReport, setDoctorReport] = useState<DoctorReport | null>(null)
  const [doctorLoading, setDoctorLoading] = useState(false)
  const [doctorError, setDoctorError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const listed = await api.providers()
      setProviders(listed.providers)
      setListError(null)
    } catch (error) {
      setListError(error instanceof Error ? error.message : '服务商列表加载失败。')
    }
  }, [api])

  useEffect(() => {
    void refresh()
    void api.providerTemplates().then(result => { setTemplates(result.templates) }).catch(() => {})
    if (api.meta !== undefined) {
      void api.meta().then(setMeta).catch(() => {})
    }
  }, [api, refresh])

  const runDoctor = useCallback(async (live: boolean) => {
    if (api.doctor === undefined) return
    setDoctorLoading(true)
    setDoctorError(null)
    try {
      const report = await api.doctor({ live })
      setDoctorReport(report)
    } catch (error) {
      setDoctorError(error instanceof Error ? error.message : '系统诊断执行失败。')
    } finally {
      setDoctorLoading(false)
    }
  }, [api])

  useEffect(() => {
    if (tab === 'diagnostics' && doctorReport === null && !doctorLoading && api.doctor !== undefined) {
      void runDoctor(false)
    }
  }, [api.doctor, doctorLoading, doctorReport, runDoctor, tab])

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
      update({ error: '请填写服务商显示名称。' })
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

  const setDefaultProvider = useCallback(async (provider: ProviderProfile) => {
    try {
      const result = await api.patchProvider(provider.name, { set_default: true })
      toast.show({
        title: `已将「${provider.name}」设为默认 provider`,
        description: result.default !== undefined ? `当前默认 provider：${result.default}` : undefined,
      })
      await refresh()
      onProvidersChanged?.()
    } catch (error) {
      toast.show({
        title: `设置默认 provider 失败`,
        description: error instanceof Error ? error.message : undefined,
      })
    }
  }, [api, onProvidersChanged, refresh, toast])

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
    const next = (providers ?? []).find(item => item.name !== deletingName && item.kind !== 'embedding')
    return next?.name ?? null
  }, [providers])

  return (
    <section className={styles.page} aria-labelledby="settings-page-title">
      <PageHeader
        eyebrow="模型与系统设置"
        title="Provider 设置"
        titleId="settings-page-title"
        subtitle="配置对话推理大模型与长期记忆向量模型（Embedding），或执行系统运行环境自检。所有配置保存后即时生效，无需重启。"
        actions={
          tab === 'providers' ? (
            <button type="button" className={styles.primary} onClick={openNew} disabled={form !== null}>
              ＋ 新增 provider
            </button>
          ) : (
            <div className={styles.cardActions}>
              <button type="button" onClick={() => { void runDoctor(false) }} disabled={doctorLoading}>
                {doctorLoading ? '诊断中…' : '快速自检'}
              </button>
              <button type="button" className={styles.primary} onClick={() => { void runDoctor(true) }} disabled={doctorLoading}>
                {doctorLoading ? '测试中…' : '真实模型连通自检'}
              </button>
            </div>
          )
        }
      />

      {api.doctor !== undefined && (
        <div className={styles.tabs} role="tablist" aria-label="设置分类">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'providers'}
            onClick={() => { setTab('providers') }}
          >
            模型服务商 ({providers?.length ?? 0})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'diagnostics'}
            onClick={() => { setTab('diagnostics') }}
          >
            系统状态与环境诊断
          </button>
        </div>
      )}

      {tab === 'providers' && <>
        {listError !== null && (
          <div className={styles.error} role="alert">
            <div><strong>服务商列表加载失败</strong><span>{listError}</span></div>
            <button type="button" onClick={() => { void refresh() }}>重新加载</button>
          </div>
        )}

        {providers !== null && providers.length === 0 && form === null && (
          <div className={styles.empty}>
            还没有配置模型服务商。<Link to="#/onboarding">打开三步配置向导</Link>，或点右上角「新增 provider」手动添加。
          </div>
        )}

        <div className={styles.list}>
          {(providers ?? []).map(provider => {
            const rowTest = rowTests[provider.name]
            const modelsOpen = modelsPanel?.name === provider.name
            const kindValue = provider.kind || 'chat'
            const humanKind = KIND_HUMAN_LABEL[kindValue]
            return (
              <article className={styles.card} key={provider.name}>
                <div className={styles.cardMain}>
                  <div className={styles.cardTitle}>
                    <strong>{provider.name}</strong>
                    {humanKind !== undefined && <span className={styles.humanKind}>{humanKind}</span>}
                    <span className={styles.badge}>{kindValue}</span>
                    {provider.is_default === true && <span className={styles.defaultBadge}>默认对话模型</span>}
                    <span className={styles.keyState} data-configured={provider.api_key_configured}>
                      {provider.api_key_configured ? `已配置 Key ×${provider.api_key_count}` : '未配置 Key'}
                    </span>
                  </div>
                  <div className={styles.cardMeta}>
                    <span>接口协议：{provider.api_format}（{provider.auth_type}）</span>
                    <span>模型：<code className={styles.mono}>{provider.model}</code></span>
                    <span>地址：<code className={styles.mono}>{provider.base_url}</code></span>
                  </div>
                  <div className={styles.cardActions}>
                    <button type="button" onClick={() => { void runTest('row', provider.name) }} disabled={rowTest === 'running'}>
                      {rowTest === 'running' ? '测试中…' : '测试连接'}
                    </button>
                    <button type="button" onClick={() => { void listModels(provider.name) }}>
                      {modelsOpen ? '刷新模型列表' : '枚举可用模型'}
                    </button>
                    {provider.is_default !== true && provider.kind !== 'embedding' && (
                      <button type="button" onClick={() => { void setDefaultProvider(provider) }}>设为默认</button>
                    )}
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
                        {modelsPanel.source === 'fallback' && <span className={styles.fallbackBadge}>在线枚举不可用，显示兜底列表</span>}
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
            <h2>{isNew ? '新增模型服务商' : `编辑服务商「${editingName}」`}</h2>
            <div className={styles.grid}>
              {isNew && (
                <label className={styles.field}>
                  <span>服务商预设模板</span>
                  <select value={form.templateKey} onChange={event => { pickTemplate(event.target.value) }} aria-label="Provider 模板">
                    {templates.map(template => (
                      <option key={template.key} value={template.key}>{template.title}</option>
                    ))}
                    {templates.length === 0 && <option value="">自定义</option>}
                  </select>
                </label>
              )}
              <label className={styles.field}>
                <span>显示名称</span>
                <input
                  type="text"
                  value={form.name}
                  disabled={!isNew}
                  placeholder={selectedTemplate?.title ?? '例如：DeepSeek / Claude'}
                  aria-label="Provider 名称"
                  onChange={event => { update({ name: event.target.value }) }}
                />
              </label>
              <label className={styles.field}>
                <span>模型用途 (kind)</span>
                <select value={form.kind} onChange={event => { update({ kind: event.target.value }) }} aria-label="Provider 用途">
                  <option value="chat">对话推理模型 (chat)</option>
                  <option value="embedding">长期记忆向量模型 (embedding)</option>
                </select>
              </label>
              <label className={styles.field}>
                <span>接口地址 (base_url)</span>
                <input
                  type="text"
                  value={form.baseUrl}
                  placeholder="https://api.example.com/v1"
                  aria-label="Base URL"
                  onChange={event => { update({ baseUrl: event.target.value }) }}
                />
              </label>
              <label className={styles.field}>
                <span>默认模型名称 (model)</span>
                <div className={styles.inline}>
                  <input
                    type="text"
                    value={form.model}
                    placeholder="填写模型 ID，如 deepseek-chat"
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
                  <span>API 密钥 (API Key，只写不回显)</span>
                  <input
                    type="password"
                    value={form.apiKey}
                    placeholder={isNew ? 'sk-…' : '已安全保存，留空则保持原密钥不变'}
                    aria-label="API Key"
                    autoComplete="off"
                    onChange={event => { update({ apiKey: event.target.value }) }}
                  />
                </label>
              )}
              {keyless && <p className={styles.hint}>该模板为本地免密钥服务（如 Ollama），无需填写 API Key。</p>}
              {showAuthType && (
                <label className={styles.field}>
                  <span>鉴权方式 (auth_type)</span>
                  <select value={form.authType} onChange={event => { update({ authType: event.target.value }) }} aria-label="Auth type">
                    <option value="api_key">标准 API Key (x-api-key / Bearer)</option>
                    <option value="auth_token">Auth Token 令牌 (auth_token)</option>
                  </select>
                </label>
              )}
              {form.kind === 'embedding' && (
                <label className={styles.field}>
                  <span>向量接口完整地址 (embedding_url，可选)</span>
                  <input
                    type="text"
                    value={form.embeddingUrl}
                    placeholder="留空则根据 base_url 自动拼接 /embeddings"
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
                  设为默认对话服务商
                </label>
              )}
            </div>

            {!isNew && editingName !== null && modelsPanel !== null && modelsPanel.name === editingName && (
              <div className={styles.models} aria-label={`${editingName} 可用模型`}>
                {modelsPanel.loading && <span className={styles.muted}>正在枚举模型…</span>}
                {modelsPanel.error !== null && <span className={styles.errorText}>{modelsPanel.error}</span>}
                {!modelsPanel.loading && modelsPanel.error === null && (
                  <>
                    {modelsPanel.source === 'fallback' && <span className={styles.fallbackBadge}>在线枚举不可用，显示兜底列表</span>}
                    {modelsPanel.models.length === 0
                      ? <span className={styles.muted}>无可用模型</span>
                      : modelsPanel.models.map(model => (
                        <button
                          type="button"
                          className={styles.modelChip}
                          key={model}
                          title="点选填入默认模型"
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
      </>}

      {tab === 'diagnostics' && (
        <div className={styles.diagnosticsPanel} aria-label="系统状态与环境诊断">
          {meta !== null && (
            <div className={styles.metaGrid}>
              <div className={styles.metaCard}>
                <span>系统版本</span>
                <strong>v{meta.version}</strong>
              </div>
              <div className={styles.metaCard}>
                <span>运行环境</span>
                <strong>{meta.environment === 'home' ? '家居环境 (Home)' : meta.environment === 'browser' ? '浏览器环境 (Browser)' : meta.environment ?? '标准工作台'}</strong>
              </div>
              <div className={styles.metaCard}>
                <span>记忆系统档位</span>
                <strong>{meta.memory_mode === 'full' ? '完整图谱记忆档 (full)' : '轻量文件记忆档 (files)'}</strong>
              </div>
              <div className={styles.metaCard}>
                <span>配置文件路径</span>
                <strong><code>{doctorReport?.config_source ?? '默认路径'}</code></strong>
              </div>
            </div>
          )}

          {doctorError !== null && (
            <div className={styles.error} role="alert">
              <div><strong>系统诊断失败</strong><span>{doctorError}</span></div>
              <button type="button" onClick={() => { void runDoctor(false) }}>重试</button>
            </div>
          )}

          {doctorLoading && doctorReport === null && (
            <div className={styles.empty}>正在执行系统环境检查…</div>
          )}

          {doctorReport !== null && (
            <div className={styles.checkList}>
              {doctorReport.checks.map((check: DoctorCheck) => (
                <article key={check.name} className={styles.checkCard} data-status={check.status}>
                  <div className={styles.checkHead}>
                    <span className={styles.checkBadge} data-status={check.status}>
                      {check.status === 'PASS' ? '正常' : check.status === 'WARN' ? '警告' : '异常'}
                    </span>
                    <strong>{doctorCheckTitle(check.name)}</strong>
                    <code className={styles.mono}>{check.name}</code>
                  </div>
                  <p className={styles.checkMessage}>{check.message}</p>
                  {check.suggestion !== null && (
                    <p className={styles.checkSuggestion}>建议：{check.suggestion}</p>
                  )}
                </article>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
