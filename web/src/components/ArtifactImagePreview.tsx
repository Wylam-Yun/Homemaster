import { useCallback, useRef, useState } from 'react'

import type { ArtifactRef } from '../protocol/events'
import { ImageLightbox } from './ImageLightbox'
import styles from './ArtifactImagePreview.module.css'

export function ArtifactImagePreview({
  artifact,
  toolName,
  url,
}: {
  artifact: ArtifactRef
  toolName: string
  url: string
}) {
  const [failed, setFailed] = useState(false)
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const close = useCallback(() => setOpen(false), [])

  if (failed) {
    return (
      <li className={styles.fallback}>
        <a href={url}>{artifact.filename}</a>
        <small>{artifact.media_type}</small>
      </li>
    )
  }

  return (
    <li className={styles.item}>
      <button
        className={styles.thumbnail}
        ref={triggerRef}
        type="button"
        aria-label={`Enlarge ${artifact.filename}`}
        onClick={() => setOpen(true)}
      >
        <img
          src={url}
          alt={`${artifact.filename} from ${toolName}`}
          onError={() => setFailed(true)}
        />
      </button>
      <div className={styles.metadata}>
        <a href={url}>{artifact.filename}</a>
        <small>Click to enlarge · {artifact.media_type}</small>
      </div>
      {open && (
        <ImageLightbox
          filename={artifact.filename}
          mediaType={artifact.media_type}
          toolName={toolName}
          url={url}
          onClose={close}
          returnFocusRef={triggerRef}
        />
      )}
    </li>
  )
}
