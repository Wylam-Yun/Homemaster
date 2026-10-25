import { useCallback, useEffect, useState } from 'react'

import type { SkillInfo, SkillIssueInfo } from '../api/http'
import styles from './SkillsPage.module.css'

/** Structural subset of HomeMasterApi this page consumes (contract: GET /api/skills). */
export type SkillsApi = {
  listSkills(): Promise<{ skills: SkillInfo[]; issues?: SkillIssueInfo[] }>
}

export function SkillsPage({ api }: { api: SkillsApi }) {
  const [skills, setSkills] = useState<SkillInfo[] | null>(null)
  const [issues, setIssues] = useState<SkillIssueInfo[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const listed = await api.listSkills()
      setSkills(listed.skills)
      setIssues(listed.issues ?? [])
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '技能列表加载失败。')
    } finally {
      setLoading(false)
    }
  }, [api])

  useEffect(() => { void refresh() }, [refresh])

  return (
    <section className={styles.page} aria-labelledby="skills-page-title">
      <div className={styles.headingRow}>
        <div>
          <p className={styles.eyebrow}>能力</p>
          <h1 id="skills-page-title">技能</h1>
          <p>已加载的技能列表，只读查看。带 <code>/命令</code> 的技能可在输入框里用斜杠唤起。</p>
        </div>
        <button type="button" className={styles.refresh} onClick={() => { void refresh() }} disabled={loading}>
          {loading ? '加载中…' : '刷新'}
        </button>
      </div>

      {error !== null && (
        <div className={styles.error} role="alert">
          <div><strong>技能列表加载失败</strong><span>{error}</span></div>
          <button type="button" onClick={() => { void refresh() }}>重新加载</button>
        </div>
      )}

      {issues.length > 0 && (
        <div className={styles.issues} role="alert">
          <strong>{issues.length} 个技能加载失败</strong>
          <ul>
            {issues.map((issue, index) => (
              <li key={`${issue.source}:${issue.path ?? index}`}>
                <code>{issue.path ?? issue.source}</code> — {issue.code}: {issue.detail}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className={styles.list}>
        {loading && skills === null && <div className={styles.empty}>正在读取技能…</div>}
        {!loading && error === null && skills !== null && skills.length === 0 && (
          <div className={styles.empty}>未发现可用技能</div>
        )}
        {(skills ?? []).map(skill => (
          <article className={styles.card} key={`${skill.source}:${skill.name}`}>
            <div className={styles.cardTop}>
              <strong>{skill.name}</strong>
              {skill.user_invocable && skill.command_name !== null && skill.command_name !== '' && (
                <code className={styles.command}>/{skill.command_name}</code>
              )}
              <span className={styles.badges}>
                <span className={styles.sourceBadge}>{skill.source}</span>
                {!skill.enabled && <span className={styles.offBadge}>已停用</span>}
                {!skill.user_invocable && <span className={styles.muteBadge}>不可直接调用</span>}
              </span>
            </div>
            <p className={styles.description}>{skill.description || '（无描述）'}</p>
          </article>
        ))}
      </div>
    </section>
  )
}
