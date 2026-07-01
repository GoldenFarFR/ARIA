export interface ChainMeta {
  id: string
  label: string
  shortLabel: string
  color: string
  bgClass: string
  borderClass: string
  textClass: string
}

export const CHAINS: Record<string, ChainMeta> = {
  solana: {
    id: 'solana',
    label: 'Solana',
    shortLabel: 'SOL',
    color: '#9945FF',
    bgClass: 'bg-violet-500/15',
    borderClass: 'border-violet-500/30',
    textClass: 'text-violet-300',
  },
  ethereum: {
    id: 'ethereum',
    label: 'Ethereum',
    shortLabel: 'ETH',
    color: '#627EEA',
    bgClass: 'bg-indigo-500/15',
    borderClass: 'border-indigo-500/30',
    textClass: 'text-indigo-300',
  },
  base: {
    id: 'base',
    label: 'Base',
    shortLabel: 'BASE',
    color: '#0052FF',
    bgClass: 'bg-blue-500/15',
    borderClass: 'border-blue-500/30',
    textClass: 'text-blue-300',
  },
  bsc: {
    id: 'bsc',
    label: 'BNB Chain',
    shortLabel: 'BSC',
    color: '#F0B90B',
    bgClass: 'bg-amber-500/15',
    borderClass: 'border-amber-500/30',
    textClass: 'text-amber-300',
  },
  arbitrum: {
    id: 'arbitrum',
    label: 'Arbitrum',
    shortLabel: 'ARB',
    color: '#28A0F0',
    bgClass: 'bg-sky-500/15',
    borderClass: 'border-sky-500/30',
    textClass: 'text-sky-300',
  },
  polygon: {
    id: 'polygon',
    label: 'Polygon',
    shortLabel: 'POL',
    color: '#8247E5',
    bgClass: 'bg-purple-500/15',
    borderClass: 'border-purple-500/30',
    textClass: 'text-purple-300',
  },
  avalanche: {
    id: 'avalanche',
    label: 'Avalanche',
    shortLabel: 'AVAX',
    color: '#E84142',
    bgClass: 'bg-red-500/15',
    borderClass: 'border-red-500/30',
    textClass: 'text-red-300',
  },
  optimism: {
    id: 'optimism',
    label: 'Optimism',
    shortLabel: 'OP',
    color: '#FF0420',
    bgClass: 'bg-rose-500/15',
    borderClass: 'border-rose-500/30',
    textClass: 'text-rose-300',
  },
}

export function getChainMeta(chainId: string): ChainMeta {
  return (
    CHAINS[chainId.toLowerCase()] ?? {
      id: chainId,
      label: chainId.charAt(0).toUpperCase() + chainId.slice(1),
      shortLabel: chainId.slice(0, 4).toUpperCase(),
      color: '#6b7280',
      bgClass: 'bg-gray-500/15',
      borderClass: 'border-gray-500/30',
      textClass: 'text-gray-300',
    }
  )
}

export const SIGNAL_LABELS: Record<string, { label: string; hint: string; emoji: string }> = {
  buy: { label: 'Buy signal', hint: 'Multiple indicators are favorable', emoji: '🟢' },
  sell: { label: 'Avoid', hint: 'Selling pressure detected', emoji: '🔴' },
  watch: { label: 'Watch', hint: 'Wait for confirmation before acting', emoji: '🟡' },
  neutral: { label: 'No clear signal', hint: 'Market is indecisive on this token', emoji: '⚪' },
}

export const TIMEFRAME_LABELS: Record<string, string> = {
  '1m': 'Very short term',
  '5m': 'Short term',
  '15m': 'Intraday',
  '30m': 'Intraday+',
  '1h': 'Few hours',
  '4h': 'Medium term',
  '1d': 'Long term',
}

export function scoreVerdict(score: number): { label: string; color: string } {
  if (score >= 70) return { label: 'Favorable', color: 'text-emerald-400' }
  if (score >= 50) return { label: 'Mixed', color: 'text-amber-400' }
  return { label: 'Unfavorable', color: 'text-red-400' }
}

export function formatPrice(price: number | null | undefined): string {
  if (price == null) return '—'
  if (price < 0.0001) return `$${price.toExponential(2)}`
  if (price < 1) return `$${price.toFixed(6)}`
  return `$${price.toFixed(2)}`
}

export function formatLiquidity(value: number | null | undefined): string {
  return formatCompactUsd(value)
}

export function formatCompactUsd(value: number | null | undefined): string {
  if (value == null) return '—'
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}K`
  return `$${value.toFixed(0)}`
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null) return '—'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

export function percentColor(value: number | null | undefined): string {
  if (value == null) return 'text-gray-500'
  if (value > 0) return 'text-emerald-400'
  if (value < 0) return 'text-red-400'
  return 'text-gray-400'
}

export function formatPairAge(ms: number | null | undefined): string {
  if (!ms) return '—'
  const diff = Date.now() - ms
  const mins = Math.floor(diff / 60_000)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 48) return `${hours}h`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d`
  const months = Math.floor(days / 30)
  return `${months}mo`
}

export function formatTxns(buys: number, sells: number): string {
  return `${buys} / ${sells}`
}