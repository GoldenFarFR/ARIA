import { Bell, ChevronDown, Search, Star, Zap } from 'lucide-react'
import { useState } from 'react'
import { AlertsPanel } from './AlertsPanel'
import { MarketFeedPanel } from './MarketFeedPanel'
import { WatchlistPanel } from './WatchlistPanel'
import type { Alert, MarketFeedType, PairSummary, WatchlistItem } from '../types'

interface WatchlistHomeProps {
  watchlist: WatchlistItem[]
  alerts: Alert[]
  connected: boolean
  onWatchlistSelect: (item: WatchlistItem) => void
  onWatchlistRemove: (item: WatchlistItem) => void
  discoverOpen: boolean
  onDiscoverOpenChange: (open: boolean) => void
  marketFeed: MarketFeedType
  onFeedChange: (feed: MarketFeedType) => void
  marketPairs: PairSummary[]
  marketLoading: boolean
  activeChain: string | null
  onChainSelect: (chainId: string | null) => void
  onPairSelect: (pair: PairSummary) => void
  onQuickSearch?: (query: string) => void
  marketSource?: string
}

export function WatchlistHome({
  watchlist,
  alerts,
  connected,
  onWatchlistSelect,
  onWatchlistRemove,
  discoverOpen,
  onDiscoverOpenChange,
  marketFeed,
  onFeedChange,
  marketPairs,
  marketLoading,
  activeChain,
  onChainSelect,
  onPairSelect,
  onQuickSearch,
  marketSource,
}: WatchlistHomeProps) {
  const [discoverExpanded, setDiscoverExpanded] = useState(discoverOpen)

  const toggleDiscover = () => {
    const next = !discoverExpanded
    setDiscoverExpanded(next)
    onDiscoverOpenChange(next)
  }

  return (
    <div className="space-y-4">
      <section className="pixel-panel px-4 py-3 flex items-center gap-3">
        <Zap className="w-5 h-5 text-accent shrink-0" />
        <p className="text-sm text-terminal/60 font-terminal leading-snug">
          <span className="text-terminal">Favorites + signals.</span> Search → analyze → tap the star.
        </p>
      </section>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <WatchlistPanel
          items={watchlist}
          onSelect={onWatchlistSelect}
          onRemove={onWatchlistRemove}
          primary
        />
        <AlertsPanel alerts={alerts} connected={connected} compact />
      </div>

      <section className="pixel-panel overflow-hidden">
        <button
          type="button"
          onClick={toggleDiscover}
          className="w-full px-4 py-3 flex items-center justify-between gap-3 hover:bg-panel-elevated transition-colors text-left"
        >
          <div>
            <p className="pixel-label">Discover markets</p>
            <p className="text-xs text-terminal/45 font-terminal mt-0.5">Optional — loads on demand</p>
          </div>
          <ChevronDown
            className={`w-4 h-4 text-terminal/40 transition-transform ${discoverExpanded ? 'rotate-180' : ''}`}
          />
        </button>
        {discoverExpanded && (
          <div className="border-t-2 border-border p-3 space-y-3">
            <div className="flex flex-wrap gap-2 text-xs font-terminal text-terminal/45">
              <span className="flex items-center gap-1"><Search className="w-3 h-3 text-accent" /> or browse below</span>
              <span className="flex items-center gap-1"><Star className="w-3 h-3 text-watch" /> then favorite</span>
              <span className="flex items-center gap-1"><Bell className="w-3 h-3 text-accent" /> get alerts</span>
            </div>
            <MarketFeedPanel
              feed={marketFeed}
              onFeedChange={onFeedChange}
              pairs={marketPairs}
              loading={marketLoading}
              activeChain={activeChain}
              onChainSelect={onChainSelect}
              onPairSelect={onPairSelect}
              onQuickSearch={onQuickSearch}
              source={marketSource}
            />
          </div>
        )}
      </section>
    </div>
  )
}