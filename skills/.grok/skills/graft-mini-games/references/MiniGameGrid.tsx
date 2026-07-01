import { MiniGameCard } from './MiniGameCard'
import type { MiniGameDefinition } from './types'

type Props = {
  games: MiniGameDefinition[]
}

export function MiniGameGrid({ games }: Props) {
  if (games.length === 0) return null

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
      {games.map((game) => (
        <MiniGameCard key={game.id} game={game} />
      ))}
    </div>
  )
}