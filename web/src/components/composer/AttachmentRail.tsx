import { useRef, useState } from 'react'

import { ImageLightbox } from '../ImageLightbox'
import styles from './AttachmentRail.module.css'

export type AttachmentDraft = {
  id: string
  name: string
  size: number
  mime: string
  blobUrl: string
}

function isImage(attachment: AttachmentDraft): boolean {
  return attachment.mime.startsWith('image/')
}

export function formatAttachmentSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export function AttachmentRail({
  attachments,
  onRemove,
}: {
  attachments: AttachmentDraft[]
  onRemove: (id: string) => void
}) {
  const [previewId, setPreviewId] = useState<string | null>(null)
  const previewTriggerRef = useRef<HTMLButtonElement | null>(null)
  const preview = attachments.find(attachment => attachment.id === previewId) ?? null

  if (attachments.length === 0) return null

  return (
    <div className={styles.rail} aria-label="附件">
      {attachments.map(attachment => (
        <div key={attachment.id} className={isImage(attachment) ? styles.thumb : styles.fileCard}>
          {isImage(attachment) ? (
            <button
              type="button"
              className={styles.thumbButton}
              aria-label={`预览 ${attachment.name}`}
              onClick={event => {
                previewTriggerRef.current = event.currentTarget
                setPreviewId(attachment.id)
              }}
            >
              <img src={attachment.blobUrl} alt={attachment.name} />
            </button>
          ) : (
            <div className={styles.fileBody}>
              <span className={styles.fileIcon} aria-hidden>📄</span>
              <div className={styles.fileMeta}>
                <span className={styles.fileName}>{attachment.name}</span>
                <small>{attachment.mime || '文件'} · {formatAttachmentSize(attachment.size)}</small>
              </div>
            </div>
          )}
          <button
            type="button"
            className={styles.remove}
            aria-label={`移除附件 ${attachment.name}`}
            onClick={() => {
              if (previewId === attachment.id) setPreviewId(null)
              onRemove(attachment.id)
            }}
          >
            ×
          </button>
        </div>
      ))}
      {preview !== null && (
        <ImageLightbox
          filename={preview.name}
          mediaType={preview.mime}
          toolName="attachment"
          url={preview.blobUrl}
          onClose={() => { setPreviewId(null) }}
          returnFocusRef={previewTriggerRef}
        />
      )}
    </div>
  )
}
