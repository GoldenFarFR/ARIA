import type { ComponentType } from 'react'
import { Link, useParams } from 'react-router-dom'
import { SimpleNav } from '../components/SimpleNav'
import { MemoryGame } from '../games/memory/MemoryGame'
import { PongGame } from '../games/pong/PongGame'
import { SnakeGame } from '../games/snake/SnakeGame'
import { SITE_NAME } from '../lib/site'

const GAMES: Record<string, { title: string; Component: ComponentType }> = {
  snake: { title: 'Snake', Component: SnakeGame },
  pong: { title: 'Pong', Component: PongGame },
  memory: { title: 'Memory', Component: MemoryGame },
}

export function PlayPage() {
  const { gameId } = useParams<{ gameId: string }>()
  const entry = gameId ? GAMES[gameId] : undefined

  if (!entry) {
    return (
      <div className="site-mesh min-h-screen">
        <SimpleNav />
        <main className="flex flex-col items-center justify-center min-h-[70vh] px-5 text-center">
          <p className="text-neutral-400 mb-6">Game not found.</p>
          <Link to="/" className="btn-secondary px-4 py-2 text-sm">
            Back to {SITE_NAME}
          </Link>
        </main>
      </div>
    )
  }

  const { title, Component } = entry

  return (
    <div className="site-mesh min-h-screen flex flex-col">
      <SimpleNav />
      <header className="max-w-6xl w-full mx-auto px-5 pt-4 pb-2 flex items-center justify-between gap-4">
        <Link to="/" className="text-sm text-neutral-400 hover:text-white transition-colors shrink-0">
          ← Back
        </Link>
        <h1 className="text-lg font-medium text-white truncate">{title}</h1>
        <span className="w-16 shrink-0" aria-hidden />
      </header>
      <main className="flex-1 flex flex-col items-center justify-center px-4 pb-8 pt-2 min-h-0">
        <div className="game-stage w-full max-w-5xl flex-1 flex items-stretch">
          <Component />
        </div>
      </main>
    </div>
  )
}