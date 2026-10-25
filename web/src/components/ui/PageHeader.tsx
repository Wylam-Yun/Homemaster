import type { ReactNode } from 'react'

import styles from './ui.module.css'

type Props = {
  eyebrow: string
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  /** id for the section aria-labelledby hook. */
  titleId?: string
}

export function PageHeader({ eyebrow, title, subtitle, actions, titleId }: Props) {
  return (
    <div className={styles.pageHeader}>
      <div>
        <p className={styles.eyebrow}>{eyebrow}</p>
        <h1 id={titleId}>{title}</h1>
        {subtitle !== undefined && <p className={styles.subtitle}>{subtitle}</p>}
      </div>
      {actions !== undefined && <div className={styles.actions}>{actions}</div>}
    </div>
  )
}
