import { getChainMeta } from '../lib/chains'

interface ChainBadgeProps {
  chainId: string
  size?: 'sm' | 'md'
}

export function ChainBadge({ chainId, size = 'sm' }: ChainBadgeProps) {
  const chain = getChainMeta(chainId)
  const sizeClass = size === 'md' ? 'px-3 py-1 text-sm' : 'px-2 py-0.5 text-xs'

  return (
    <span
      className={`inline-flex items-center gap-1.5 border-2 font-terminal font-medium ${sizeClass} ${chain.bgClass} ${chain.borderClass} ${chain.textClass}`}
    >
      <span
        className="w-2 h-2 shrink-0"
        style={{ backgroundColor: chain.color }}
      />
      {chain.label}
    </span>
  )
}