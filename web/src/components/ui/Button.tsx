import type { ButtonHTMLAttributes, ReactNode } from 'react'

import styles from './ui.module.css'

export type ButtonVariant = 'default' | 'primary' | 'danger' | 'ghost'

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  default: '',
  primary: styles.buttonPrimary,
  danger: styles.buttonDanger,
  ghost: styles.buttonGhost,
}

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  children: ReactNode
}

export function Button({ variant = 'default', className = '', children, ...rest }: Props) {
  const classes = [styles.button, VARIANT_CLASS[variant], className].filter(Boolean).join(' ')
  return <button type="button" className={classes} {...rest}>{children}</button>
}
