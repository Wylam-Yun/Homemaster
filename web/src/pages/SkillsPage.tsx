import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { HttpError, type SkillEvolutionToggleResult, type SkillInfo, type SkillIssueInfo } from '../api/http'
import { useToast } from '../components/toast'
import { PageHeader } from '../components/ui/PageHeader'
import { IconSearch } from '../components/ui/icons'
import styles from './SkillsPage.module.css'

/** Structural subset of HomeMasterApi this page consumes (contract: /api/skills*). */
export type SkillsApi = {
  listSkills(): Promise<{ skills: SkillInfo[]; issues?: SkillIssueInfo[] }>
  importSkill(
    files: Array<{ path: string; content: string }>,
    overwrite?: boolean,
  ): Promise<{ status: string; skill: SkillInfo }>
  deleteSkill(name: string): Promise<{ deleted?: boolean; resurrected?: boolean; message?: string }>
  setSkillEvolution(name: string, enabled: boolean): Promise<SkillEvolutionToggleResult>
}

type FilterMode = 'all' | 'invocable' | 'auto' | 'user'

const SOURCE_LABELS: Record<string, string> = {
  builtin: '内置技能',
  bundled: '内置技能',
  project: '项目技能',
  user: '自定义技能',
}

async function collectFiles(list: FileList | File[]): Promise<Array<{ path: string; content: string }>> {
  return Promise.all(
    Array.from(list).map(async file => ({
      path: file.webkitRelativePath !== '' ? file.webkitRelativePath : file.name,
      content: await file.text(),
    })),
  )
}

export function SkillsPage({
  api,
  onUseSkill,
}: {
  api: SkillsApi
  /** Optional callback to jump into the chat composer with `/command ` prefilled. */
  onUseSkill?: (commandName: string) => void
}) {
  const toast = useToast()
  const [skills, setSkills] = useState<SkillInfo[] | null>(null)
  const [issues, setIssues] = useState<SkillIssueInfo[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [toggling, setToggling] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<FilterMode>('all')
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
      const result = await api.deleteSkill(skill.name)
      setConfirmDelete(null)
      if (result.resurrected === true) {
        toast.show({
          title: `已删除技能「${skill.name}」`,
          description: result.message ?? '同名技能仍从其他用户技能目录加载。',
        })
      } else {
        toast.show({ title: `已删除技能「${skill.name}」` })
      }
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

  const toggleEvolution = useCallback(async (skill: SkillInfo) => {
    const next = skill.evolution_enabled !== true
    setToggling(skill.name)
    try {
      const result = await api.setSkillEvolution(skill.name, next)
      setSkills(current =>
        current === null
          ? current
          : current.map(item =>
              item.name === skill.name
                ? {
                    ...item,
                    evolution_enabled: result.evolution_enabled,
                    evolution_cloud_skill_id: result.evolution_cloud_skill_id,
                  }
                : item,
            ),
      )
      toast.show({
        title: next ? `已开启「${skill.name}」进化` : `已关闭「${skill.name}」进化`,
        description:
          next && result.evolution_cloud_skill_id !== null
            ? `已注册云端技能 ${result.evolution_cloud_skill_id}`
            : undefined,
      })
    } catch (cause) {
      toast.show({
        title: `切换「${skill.name}」进化开关失败`,
        description: cause instanceof Error ? cause.message : undefined,
      })
    } finally {
      setToggling(null)
    }
  }, [api, toast])

  const filteredSkills = useMemo(() => {
    const list = skills ?? []
    const needle = query.trim().toLowerCase()
    return list.filter(skill => {
      if (filter === 'invocable' && !skill.user_invocable) return false
      if (filter === 'auto' && skill.user_invocable) return false
      if (filter === 'user' && skill.deletable !== true && skill.source !== 'user') return false
      if (needle === '') return true
      const text = `${skill.name} ${skill.command_name ?? ''} ${skill.description} ${skill.source}`.toLowerCase()
      return text.includes(needle)
    })
  }, [filter, query, skills])

  return (
    <section className={styles.page} aria-labelledby="skills-page-title">
      <PageHeader
        eyebrow="能力扩展"
        title="技能"
        titleId="skills-page-title"
        subtitle={<>技能（Skills）为智能助手提供标准化的工作流与专业领域指令。带有 <code>/命令</code> 标识的技能可在对话输入框直接输入斜杠调用；其余技能由助手根据任务需要自动加载。</>}
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
            className={styles.primary}
            onClick={() => { fileInputRef.current?.click() }}
            disabled={importing}
          >
            {importing ? '导入中…' : '＋ 导入技能文件夹'}
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

      {skills !== null && skills.length > 0 && (
        <div className={styles.toolbar}>
          <div className={styles.tabs} role="tablist" aria-label="技能筛选">
            <button type="button" role="tab" aria-selected={filter === 'all'} onClick={() => { setFilter('all') }}>
              全部 ({skills.length})
            </button>
            <button type="button" role="tab" aria-selected={filter === 'invocable'} onClick={() => { setFilter('invocable') }}>
              可输入 /命令 调用 ({skills.filter(s => s.user_invocable).length})
            </button>
            <button type="button" role="tab" aria-selected={filter === 'auto'} onClick={() => { setFilter('auto') }}>
              助手自动调用 ({skills.filter(s => !s.user_invocable).length})
            </button>
            <button type="button" role="tab" aria-selected={filter === 'user'} onClick={() => { setFilter('user') }}>
              自定义导入 ({skills.filter(s => s.deletable === true || s.source === 'user').length})
            </button>
          </div>
          <label className={styles.search}>
            <span aria-hidden="true"><IconSearch size={14} /></span>
            <input
              type="search"
              value={query}
              onChange={event => { setQuery(event.target.value) }}
              placeholder="搜索技能名称或功能描述…"
              aria-label="搜索技能"
            />
          </label>
        </div>
      )}

      <div className={styles.list}>
        {loading && skills === null && <div className={styles.empty}>正在读取技能…</div>}
        {!loading && error === null && skills !== null && skills.length === 0 && (
          <div className={styles.empty}>未发现可用技能</div>
        )}
        {!loading && error === null && skills !== null && skills.length > 0 && filteredSkills.length === 0 && (
          <div className={styles.empty}>没有匹配的技能</div>
        )}
        {filteredSkills.map(skill => {
          const sourceText = SOURCE_LABELS[skill.source]
          return (
            <article className={styles.card} key={`${skill.source}:${skill.name}`}>
              <div className={styles.cardTop}>
                <strong>{skill.name}</strong>
                {skill.user_invocable && skill.command_name !== null && skill.command_name !== '' && (
                  <code className={styles.command}>/{skill.command_name}</code>
                )}
                <span className={styles.badges}>
                  {sourceText !== undefined && <span className={styles.sourceHuman}>{sourceText}</span>}
                  <span className={styles.sourceBadge}>{skill.source}</span>
                  {!skill.user_invocable && <span className={styles.muteBadge} title="该技能由智能助手在推理过程中自动按需调用">不可直接调用</span>}
                  {skill.evolution_enabled === true && (
                    <span
                      className={styles.evolveBadge}
                      title={`已注册云端技能 ${skill.evolution_cloud_skill_id ?? ''}，sealed trace 达到 8 条后自动进化`}
                    >可进化</span>
                  )}
                </span>
              </div>
              <p className={styles.description}>{skill.description || '（无描述）'}</p>
              <div className={styles.actions}>
                {onUseSkill !== undefined && skill.user_invocable && skill.command_name !== null && skill.command_name !== '' && (
                  <button
                    type="button"
                    className={styles.useButton}
                    onClick={() => { onUseSkill(skill.command_name!) }}
                  >
                    在对话中使用 /{skill.command_name} →
                  </button>
                )}
                <button
                  type="button"
                  className={skill.evolution_enabled === true ? styles.evolveOn : styles.evolveOff}
                  disabled={toggling === skill.name}
                  aria-pressed={skill.evolution_enabled === true}
                  title={skill.evolution_enabled === true
                    ? '已注册云端，点击关闭（保留云端注册）'
                    : '开启后注册到 MindMemOS，攒满 8 条 sealed trace 自动进化'}
                  onClick={() => { void toggleEvolution(skill) }}
                >
                  {toggling === skill.name
                    ? '切换中…'
                    : skill.evolution_enabled === true
                      ? '进化：开'
                      : '进化：关'}
                </button>
                {skill.deletable === true && (
                  <button
                    type="button"
                    className={styles.danger}
                    onClick={() => {
                      setConfirmDelete(current => (current === skill.name ? null : skill.name))
                    }}
                  >删除</button>
                )}
              </div>
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
          )
        })}
      </div>
    </section>
  )
}
