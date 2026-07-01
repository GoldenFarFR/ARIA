import { createInitialBoard, findMatchGroups } from './engine'
import { hasValidMove } from './hints'
import type { Board } from './types'
function boardSignature(board: Board): string {
  return board.map((row) => row.map((c) => c.gem).join('')).join('|')
}

export function shuffleBoard(board: Board, maxAttempts = 40): Board {
  const sig = boardSignature(board)
  for (let i = 0; i < maxAttempts; i += 1) {
    let candidate = createInitialBoard()
    if (boardSignature(candidate) === sig) continue
    if (findMatchGroups(candidate).length > 0) continue
    if (!hasValidMove(candidate)) continue
    return candidate
  }
  return createInitialBoard()
}