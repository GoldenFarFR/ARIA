import { Eye, Star, Trash2 } from 'lucide-react'
import { ChainBadge } from './ChainBadge'
import { cn } from '../lib/cn'
import type { WatchlistItem } from '../types'

interface WatchlistPanelProps {
  items: WatchlistItem[]
  onSelect: (item: WatchlistItem) => void
  onRemove: (item: WatchlistItem) => void
  primary?: boolean
}

export function WatchlistPanel({ items, onSelect, onRemove, primary = false }: WatchlistPanelProps) {
  return (
    <div className="pixel-panel overflow-hidden flex flex-col">
      <div className="px-3 py-2 border-b-2 border-border-bright flex items-center gap-2 bg-panel-elevated">
        <Star className="w-3.5 h-3.5 text-watch" />
        <h3 className="pixel-label text-[8px]">{primary ? 'Favorites' : 'Favorites'}</h3>
        <span className="text-xs text-terminal/50 ml-auto font-terminal">{items.length}</span>
      </div>

      <div
        className={cn(
          'p-1.5 space-y-0.5 overflow-y-auto',
          primary ? 'max-h-44' : 'max-h-36',
        )}
      >
        {items.length === 0 ? (
          <div className="text-center py-5 px-3">
            <Star className="w-6 h-6 text-watch/40 mx-auto mb-2 star-waiting" />
            <p className="text-xs text-terminal/60 font-terminal leading-relaxed">
              Search a token, tap the spinning star to favorite it.
            </p>
          </div>
        ) : (
          items.map((item) => (
            <div
              key={item.id}
              className="flex items-center gap-1.5 hover:bg-panel-elevated px-2 py-1.5 group border border-transparent hover:border-border"
            >
              <button
                onClick={() => onSelect(item)}
                className="flex-1 flex items-center gap-2 text-left min-w-0"
              >
                <span className="text-sm font-terminal font-medium text-terminal truncate">{item.symbol}</span>
                <ChainBadge chainId={item.chain_id} />
              </button>
              <button
                onClick={() => onSelect(item)}
                className="p-1 text-terminal/40 hover:text-accent opacity-0 group-hover:opacity-100 transition-opacity"
                title="View signals"
              >
                <Eye className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => onRemove(item)}
                className="p-1 text-terminal/40 hover:text-sell opacity-0 group-hover:opacity-100 transition-opacity"
                title="Remove"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}