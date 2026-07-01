import { Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { ChainBadge } from './ChainBadge'
import { formatPrice, getChainMeta } from '../lib/chains'
import type { PairSummary } from '../types'

interface SearchBarProps {
  onSearch: (query: string) => void
  results: PairSummary[]
  loading: boolean
  onSelect: (pair: PairSummary) => void
}

export function SearchBar({ onSearch, results, loading, onSelect }: SearchBarProps) {
  const [query, setQuery] = useState('')
  const [chainFilter, setChainFilter] = useState<string | null>(null)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (query.trim()) onSearch(query.trim())
  }

  const grouped = useMemo(() => {
    const filtered = chainFilter
      ? results.filter((p) => p.chain_id === chainFilter)
      : results
    const map = new Map<string, PairSummary[]>()
    for (const pair of filtered) {
      const list = map.get(pair.chain_id) ?? []
      list.push(pair)
      map.set(pair.chain_id, list)
    }
    return [...map.entries()].sort((a, b) => {
      const la = getChainMeta(a[0]).label
      const lb = getChainMeta(b[0]).label
      return la.localeCompare(lb, 'en')
    })
  }, [results, chainFilter])

  const availableChains = useMemo(() => {
    const ids = [...new Set(results.map((p) => p.chain_id))]
    return ids.sort((a, b) => getChainMeta(a).label.localeCompare(getChainMeta(b).label, 'en'))
  }, [results])

  return (
    <div className="relative">
      <form onSubmit={handleSubmit} className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-terminal/40" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search a token (e.g. PEPE, WIF, BONK…)"
          className="w-full pl-10 pr-4 py-2 pixel-input focus-ring"
        />
      </form>

      {loading && (
        <div className="absolute z-20 w-full mt-1 p-3 pixel-panel text-base text-[#8a8880] font-terminal">
          Searching…
        </div>
      )}

      {!loading && results.length > 0 && (
        <div className="absolute z-20 w-full mt-1 pixel-panel overflow-hidden">
          {availableChains.length > 1 && (
            <div className="px-3 py-2 border-b-2 border-border flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => setChainFilter(null)}
                className={`px-2 py-0.5 text-sm font-terminal transition-colors ${
                  chainFilter === null
                    ? 'pixel-btn pixel-btn-active'
                    : 'pixel-btn text-terminal/50 hover:text-terminal'
                }`}
              >
                All
              </button>
              {availableChains.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setChainFilter(id)}
                  className={`px-2 py-0.5 text-sm font-terminal transition-colors ${
                    chainFilter === id
                      ? `pixel-btn ${getChainMeta(id).bgClass} ${getChainMeta(id).textClass}`
                      : 'pixel-btn text-terminal/50 hover:text-terminal'
                  }`}
                >
                  {getChainMeta(id).label}
                </button>
              ))}
            </div>
          )}

          <div className="max-h-80 overflow-y-auto">
            {grouped.map(([chainId, pairs]) => (
              <div key={chainId}>
                <div className="sticky top-0 px-3 py-2 bg-panel/95 backdrop-blur border-b-2 border-border">
                  <ChainBadge chainId={chainId} />
                  <span className="ml-2 text-sm text-terminal/40 font-terminal">{pairs.length} result{pairs.length > 1 ? 's' : ''}</span>
                </div>
                {pairs.map((pair) => (
                  <button
                    key={`${pair.chain_id}-${pair.pair_address}`}
                    onClick={() => onSelect(pair)}
                    className="w-full flex items-center gap-3 px-3 py-2.5 hover:bg-panel-elevated text-left transition-colors"
                  >
                    {pair.image_url ? (
                      <img src={pair.image_url} alt="" className="w-8 h-8 ring-1 ring-border" />
                    ) : (
                      <div className="w-8 h-8 bg-panel-elevated border border-border flex items-center justify-center text-sm text-terminal/50 font-terminal">
                        {pair.base_token.symbol.slice(0, 2)}
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-terminal font-medium text-terminal truncate">
                        {pair.base_token.symbol} / {pair.quote_token.symbol}
                      </div>
                      <div className="text-sm text-terminal/50 font-terminal">{pair.dex_id}</div>
                    </div>
                    {pair.price_usd != null && (
                      <div className="text-right shrink-0">
                        <div className="text-sm text-terminal font-terminal">{formatPrice(pair.price_usd)}</div>
                        {pair.price_change_h24 != null && (
                          <div
                            className={`text-sm font-terminal ${
                              pair.price_change_h24 >= 0 ? 'text-buy' : 'text-sell'
                            }`}
                          >
                            {pair.price_change_h24 >= 0 ? '+' : ''}
                            {pair.price_change_h24.toFixed(1)}%
                          </div>
                        )}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}