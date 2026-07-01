import { COLS, ROWS } from './constants'
import { hasAnyMatch, swapCells } from './engine'
import type { Board } from './types'

export type Pos = [number, number]

export function findHintMove(board: Board): [Pos, Pos] | null {
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      const a: Pos = [r, c]
      const neighbors: Pos[] = (
        [
          [r - 1, c],
          [r + 1, c],
          [r, c - 1],
          [r, c + 1],
        ] as Pos[]
      ).filter(([nr, nc]) => nr >= 0 && nr < ROWS && nc >= 0 && nc < COLS)
      for (const b of neighbors) {
        if (hasAnyMatch(swapCells(board, a, b))) return [a, b]
      }
    }
  }
  return null
}

export function hasValidMove(board: Board): boolean {
  return findHintMove(board) !== null
}