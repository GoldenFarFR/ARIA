import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

interface PanelProps {
  children: ReactNode
  className?: string
  padding?: boolean
}

interface PanelHeaderProps {
  children: ReactNode
  className?: string
  action?: ReactNode
}

export function Panel({ children, className, padding = false }: PanelProps) {
  return (
    <div
      className={cn(
        'pixel-panel overflow-hidden',
        padding && 'p-4',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function PanelHeader({ children, className, action }: PanelHeaderProps) {
  return (
    <div
      className={cn(
        'px-4 py-3 border-b-2 border-border-bright flex items-center justify-between gap-3 bg-panel-elevated',
        className,
      )}
    >
      <div className="min-w-0">{children}</div>
      {action}
    </div>
  )
}

export function PanelBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('p-4', className)}>{children}</div>
}