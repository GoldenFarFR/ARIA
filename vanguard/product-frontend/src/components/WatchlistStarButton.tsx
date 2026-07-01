import { Star } from 'lucide-react'
import { cn } from '../lib/cn'

interface WatchlistStarButtonProps {
  watched: boolean
  loading?: boolean
  onClick: () => void
  symbol?: string
}

export function WatchlistStarButton({
  watched,
  loading,
  onClick,
  symbol,
}: WatchlistStarButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      title={watched ? `Remove ${symbol ?? 'token'} from favorites` : `Add ${symbol ?? 'token'} to favorites`}
      aria-label={watched ? 'Remove from watchlist' : 'Add to watchlist'}
      className={cn(
        'group relative flex items-center gap-2 px-3 py-2 pixel-btn focus-ring transition-transform',
        watched && 'pixel-btn-active border-watch/50',
        loading && 'opacity-60 cursor-wait',
      )}
    >
      <span className="relative flex items-center justify-center w-8 h-8">
        <Star
          className={cn(
            'w-7 h-7 transition-colors',
            watched
              ? 'star-watched fill-watch text-watch'
              : 'star-waiting text-watch fill-watch/20',
          )}
          strokeWidth={watched ? 1.5 : 2}
        />
      </span>
      <span className="text-sm font-terminal hidden sm:inline">
        {watched ? 'Favorited' : 'Favorite'}
      </span>
    </button>
  )
}