import type { ReactNode } from 'react'

import styles from './ui.module.css'

export type BadgeVariant = 'accent' | 'success' | 'warning' | 'danger' | 'muted'

const VARIANT_CLASS: Record<BadgeVariant, string> = {
  accent: styles.badgeAccent,
  success: styles.badgeSuccess,
  warning: styles.badgeWarning,
  danger: styles.badgeDanger,
  muted: styles.badgeMuted,
}

export function Badge({ variant = 'muted', children }: { variant?: BadgeVariant; children: ReactNode }) {
  return <span className={`${styles.badge} ${VARIANT_CLASS[variant]}`}>{children}</span>
}
