import { useCallback, useEffect, useMemo, useState } from 'react'

import type {
  ProviderPayload,
  ProviderTemplate,
  ProviderTestResult,
} from '../api/http'
import { useToast } from '../components/toast'
import { Link, navigate } from '../router'
import { ProviderTestResultCard } from './ProviderTestResult'
import styles from './OnboardingPage.module.css'

/** Structural subset of HomeMasterApi this page consumes (contract: web/app.py). */
export type OnboardingApi = {
  providerTemplates(): Promise<{ templates: ProviderTemplate[] }>
  testProvider(payload: { name: string } | ProviderPayload): Promise<ProviderTestResult>
  upsertProvider(payload: ProviderPayload, options?: { overwrite?: boolean }): Promise<{ status: string; default?: string }>
}

type Props = {
  api: OnboardingApi
  /** Let the shell refresh composer pickers after the provider lands. */
  onDone?: () => void
}

const STEPS = ['选择服务商', '填写连接信息', '测试并保存'] as const

export function OnboardingPage({ api, onDone }: Props) {
  const toast = useToast()
  const [step, setStep] = useState(0)
  const [templates, setTemplates] = useState<ProviderTemplate[] | null>(null)
  const [templatesError, setTemplatesError] = useState<string | null>(null)
  const [template, setTemplate] = useState<ProviderTemplate | null>(null)
  const [name, setName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [authType, setAuthType] = useState('api_key')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    void api.providerTemplates()
      .then(result => { setTemplates(result.templates) })
      .catch(error => { setTemplatesError(error instanceof Error ? error.message : '模板加载失败') })
  }, [api])

  const pickTemplate = useCallback((picked: ProviderTemplate) => {
    // detection≠consent: selecting a template only prefills the form — nothing
    // is probed or persisted until the user runs the test and hits 保存并进入.
    setTemplate(picked)
    setName(current => (current.trim().length > 0 ? current : picked.key))
    setBaseUrl(picked.base_url)
    setModel(picked.model)
    setApiKey('')
    setAuthType('api_key')
    setFieldError(null)
    setTestResult(null)
    setStep(1)
  }, [])

  const payload = useMemo((): ProviderPayload => {
    const body: ProviderPayload = {
      name: name.trim(),
      base_url: baseUrl.trim(),
      model: model.trim(),
      auth_type: authType,
      kind: 'chat',
      set_default: true,
    }
    if (template !== null) body.template = template.key
    if (apiKey.trim().length > 0) body.api_key = apiKey.trim()
    return body
  }, [apiKey, authType, baseUrl, model, name, template])

  const next = useCallback(() => {
    if (name.trim().length === 0) { setFieldError('请填写 provider 名称。'); return }
    if (baseUrl.trim().length === 0) { setFieldError('请填写 base_url。'); return }
    if (model.trim().length === 0) { setFieldError('请填写 model。'); return }
    if (template?.keyless !== true && apiKey.trim().length === 0) {
      setFieldError('请填写 API Key。')
      return
    }
    setFieldError(null)
    setStep(2)
  }, [apiKey, baseUrl, model, name, template])

  const runTest = useCallback(async () => {
    setTesting(true)
    setTestResult(null)
    setSaveError(null)
    try {
      setTestResult(await api.testProvider(payload))
    } catch (error) {
      setTestResult({
        ok: false,
        error_kind: 'unknown',
        message: error instanceof Error ? error.message : '测试请求失败',
      })
    } finally {
      setTesting(false)
    }
  }, [api, payload])

  const save = useCallback(async () => {
    setSaving(true)
    setSaveError(null)
    try {
      await api.upsertProvider(payload)
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : '保存失败')
      setSaving(false)
      return
    }
    setSaving(false)
    toast.show({ title: `provider「${payload.name ?? ''}」已配置并生效` })
    onDone?.()
    navigate('#/chat')
  }, [api, onDone, payload, toast])

  return (
    <section className={styles.page} aria-labelledby="onboarding-title">
      <div className={styles.head}>
        <p className={styles.eyebrow}>首次配置</p>
        <h1 id="onboarding-title">接入一个模型服务，开始使用</h1>
        <p className={styles.sub}>三步完成 provider 配置。探测到的默认值只用于预填——只有你点「测试并保存」后才会落盘。</p>
        <ol className={styles.steps} aria-label="引导步骤">
          {STEPS.map((label, index) => (
            <li key={label} data-active={index === step || undefined} data-done={index < step || undefined}>
              <span>{index + 1}</span>{label}
            </li>
          ))}
        </ol>
        <Link className={styles.skip} to="#/chat">跳过，稍后在 Provider 页里配 →</Link>
      </div>

      {step === 0 && (
        <div className={styles.stepBody}>
          {templatesError !== null && <div className={styles.errorText} role="alert">模板列表加载失败：{templatesError}</div>}
          {templates === null && templatesError === null && <div className={styles.muted}>正在加载模板…</div>}
          <div className={styles.cards}>
            {(templates ?? []).map(item => (
              <button
                type="button"
                key={item.key}
                className={styles.templateCard}
                onClick={() => { pickTemplate(item) }}
              >
                <strong>{item.title}</strong>
                <span className={styles.cardMeta}>{item.model || '自定义模型'}</span>
                <span className={styles.cardMeta}>{item.base_url || '自定义 base_url'}</span>
                {item.keyless && <span className={styles.keylessBadge}>本地 · 无需 Key</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {step === 1 && template !== null && (
        <div className={styles.stepBody}>
          <p className={styles.muted}>
            模板：<strong>{template.title}</strong>
            <button type="button" className={styles.linkish} onClick={() => { setStep(0) }}>换一个</button>
          </p>
          <div className={styles.grid}>
            <label className={styles.field}>
              <span>provider 名称</span>
              <input type="text" value={name} aria-label="Provider 名称" onChange={event => { setName(event.target.value) }} />
            </label>
            {template.keyless !== true && (
              <label className={styles.field}>
                <span>API Key</span>
                <input
                  type="password"
                  value={apiKey}
                  placeholder="sk-…"
                  aria-label="API Key"
                  autoComplete="off"
                  onChange={event => { setApiKey(event.target.value) }}
                />
              </label>
            )}
            {template.supports_auth_token && (
              <label className={styles.field}>
                <span>auth_type</span>
                <select value={authType} aria-label="Auth type" onChange={event => { setAuthType(event.target.value) }}>
                  <option value="api_key">api_key</option>
                  <option value="auth_token">auth_token</option>
                </select>
              </label>
            )}
            <label className={styles.field}>
              <span>base_url</span>
              <input type="text" value={baseUrl} aria-label="Base URL" onChange={event => { setBaseUrl(event.target.value) }} />
            </label>
            <label className={styles.field}>
              <span>model</span>
              <input
                type="text"
                value={model}
                placeholder={template.model || '模型 id，自由输入'}
                aria-label="Model"
                onChange={event => { setModel(event.target.value) }}
              />
            </label>
          </div>
          <p className={styles.hint}>保存后可到 Provider 页「枚举可用模型」挑选线上模型列表。</p>
          {fieldError !== null && <div className={styles.errorText} role="alert">{fieldError}</div>}
          <div className={styles.actions}>
            <button type="button" onClick={() => { setStep(0) }}>上一步</button>
            <button type="button" className={styles.primary} onClick={next}>下一步</button>
          </div>
        </div>
      )}

      {step === 2 && template !== null && (
        <div className={styles.stepBody}>
          <div className={styles.summary}>
            <strong>{name}</strong>
            <span>{template.title} · <code>{model}</code></span>
            <span className={styles.muted}><code>{baseUrl}</code></span>
            {template.keyless !== true && <span className={styles.muted}>API Key 已填写（不回显）</span>}
          </div>
          <div className={styles.actions}>
            <button type="button" className={styles.primary} onClick={() => { void runTest() }} disabled={testing || saving}>
              {testing ? '测试中…' : testResult === null ? '测试连接' : '重新测试'}
            </button>
            <button type="button" onClick={() => { setStep(1) }} disabled={saving}>返回修改</button>
          </div>
          {testResult !== null && <ProviderTestResultCard result={testResult} />}
          {testResult?.ok === true && (
            <div className={styles.actions}>
              <button type="button" className={styles.primary} onClick={() => { void save() }} disabled={saving}>
                {saving ? '保存中…' : '保存并进入'}
              </button>
            </div>
          )}
          {testResult?.ok === false && (
            <p className={styles.hint}>测试未通过：按上方建议修正后重新测试；也可以「返回修改」调整字段。</p>
          )}
          {saveError !== null && <div className={styles.errorText} role="alert">{saveError}</div>}
        </div>
      )}
    </section>
  )
}
