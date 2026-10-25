import { useEffect, useId, useRef, useState } from 'react'

import type { ProviderInfo, ProviderModels } from '../../api/http'
import styles from './ModelPicker.module.css'

/**
 * Per-session provider/model pick, sent as SendMessageRequest.provider_name +
 * model. Follows cline's ModelPickerWithManualEntry shape: a provider dropdown
 * plus the configured model, live-enumerated provider models (when loadModels
 * is supplied), or a free-text "custom" model id.
 */
export type ModelSelection = {
  provider_name?: string
  model?: string
}

const CUSTOM_VALUE = '__custom__'

export function modelSelectionLabel(selection: ModelSelection, providers: ProviderInfo[]): string {
  if (selection.provider_name === undefined || selection.provider_name === '') return '默认模型'
  const provider = providers.find(item => item.name === selection.provider_name)
  const model = selection.model ?? provider?.model ?? null
  return model !== null && model !== '' ? `${selection.provider_name} · ${model}` : selection.provider_name
}

type EnumState = {
  name: string
  status: 'loading' | 'ready' | 'error'
  models: ProviderModels | null
}

export function ModelPicker({
  providers,
  value,
  disabled = false,
  onChange,
  loadModels,
}: {
  providers: ProviderInfo[]
  value: ModelSelection
  disabled?: boolean
  onChange: (selection: ModelSelection) => void
  /** Lazily enumerates models for a provider; cached per provider name. */
  loadModels?: (providerName: string) => Promise<ProviderModels>
}) {
  const providerId = useId()
  const modelId = useId()
  const customInputRef = useRef<HTMLInputElement>(null)
  // The custom-entry editor opens on "自定义…" and stays open while the stored
  // selection keeps a model that no provider default covers.
  const [customOpen, setCustomOpen] = useState(false)
  const [customDraft, setCustomDraft] = useState('')
  const [enumState, setEnumState] = useState<EnumState | null>(null)
  const enumCacheRef = useRef(new Map<string, ProviderModels | 'error'>())

  const selected = providers.find(item => item.name === value.provider_name)
  const providerMissing = value.provider_name !== undefined
    && value.provider_name !== ''
    && selected === undefined

  useEffect(() => {
    if (providerMissing) return
    // Selection cleared or provider switched → collapse the custom editor.
    if (value.model === undefined || (selected !== undefined && value.model === selected.model)) {
      setCustomOpen(false)
    }
  }, [providerMissing, selected, value.model])

  // Lazy enumeration: fetch once per provider name, cache the result (incl.
  // failures) so switching providers back and forth stays free.
  useEffect(() => {
    const name = value.provider_name
    if (name === undefined || name === '' || loadModels === undefined) {
      setEnumState(null)
      return
    }
    const cached = enumCacheRef.current.get(name)
    if (cached !== undefined) {
      setEnumState(cached === 'error'
        ? { name, status: 'error', models: null }
        : { name, status: 'ready', models: cached })
      return
    }
    let current = true
    setEnumState({ name, status: 'loading', models: null })
    void loadModels(name)
      .then(models => {
        enumCacheRef.current.set(name, models)
        if (current) setEnumState({ name, status: 'ready', models })
      })
      .catch(() => {
        enumCacheRef.current.set(name, 'error')
        if (current) setEnumState({ name, status: 'error', models: null })
      })
    return () => { current = false }
  }, [value.provider_name, loadModels])

  const pickProvider = (name: string): void => {
    setCustomOpen(false)
    setCustomDraft('')
    if (name === '') {
      onChange({})
      return
    }
    onChange({ provider_name: name })
  }

  const pickModel = (raw: string): void => {
    if (raw === CUSTOM_VALUE) {
      setCustomDraft('')
      setCustomOpen(true)
      requestAnimationFrame(() => { customInputRef.current?.focus() })
      return
    }
    setCustomOpen(false)
    if (raw === '') {
      onChange({ provider_name: value.provider_name })
      return
    }
    onChange({ provider_name: value.provider_name, model: raw })
  }

  const commitCustom = (): void => {
    const model = customDraft.trim()
    setCustomOpen(false)
    if (model.length === 0) {
      onChange({ provider_name: value.provider_name })
      return
    }
    onChange({ provider_name: value.provider_name, model })
  }

  const showModelControls = selected !== undefined || providerMissing || customOpen
  // Only 'live' enumerations populate the dropdown — 'fallback' results are
  // configured-model + template guesses and would mislead the pick.
  const enumForProvider = enumState !== null && enumState.name === value.provider_name ? enumState : null
  const liveModels = enumForProvider?.status === 'ready' && enumForProvider.models?.source === 'live'
    ? enumForProvider.models.models
    : []
  const modelSelectValue = customOpen
    ? CUSTOM_VALUE
    : value.model === undefined || value.model === '' || value.model === selected?.model
      ? ''
      : liveModels.includes(value.model)
        ? value.model
        : CUSTOM_VALUE

  return (
    <div className={styles.picker} aria-label="模型选择">
      <label className={styles.field} htmlFor={providerId}>
        <span className={styles.fieldLabel}>Provider</span>
        <select
          id={providerId}
          className={styles.select}
          value={value.provider_name ?? ''}
          disabled={disabled}
          aria-label="Provider"
          onChange={event => { pickProvider(event.target.value) }}
        >
          <option value="">默认</option>
          {providers.map(provider => (
            <option
              key={provider.name}
              value={provider.name}
              disabled={!provider.api_key_configured}
            >
              {provider.name}{provider.model !== null && provider.model !== '' ? ` · ${provider.model}` : ''}
              {provider.api_key_configured ? '' : '（未配置 Key）'}
            </option>
          ))}
          {providerMissing && (
            <option value={value.provider_name}>{value.provider_name}（已不可用）</option>
          )}
        </select>
      </label>
      {showModelControls && !customOpen && (
        <label className={styles.field} htmlFor={modelId}>
          <span className={styles.fieldLabel}>Model</span>
          <select
            id={modelId}
            className={styles.select}
            value={modelSelectValue}
            disabled={disabled || selected === undefined}
            aria-label="Model"
            onChange={event => { pickModel(event.target.value) }}
          >
            <option value="">默认{selected?.model ? ` · ${selected.model}` : ''}</option>
            {liveModels
              .filter(model => model !== selected?.model)
              .map(model => <option key={model} value={model}>{model}</option>)}
            {value.model !== undefined && value.model !== '' && value.model !== selected?.model
              && !liveModels.includes(value.model) && (
              <option value={CUSTOM_VALUE}>{value.model}</option>
            )}
            <option value={CUSTOM_VALUE}>自定义…</option>
          </select>
        </label>
      )}
      {enumForProvider?.status === 'loading' && (
        <span className={styles.hint}>枚举模型中…</span>
      )}
      {enumForProvider?.status === 'error' && (
        <span className={styles.hint}>在线枚举失败，可手动输入 model id</span>
      )}
      {enumForProvider?.status === 'ready' && enumForProvider.models?.source === 'fallback' && (
        <span className={styles.hint}>在线枚举不可用，可手动输入 model id</span>
      )}
      {customOpen && (
        <input
          ref={customInputRef}
          className={styles.customInput}
          type="text"
          value={customDraft}
          disabled={disabled}
          placeholder="输入 model id，如 claude-sonnet-4"
          aria-label="自定义 model id"
          onChange={event => { setCustomDraft(event.target.value) }}
          onBlur={commitCustom}
          onKeyDown={event => {
            if (event.key === 'Enter') {
              event.preventDefault()
              commitCustom()
            } else if (event.key === 'Escape') {
              event.preventDefault()
              event.stopPropagation()
              setCustomOpen(false)
              setCustomDraft('')
            }
          }}
        />
      )}
    </div>
  )
}
