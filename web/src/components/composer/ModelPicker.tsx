import { useEffect, useId, useRef, useState } from 'react'

import type { ProviderInfo } from '../../api/http'
import styles from './ModelPicker.module.css'

/**
 * Per-session provider/model pick, sent as SendMessageRequest.provider_name +
 * model. Follows cline's ModelPickerWithManualEntry shape: a provider dropdown
 * plus the configured model or a free-text "custom" model id.
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

export function ModelPicker({
  providers,
  value,
  disabled = false,
  onChange,
}: {
  providers: ProviderInfo[]
  value: ModelSelection
  disabled?: boolean
  onChange: (selection: ModelSelection) => void
}) {
  const providerId = useId()
  const modelId = useId()
  const customInputRef = useRef<HTMLInputElement>(null)
  // The custom-entry editor opens on "自定义…" and stays open while the stored
  // selection keeps a model that no provider default covers.
  const [customOpen, setCustomOpen] = useState(false)
  const [customDraft, setCustomDraft] = useState('')

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
    onChange({ provider_name: value.provider_name })
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
            value={
              customOpen || (value.model !== undefined && value.model !== '' && value.model !== selected?.model)
                ? CUSTOM_VALUE
                : ''
            }
            disabled={disabled || selected === undefined}
            aria-label="Model"
            onChange={event => { pickModel(event.target.value) }}
          >
            <option value="">默认{selected?.model ? ` · ${selected.model}` : ''}</option>
            {value.model !== undefined && value.model !== '' && value.model !== selected?.model && (
              <option value={CUSTOM_VALUE}>{value.model}</option>
            )}
            <option value={CUSTOM_VALUE}>自定义…</option>
          </select>
        </label>
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
