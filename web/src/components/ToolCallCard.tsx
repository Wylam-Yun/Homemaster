import type { ArtifactRef } from '../protocol/events'
import type { ToolCallState } from '../state/conversation'
import { ArtifactImagePreview } from './ArtifactImagePreview'
import styles from './ToolCallCard.module.css'

function artifactUrl(artifact: ArtifactRef, sessionId: string): string {
  return `/api/artifacts/${encodeURIComponent(artifact.artifact_handle)}?session_id=${encodeURIComponent(sessionId)}&run_id=${encodeURIComponent(artifact.run_id)}`
}

const TOOL_STATUS_LABEL: Record<ToolCallState['status'], string> = {
  running: '执行中',
  completed: '已完成',
  failed: '失败',
}

const TOOL_FRIENDLY_LABELS: Record<string, string> = {
  terminal: '执行终端命令',
  bash: '执行终端命令',
  run_shell: '执行终端命令',
  read_file: '读取文件',
  write_file: '写入文件',
  edit_file: '编辑文件',
  search_files: '搜索文件',
  web_search: '网页搜索',
  web_fetch: '获取网页内容',
  mindmemos_search: '检索长期记忆',
  mindmemos_add: '写入长期记忆',
  context_memory: '读写会话记忆',
  load_skill: '加载技能说明',
  ask_user_question: '向你提问确认',
  task_planner: '任务规划',
  robot_manipulate: '家居设备操作',
  observe: '环境观察',
}

export function toolDisplayLabel(name: string): string | null {
  return TOOL_FRIENDLY_LABELS[name] ?? null
}

export function ToolCallCard({
  tool,
  sessionId,
  defaultOpen = false,
}: {
  tool: ToolCallState
  sessionId?: string
  defaultOpen?: boolean
}) {
  const friendly = toolDisplayLabel(tool.name)
  return (
    <article className={styles.card} data-state={tool.status}>
      <header>
        <span className={styles.dot} aria-hidden />
        {friendly !== null && <span className={styles.friendly}>{friendly}</span>}
        <strong>{tool.name}</strong>
        <span className={styles.status}>{TOOL_STATUS_LABEL[tool.status] ?? tool.status}</span>
      </header>
      <details open={defaultOpen}>
        <summary>参数</summary>
        <pre>{JSON.stringify(tool.arguments, null, 2)}</pre>
      </details>
      {tool.output && <pre className={styles.output}>{tool.output}</pre>}
      {tool.artifacts.length > 0 && (
        <ul className={styles.artifacts} aria-label="产物">
          {tool.artifacts.map(artifact => {
            const url = sessionId === undefined ? undefined : artifactUrl(artifact, sessionId)
            if (url !== undefined && artifact.media_type.startsWith('image/')) {
              return <ArtifactImagePreview key={artifact.artifact_handle} artifact={artifact} toolName={tool.name} url={url} />
            }
            return (
              <li className={styles.artifactRow} key={artifact.artifact_handle}>
                {url === undefined ? <span>{artifact.filename}</span> : <a href={url}>{artifact.filename}</a>}
                <small>{artifact.media_type}</small>
              </li>
            )
          })}
        </ul>
      )}
    </article>
  )
}
