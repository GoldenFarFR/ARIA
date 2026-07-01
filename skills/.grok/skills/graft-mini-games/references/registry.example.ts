import type { MiniGameDefinition } from './types'
import { SnakePoster } from './posters/SnakePoster'
import { SnakePreview } from './previews/SnakePreview'
import { PongPoster } from './posters/PongPoster'
import { PongPreview } from './previews/PongPreview'

export const MINI_GAMES: MiniGameDefinition[] = [
  {
    id: 'snake',
    title: 'Snake',
    description: 'Eat, grow, do not hit the walls.',
    Poster: SnakePoster,
    Preview: SnakePreview,
    href: '/play/snake',
  },
  {
    id: 'pong',
    title: 'Pong',
    description: 'Classic paddle duel.',
    Poster: PongPoster,
    Preview: PongPreview,
    href: '/play/pong',
  },
]