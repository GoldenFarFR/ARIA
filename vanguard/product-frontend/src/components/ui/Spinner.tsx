import { cn } from '../../lib/cn'

interface SpinnerProps {
  className?: string
  size?: 'sm' | 'md'
}

export function Spinner({ className, size = 'md' }: SpinnerProps) {
  return (
    <div
      className={cn(
        'border-2 border-accent/30 border-t-accent animate-spin',
        size === 'sm' ? 'w-4 h-4' : 'w-6 h-6',
        className,
      )}
      role="status"
      aria-label="Loading"
    />
  )
}