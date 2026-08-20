import type { ToolCallState } from '../state/conversation'
import styles from './ToolCallCard.module.css'

export function ToolCallCard({ tool, sessionId }: { tool: ToolCallState; sessionId?: string }) {
  return (
    <article className={styles.card} data-state={tool.status}>
      <header>
        <span className={styles.dot} aria-hidden />
        <strong>{tool.name}</strong>
        <span className={styles.status}>{tool.status}</span>
      </header>
      <details>
        <summary>Arguments</summary>
        <pre>{JSON.stringify(tool.arguments, null, 2)}</pre>
      </details>
      {tool.output && <pre className={styles.output}>{tool.output}</pre>}
      {tool.artifacts.length > 0 && (
        <ul className={styles.artifacts} aria-label="Artifacts">
          {tool.artifacts.map(artifact => (
            <li key={artifact.artifact_handle}>
              {sessionId === undefined ? <span>{artifact.filename}</span> : (
                <a href={`/api/artifacts/${encodeURIComponent(artifact.artifact_handle)}?session_id=${encodeURIComponent(sessionId)}&run_id=${encodeURIComponent(artifact.run_id)}`}>
                  {artifact.filename}
                </a>
              )}
              <small>{artifact.media_type}</small>
            </li>
          ))}
        </ul>
      )}
    </article>
  )
}
