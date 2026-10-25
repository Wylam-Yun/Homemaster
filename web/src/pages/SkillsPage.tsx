import { useCallback, useEffect, useRef, useState } from 'react'

import { HttpError, type SkillInfo, type SkillIssueInfo } from '../api/http'
import { useToast } from '../components/toast'
import { PageHeader } from '../components/ui/PageHeader'
import styles from './SkillsPage.module.css'

/** Structural subset of HomeMasterApi this page consumes (contract: /api/skills*). */
export type SkillsApi = {
  listSkills(): Promise<{ skills: SkillInfo[]; issues?: SkillIssueInfo[] }>
  importSkill(
    files: Array<{ path: string; content: string }>,
    overwrite?: boolean,
  ): Promise<{ status: string; skill: SkillInfo }>
  deleteSkill(name: string): Promise<void>
}

async function collectFiles(list: FileList | File[]): Promise<Array<{ path: string; content: string }>> {
  return Promise.all(
    Array.from(list).map(async file => ({
      path: file.webkitRelativePath !== '' ? file.webkitRelativePath : file.name,
      content: await file.text(),
    })),
  )
}

export function SkillsPage({ api }: { api: SkillsApi }) {
  const toast = useToast()
  const [skills, setSkills] = useState<SkillInfo[] | null>(null)
  const [issues, setIssues] = useState<SkillIssueInfo[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

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

  const importFiles = useCallback(async (
    files: Array<{ path: string; content: string }>,
    overwrite: boolean,
  ): Promise<void> => {
    setImporting(true)
    try {
      const result = await api.importSkill(files, overwrite)
      toast.show({ title: `已导入技能「${result.skill.name}」` })
      await refresh()
    } catch (cause) {
      if (cause instanceof HttpError && cause.status === 409 && !overwrite) {
        if (window.confirm('目标技能目录已存在，覆盖导入？')) {
          await importFiles(files, true)
        }
        return
      }
      toast.show({
        title: '导入技能失败',
        description: cause instanceof Error ? cause.message : undefined,
      })
    } finally {
      setImporting(false)
    }
  }, [api, refresh, toast])

  const pickFolder = useCallback((list: FileList | null) => {
    if (list === null || list.length === 0) return
    void collectFiles(list).then(files => importFiles(files, false))
  }, [importFiles])

  const remove = useCallback(async (skill: SkillInfo) => {
    setDeleting(true)
    try {
      await api.deleteSkill(skill.name)
      setConfirmDelete(null)
      toast.show({ title: `已删除技能「${skill.name}」` })
      await refresh()
    } catch (cause) {
      toast.show({
        title: `删除技能「${skill.name}」失败`,
        description: cause instanceof Error ? cause.message : undefined,
      })
    } finally {
      setDeleting(false)
    }
  }, [api, refresh, toast])

  return (
    <section className={styles.page} aria-labelledby="skills-page-title">
      <PageHeader
        eyebrow="能力"
        title="技能"
        titleId="skills-page-title"
        subtitle={<>管理技能目录：导入一个包含 SKILL.md 的文件夹到用户技能目录，或删除用户来源的技能。带 <code>/命令</code> 的技能可在输入框里用斜杠唤起。</>}
        actions={<>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            aria-label="选择技能文件夹"
            {...({ webkitdirectory: '' } as object)}
            onChange={event => {
              const list = event.target.files
              event.target.value = ''
              pickFolder(list)
            }}
          />
          <button
            type="button"
            className={styles.refresh}
            onClick={() => { fileInputRef.current?.click() }}
            disabled={importing}
          >
            {importing ? '导入中…' : '导入技能'}
          </button>
          <button type="button" className={styles.refresh} onClick={() => { void refresh() }} disabled={loading}>
            {loading ? '加载中…' : '刷新'}
          </button>
        </>}
      />

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
            {skill.deletable === true && (
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.danger}
                  onClick={() => {
                    setConfirmDelete(current => (current === skill.name ? null : skill.name))
                  }}
                >删除</button>
              </div>
            )}
            {confirmDelete === skill.name && (
              <div className={styles.confirm} role="alert">
                <span>确认删除「{skill.name}」？整个技能目录将被移除。</span>
                <button
                  type="button"
                  className={styles.danger}
                  disabled={deleting}
                  onClick={() => { void remove(skill) }}
                >{deleting ? '删除中…' : '确认删除'}</button>
                <button type="button" onClick={() => { setConfirmDelete(null) }}>取消</button>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}
