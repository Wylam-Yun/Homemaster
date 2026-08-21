import { useEffect, useId, useRef, useState, type MouseEvent, type RefObject } from 'react'
import { createPortal } from 'react-dom'

import styles from './ImageLightbox.module.css'

export function ImageLightbox({
  filename,
  mediaType,
  toolName,
  url,
  onClose,
  returnFocusRef,
}: {
  filename: string
  mediaType: string
  toolName: string
  url: string
  onClose: () => void
  returnFocusRef: RefObject<HTMLButtonElement | null>
}) {
  const [failed, setFailed] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)
  const titleId = useId()

  useEffect(() => {
    const overflow = document.body.style.overflow
    closeRef.current?.focus()
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = overflow
      returnFocusRef.current?.focus()
    }
  }, [onClose, returnFocusRef])

  const onBackdropClick = (event: MouseEvent<HTMLDivElement>): void => {
    if (event.target === event.currentTarget) onClose()
  }

  return createPortal(
    <div
      className={styles.backdrop}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onClick={onBackdropClick}
    >
      <section className={styles.dialog}>
        <header className={styles.header}>
          <div>
            <h2 id={titleId}>Image preview: {filename}</h2>
            <small>{mediaType}</small>
          </div>
          <button ref={closeRef} type="button" aria-label="Close image preview" onClick={onClose}>×</button>
        </header>
        <div className={styles.imageArea}>
          {failed ? (
            <p className={styles.failure}>
              Preview unavailable. <a href={url}>Open {filename}</a>
            </p>
          ) : (
            <img src={url} alt={`${filename} from ${toolName}`} onError={() => setFailed(true)} />
          )}
        </div>
        <footer className={styles.footer}>
          <span>{filename}</span>
          <a href={url} target="_blank" rel="noreferrer">Open original</a>
        </footer>
      </section>
    </div>,
    document.body,
  )
}
