import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Bot, Building2, Star, Zap } from 'lucide-react'
import {
  addToWatchlist,
  analyzePair,
  getAlerts,
  getMarketFeed,
  getPair,
  getWatchlist,
  removeFromWatchlist,
  searchPairs,
} from '../api'
import { AgentPanel } from '../components/AgentPanel'
import { FaqPanel } from '../components/FaqPanel'
import { CorporatePanel } from '../components/CorporatePanel'
import { HoldingFooter } from '../components/HoldingFooter'
import { AlertsPanel } from '../components/AlertsPanel'
import { RepertoirePanel } from '../components/RepertoirePanel'
import { AnalysisPanel } from '../components/AnalysisPanel'
import { ChainBadge } from '../components/ChainBadge'
import { EmbeddedChartPanel } from '../components/EmbeddedChartPanel'
import { SearchBar } from '../components/SearchBar'
import { WatchlistHome } from '../components/WatchlistHome'
import { WatchlistPanel } from '../components/WatchlistPanel'
import { WatchlistStarButton } from '../components/WatchlistStarButton'
import { PairStatsPanel } from '../components/PairStatsPanel'
import { useWebSocket } from '../hooks/useWebSocket'
import { cn } from '../lib/cn'
import { formatPrice, getChainMeta } from '../lib/chains'
import { loadSelectedTimeframes, saveSelectedTimeframes } from '../lib/timeframes'
import type {
  Alert,
  MarketFeedType,
  PairAnalysis,
  PairSummary,
  Timeframe,
  WatchlistItem,
} from '../types'
import { HOLDING_SITE_URL } from '../lib/site'

type AppTab = 'signals' | 'corporate' | 'agent'

const TABS: { id: AppTab; label: string; icon: typeof Star }[] = [
  { id: 'signals', label: 'Watchlist', icon: Star },
  { id: 'corporate', label: 'Corporate', icon: Building2 },
  { id: 'agent', label: 'ARIA', icon: Bot },
]

function tabClass(active: boolean) {
  if (!active) {
    return 'pixel-btn text-[#8a8880] border-border hover:text-terminal'
  }
  return 'pixel-btn pixel-btn-active'
}

export function MarketApp() {
  const [searchResults, setSearchResults] = useState<PairSummary[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [selectedPair, setSelectedPair] = useState<PairSummary | null>(null)
  const [analysis, setAnalysis] = useState<PairAnalysis | null>(null)
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const [selectedTimeframes, setSelectedTimeframes] = useState<Timeframe[]>(loadSelectedTimeframes)
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([])
  const [watchlistBusy, setWatchlistBusy] = useState(false)
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [activeTab, setActiveTab] = useState<AppTab>('signals')
  const [discoverOpen, setDiscoverOpen] = useState(false)
  const [marketFeed, setMarketFeed] = useState<MarketFeedType>('trending')
  const [marketPairs, setMarketPairs] = useState<PairSummary[]>([])
  const [marketLoading, setMarketLoading] = useState(false)
  const [marketSource, setMarketSource] = useState<string>('live')
  const [activeChain, setActiveChain] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const analysisGen = useRef(0)

  const handleNewAlert = useCallback((alert: Alert) => {
    setAlerts((prev) => [alert, ...prev].slice(0, 30))
  }, [])

  const { connected } = useWebSocket(handleNewAlert)

  const showToast = (message: string) => {
    setToast(message)
    window.setTimeout(() => setToast(null), 2800)
  }

  const selectPair = useCallback((pair: PairSummary) => {
    setSearchResults([])
    setSelectedPair(pair)
    setAnalysis(null)
    setAnalysisError(null)
  }, [])

  useEffect(() => {
    if (!selectedPair) return

    const gen = ++analysisGen.current
    const chainId = selectedPair.chain_id
    const pairAddress = selectedPair.pair_address
    const timeframes = [...selectedTimeframes]

    setAnalysisLoading(true)
    setAnalysisError(null)

    ;(async () => {
      try {
        const fullPair = await getPair(chainId, pairAddress).catch(() => selectedPair)
        if (analysisGen.current !== gen) return
        setSelectedPair(fullPair)

        const analysisResult = await analyzePair(chainId, pairAddress, timeframes)
        if (analysisGen.current !== gen) return
        setAnalysis(analysisResult)
      } catch (err) {
        if (analysisGen.current !== gen) return
        const msg = err instanceof Error ? err.message : 'Unknown error'
        setAnalysisError(
          msg.includes('failed') || msg.includes('Analysis')
            ? 'Unable to load analysis. Check your connection or try fewer timeframes.'
            : msg,
        )
      } finally {
        if (analysisGen.current === gen) setAnalysisLoading(false)
      }
    })()
  }, [selectedPair?.chain_id, selectedPair?.pair_address, selectedTimeframes])

  useEffect(() => {
    getWatchlist().then(setWatchlist).catch(console.error)
    getAlerts().then(setAlerts).catch(console.error)
  }, [])

  useEffect(() => {
    if (activeTab !== 'signals' || selectedPair || !discoverOpen) return

    setMarketLoading(true)
    getMarketFeed(marketFeed, activeChain)
      .then((data) => {
        setMarketPairs(data.pairs)
        setMarketSource(data.source)
      })
      .catch(console.error)
      .finally(() => setMarketLoading(false))
  }, [marketFeed, activeChain, discoverOpen, selectedPair, activeTab])

  const handleSearch = async (query: string) => {
    setSearchLoading(true)
    try {
      const results = await searchPairs(query)
      setSearchResults(results)
    } catch (err) {
      console.error(err)
    } finally {
      setSearchLoading(false)
    }
  }

  const handleTimeframesChange = (timeframes: Timeframe[]) => {
    saveSelectedTimeframes(timeframes)
    setSelectedTimeframes(timeframes)
  }

  const handleToggleWatchlist = async () => {
    if (!selectedPair || watchlistBusy) return
    setWatchlistBusy(true)
    try {
      if (isWatched) {
        await removeFromWatchlist(selectedPair.chain_id, selectedPair.pair_address)
        setWatchlist((prev) =>
          prev.filter(
            (w) =>
              !(
                w.chain_id === selectedPair.chain_id &&
                w.pair_address.toLowerCase() === selectedPair.pair_address.toLowerCase()
              ),
          ),
        )
        showToast(`${selectedPair.base_token.symbol} removed from favorites`)
      } else {
        await addToWatchlist(
          selectedPair.chain_id,
          selectedPair.pair_address,
          selectedPair.base_token.symbol,
        )
        const items = await getWatchlist()
        setWatchlist(items)
        showToast(`${selectedPair.base_token.symbol} favorited — ARIA will alert on moves`)
      }
    } catch (err) {
      console.error(err)
      showToast('Could not update favorites — refresh and try again')
    } finally {
      setWatchlistBusy(false)
    }
  }

  const handleRemoveWatchlist = async (item: WatchlistItem) => {
    try {
      await removeFromWatchlist(item.chain_id, item.pair_address)
      setWatchlist((prev) => prev.filter((w) => w.id !== item.id))
      showToast(`${item.symbol} removed from favorites`)
    } catch (err) {
      console.error(err)
      showToast('Could not remove favorite')
    }
  }

  const handleWatchlistSelect = (item: WatchlistItem) => {
    selectPair({
      chain_id: item.chain_id,
      dex_id: '',
      pair_address: item.pair_address,
      url: '',
      base_token: { address: '', name: item.symbol, symbol: item.symbol },
      quote_token: { address: '', name: '', symbol: '' },
      price_usd: null,
      price_change_m5: null,
      price_change_h1: null,
      price_change_h6: null,
      price_change_h24: null,
      volume_h24: null,
      liquidity_usd: null,
      market_cap: null,
      image_url: null,
    })
  }

  const clearSelection = () => {
    analysisGen.current += 1
    setSelectedPair(null)
    setAnalysis(null)
    setAnalysisError(null)
    setAnalysisLoading(false)
  }

  const pairLabel = selectedPair
    ? `${selectedPair.base_token.symbol} · ${getChainMeta(selectedPair.chain_id).label}`
    : undefined

  const isWatched =
    selectedPair != null &&
    watchlist.some(
      (w) =>
        w.chain_id === selectedPair.chain_id &&
        w.pair_address.toLowerCase() === selectedPair.pair_address.toLowerCase(),
    )

  const backControl = (
    <a
      href={HOLDING_SITE_URL}
      className="pixel-btn p-2 shrink-0 focus-ring"
      title="Back to Aria Vanguard ZHC"
      aria-label="Back to holding site"
    >
      <ArrowLeft className="w-4 h-4" />
    </a>
  )

  return (
    <div className="min-h-screen pixel-canvas">
      <header className="sticky top-0 z-30 pixel-header">
        <div className="max-w-7xl mx-auto px-4 py-3">
          <div className="flex items-center gap-3 md:gap-4">
            {backControl}
            <div className="flex items-center gap-3 min-w-0 shrink-0">
              <div className="w-10 h-10 pixel-panel-inset flex items-center justify-center shrink-0">
                <Zap className="w-5 h-5 text-accent" />
              </div>
              <div className="hidden sm:block min-w-0">
                <p className="pixel-label leading-none mb-1">Aria Vanguard ZHC</p>
                <h1 className="font-display text-[11px] text-terminal leading-tight">DEXPULSE</h1>
              </div>
            </div>

            <div className="flex-1 min-w-0 max-w-xl mx-auto">
              <SearchBar
                onSearch={handleSearch}
                results={searchResults}
                loading={searchLoading}
                onSelect={selectPair}
              />
            </div>

            <div className="hidden lg:flex items-center gap-2 text-base text-terminal/50 shrink-0 font-terminal">
              <Star className="w-4 h-4 text-watch" />
              signals · alerts · ARIA
            </div>
          </div>

          <nav
            className="mt-3 flex gap-1.5 overflow-x-auto pb-0.5 -mx-1 px-1 scrollbar-thin"
            aria-label="Main sections"
          >
            {TABS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={cn(
                  'flex items-center gap-2 px-3.5 py-2 text-base whitespace-nowrap focus-ring',
                  tabClass(activeTab === id),
                )}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-5 md:py-6">
        {activeTab === 'corporate' ? (
          <CorporatePanel />
        ) : activeTab === 'agent' ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 md:gap-5">
            <div className="lg:col-span-2">
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 md:gap-5">
                <AgentPanel />
                <FaqPanel />
              </div>
            </div>
            <RepertoirePanel />
          </div>
        ) : !selectedPair ? (
          <WatchlistHome
            watchlist={watchlist}
            alerts={alerts}
            connected={connected}
            onWatchlistSelect={handleWatchlistSelect}
            onWatchlistRemove={handleRemoveWatchlist}
            discoverOpen={discoverOpen}
            onDiscoverOpenChange={setDiscoverOpen}
            marketFeed={marketFeed}
            onFeedChange={setMarketFeed}
            marketPairs={marketPairs}
            marketLoading={marketLoading}
            activeChain={activeChain}
            onChainSelect={setActiveChain}
            onPairSelect={selectPair}
            onQuickSearch={handleSearch}
            marketSource={marketSource}
          />
        ) : (
          <>
            <div className="mb-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                {selectedPair.image_url && (
                  <img
                    src={selectedPair.image_url}
                    alt=""
                    className="w-10 h-10 shrink-0 ring-2 ring-border"
                  />
                )}
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="font-terminal text-xl text-terminal">
                      {selectedPair.base_token.symbol}
                      <span className="text-terminal/50 text-base ml-2">
                        / {selectedPair.quote_token.symbol}
                      </span>
                    </h2>
                    <ChainBadge chainId={selectedPair.chain_id} size="md" />
                  </div>
                  <p className="text-xs text-terminal/50 mt-0.5 font-mono">
                    {selectedPair.dex_id}
                    {selectedPair.price_usd != null && (
                      <span className="ml-2 text-terminal font-medium">
                        {formatPrice(selectedPair.price_usd)}
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button onClick={clearSelection} className="pixel-btn px-3 py-2 text-sm focus-ring">
                  ← Favorites
                </button>
                <WatchlistStarButton
                  watched={isWatched}
                  loading={watchlistBusy}
                  onClick={handleToggleWatchlist}
                  symbol={selectedPair.base_token.symbol}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 md:gap-4">
              <div className="lg:col-span-2 space-y-3 md:space-y-4">
                <AnalysisPanel
                  analysis={analysis}
                  loading={analysisLoading}
                  error={analysisError}
                  selectedTimeframes={selectedTimeframes}
                  onTimeframesChange={handleTimeframesChange}
                />
                <EmbeddedChartPanel
                  chainId={selectedPair.chain_id}
                  pairAddress={selectedPair.pair_address}
                  pageUrl={selectedPair.url}
                  pairLabel={pairLabel}
                />
                <PairStatsPanel pair={selectedPair} />
              </div>

              <div className="space-y-3">
                <WatchlistPanel
                  items={watchlist}
                  onSelect={handleWatchlistSelect}
                  onRemove={handleRemoveWatchlist}
                />
                <AlertsPanel alerts={alerts} connected={connected} compact />
              </div>
            </div>
          </>
        )}
      </main>

      <HoldingFooter />

      {toast && (
        <div
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 pixel-panel text-base text-terminal font-terminal"
          role="status"
        >
          {toast}
        </div>
      )}
    </div>
  )
}