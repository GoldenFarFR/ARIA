import { Flame, Search, Sparkles, TrendingDown, TrendingUp } from 'lucide-react'
import { ChainBadge } from './ChainBadge'
import { Panel } from './ui/Panel'
import { cn } from '../lib/cn'
import {
  formatCompactUsd,
  formatPairAge,
  formatPercent,
  formatPrice,
  formatTxns,
  getChainMeta,
  percentColor,
} from '../lib/chains'
import type { MarketFeedType, PairSummary } from '../types'

const FEED_TABS: { id: MarketFeedType; label: string; icon: typeof Flame }[] = [
  { id: 'trending', label: 'Trending', icon: Flame },
  { id: 'new', label: 'New', icon: Sparkles },
  { id: 'gainers', label: 'Gainers', icon: TrendingUp },
  { id: 'losers', label: 'Losers', icon: TrendingDown },
]

const QUICK_PICKS = ['PEPE', 'WIF', 'BONK', 'DOGE']

interface MarketFeedPanelProps {
  feed: MarketFeedType
  onFeedChange: (feed: MarketFeedType) => void
  pairs: PairSummary[]
  loading: boolean
  activeChain: string | null
  onChainSelect: (chainId: string | null) => void
  onPairSelect: (pair: PairSummary) => void
  onQuickSearch?: (query: string) => void
  source?: string
}

function PairRow({
  pair,
  onSelect,
  compact,
}: {
  pair: PairSummary
  onSelect: (pair: PairSummary) => void
  compact?: boolean
}) {
  const txns = pair.txns?.h24

  if (compact) {
    return (
      <button
        type="button"
        onClick={() => onSelect(pair)}
        className="w-full text-left p-4 border-b border-border/60 hover:bg-panel-elevated transition-colors focus-ring"
      >
        <div className="flex items-center gap-3">
          {pair.image_url ? (
            <img src={pair.image_url} alt="" className="w-10 h-10 rounded-full ring-1 ring-border" />
          ) : (
            <div className="w-10 h-10 rounded-full bg-panel-elevated flex items-center justify-center text-xs font-bold text-gray-400">
              {pair.base_token.symbol.slice(0, 2)}
            </div>
          )}
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-gray-100 truncate">
              {pair.base_token.symbol}
              <span className="text-gray-500 font-normal">/{pair.quote_token.symbol}</span>
            </div>
            <div className="flex items-center gap-2 mt-1">
              <ChainBadge chainId={pair.chain_id} size="sm" />
              <span className="text-xs text-gray-500">{formatCompactUsd(pair.volume_h24)} vol</span>
            </div>
          </div>
          <div className="text-right shrink-0">
            <div className="text-sm font-mono text-gray-200">{formatPrice(pair.price_usd)}</div>
            <div className={cn('text-xs font-medium', percentColor(pair.price_change_h24))}>
              {formatPercent(pair.price_change_h24)}
            </div>
          </div>
        </div>
      </button>
    )
  }

  return (
    <tr
      onClick={() => onSelect(pair)}
      className="border-b border-border/60 hover:bg-panel-elevated cursor-pointer transition-colors"
    >
      <td className="px-4 py-3">
        <div className="flex items-center gap-2 min-w-[160px]">
          {pair.image_url ? (
            <img src={pair.image_url} alt="" className="w-8 h-8 rounded-full" />
          ) : (
            <div className="w-8 h-8 rounded-full bg-panel-elevated flex items-center justify-center text-xs font-bold text-gray-400">
              {pair.base_token.symbol.slice(0, 2)}
            </div>
          )}
          <div>
            <div className="font-semibold text-gray-100">
              {pair.base_token.symbol}
              <span className="text-gray-500 font-normal">/{pair.quote_token.symbol}</span>
            </div>
            <div className="flex items-center gap-1.5 mt-0.5">
              <ChainBadge chainId={pair.chain_id} size="sm" />
              <span className="text-[10px] text-gray-600">{pair.dex_id}</span>
            </div>
          </div>
        </div>
      </td>
      <td className="px-3 py-3 text-gray-200 whitespace-nowrap font-mono text-sm">
        {formatPrice(pair.price_usd)}
      </td>
      <td className="px-3 py-3 text-gray-400 whitespace-nowrap">{formatPairAge(pair.pair_created_at)}</td>
      <td className="px-3 py-3 text-gray-400 whitespace-nowrap">
        {txns ? formatTxns(txns.buys, txns.sells) : '—'}
      </td>
      <td className="px-3 py-3 text-gray-300 whitespace-nowrap">{formatCompactUsd(pair.volume_h24)}</td>
      <td className="px-3 py-3 text-gray-300 whitespace-nowrap">{formatCompactUsd(pair.liquidity_usd)}</td>
      <td className="px-3 py-3 text-gray-300 whitespace-nowrap">{formatCompactUsd(pair.market_cap)}</td>
      <td className={cn('px-3 py-3 whitespace-nowrap', percentColor(pair.price_change_m5))}>
        {formatPercent(pair.price_change_m5)}
      </td>
      <td className={cn('px-3 py-3 whitespace-nowrap', percentColor(pair.price_change_h1))}>
        {formatPercent(pair.price_change_h1)}
      </td>
      <td className={cn('px-3 py-3 whitespace-nowrap', percentColor(pair.price_change_h6))}>
        {formatPercent(pair.price_change_h6)}
      </td>
      <td className={cn('px-3 py-3 whitespace-nowrap', percentColor(pair.price_change_h24))}>
        {formatPercent(pair.price_change_h24)}
      </td>
    </tr>
  )
}

function FeedSkeleton() {
  return (
    <div className="divide-y divide-border/60">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="px-4 py-4 flex items-center gap-3">
          <div className="w-10 h-10 rounded-full skeleton shrink-0" />
          <div className="flex-1 space-y-2">
            <div className="h-4 w-32 rounded skeleton" />
            <div className="h-3 w-24 rounded skeleton" />
          </div>
          <div className="h-4 w-16 rounded skeleton" />
        </div>
      ))}
    </div>
  )
}

export function MarketFeedPanel({
  feed,
  onFeedChange,
  pairs,
  loading,
  activeChain,
  onChainSelect,
  onPairSelect,
  onQuickSearch,
  source,
}: MarketFeedPanelProps) {
  const chains = Array.from(new Set(pairs.map((p) => p.chain_id))).sort()

  return (
    <div className="space-y-6">
      <section className="pixel-panel p-6 md:p-8 relative overflow-hidden">
        <div className="relative max-w-2xl">
          <p className="pixel-label mb-3">
            &gt; real-time dex intelligence
          </p>
          <h2 className="font-display text-xs md:text-sm text-terminal mb-4 leading-relaxed">
            CLEAR VERDICTS
            <br />
            <span className="text-accent">7 TIMEFRAMES</span>
          </h2>
          <p className="text-lg text-[#9a9890] leading-relaxed mb-5 font-terminal">
            pick a pair from the live feed or search by symbol. buy, watch, or avoid — no jargon.
          </p>
          {onQuickSearch && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-base text-[#6a6a72] flex items-center gap-1 font-terminal">
                <Search className="w-4 h-4" />
                quick:
              </span>
              {QUICK_PICKS.map((symbol) => (
                <button
                  key={symbol}
                  type="button"
                  onClick={() => onQuickSearch(symbol)}
                  className="pixel-btn px-3 py-1 text-base focus-ring"
                >
                  {symbol}
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      <div className="flex flex-wrap gap-2 items-center">
        {FEED_TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => onFeedChange(id)}
            className={cn(
              'flex items-center gap-2 px-4 py-2 text-base focus-ring',
              feed === id ? 'pixel-btn pixel-btn-active' : 'pixel-btn',
            )}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
        {source && (
          <span className="ml-auto text-xs text-gray-600 self-center">
            {source === 'index' ? 'Cached index' : 'Live · DexScreener'}
          </span>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => onChainSelect(null)}
          className={cn(
            'px-3 py-1.5 text-base focus-ring',
            activeChain === null ? 'pixel-btn pixel-btn-active' : 'pixel-btn',
          )}
        >
          All chains
        </button>
        {chains.map((chainId) => {
          const meta = getChainMeta(chainId)
          return (
            <button
              key={chainId}
              onClick={() => onChainSelect(chainId)}
              className={cn(
                'px-3 py-1.5 text-base focus-ring',
                activeChain === chainId ? 'pixel-btn pixel-btn-active' : 'pixel-btn',
              )}
            >
              {meta.label}
            </button>
          )
        })}
      </div>

      <Panel>
        {loading ? (
          <FeedSkeleton />
        ) : pairs.length === 0 ? (
          <div className="p-10 text-center text-sm text-gray-500">No pairs for this filter.</div>
        ) : (
          <>
            <div className="md:hidden divide-y divide-border/60">
              {pairs.map((pair) => (
                <PairRow key={`${pair.chain_id}-${pair.pair_address}`} pair={pair} onSelect={onPairSelect} compact />
              ))}
            </div>
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full min-w-[900px] text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b border-border">
                    <th className="px-4 py-3 font-medium">Token</th>
                    <th className="px-3 py-3 font-medium">Price</th>
                    <th className="px-3 py-3 font-medium">Age</th>
                    <th className="px-3 py-3 font-medium">Txns 24h</th>
                    <th className="px-3 py-3 font-medium">Volume</th>
                    <th className="px-3 py-3 font-medium">Liquidity</th>
                    <th className="px-3 py-3 font-medium">MCAP</th>
                    <th className="px-3 py-3 font-medium">5M</th>
                    <th className="px-3 py-3 font-medium">1H</th>
                    <th className="px-3 py-3 font-medium">6H</th>
                    <th className="px-3 py-3 font-medium">24H</th>
                  </tr>
                </thead>
                <tbody>
                  {pairs.map((pair) => (
                    <PairRow
                      key={`${pair.chain_id}-${pair.pair_address}`}
                      pair={pair}
                      onSelect={onPairSelect}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Panel>
    </div>
  )
}