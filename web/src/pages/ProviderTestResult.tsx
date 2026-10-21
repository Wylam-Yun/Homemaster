import type { ProviderErrorKind, ProviderTestResult } from '../api/http'
import styles from './ProviderTestResult.module.css'

/**
 * Per-error_kind remediation hint for the provider probe result — shared by
 * SettingsPage's inline test and OnboardingPage's gate step so both surfaces
 * give the same advice.
 */
export function testErrorAdvice(errorKind: ProviderErrorKind | null): string {
  switch (errorKind) {
    case 'auth':
      return '检查 API key 是否正确、是否有访问该模型的权限。'
    case 'network':
      return '检查 base_url 是否可达、本机网络/代理是否正常。'
    case 'model':
      return '该服务商无此模型名——保存后可用「枚举可用模型」挑一个，或改填正确 model id。'
    case 'schema':
      return '服务有响应但返回格式不符，检查 api_format 是否选对（Anthropic 系 vs OpenAI 系）。'
    default:
      return '原因未分类，请按下方信息排查后重试。'
  }
}

export function ProviderTestResultCard({ result }: { result: ProviderTestResult }) {
  return (
    <div className={styles.card} role="status" data-ok={result.ok}>
      <div className={styles.head}>
        <strong>{result.ok ? '连接测试通过' : '连接测试失败'}</strong>
        {typeof result.latency_ms === 'number' && <span className={styles.latency}>{result.latency_ms} ms</span>}
      </div>
      <span className={styles.message}>{result.message}</span>
      {!result.ok && (
        <span className={styles.advice}>
          {result.error_kind !== null && <em className={styles.kind}>{result.error_kind}</em>}
          {testErrorAdvice(result.error_kind)}
        </span>
      )}
    </div>
  )
}
