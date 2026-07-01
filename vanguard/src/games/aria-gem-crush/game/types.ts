export type GemId = 0 | 1 | 2 | 3 | 4 | 5

export type SpecialKind = 'none' | 'line-h' | 'line-v' | 'bomb'

export interface Cell {
  id: string
  gem: GemId
  special: SpecialKind
}

export type Board = Cell[][]

export interface LevelConfig {
  level: number
  target: number
  moves: number
}

export interface GameStats {
  score: number
  movesLeft: number
  level: number
  target: number
  combo: number
}