import { COLS, GEM_COUNT, ROWS } from './constants'
import type { Board, Cell, GemId, SpecialKind } from './types'

let _idSeq = 0

function nextId(): string {
  _idSeq += 1
  return `g-${_idSeq}`
}

export function randomGem(): GemId {
  return Math.floor(Math.random() * GEM_COUNT) as GemId
}

export function createCell(gem: GemId = randomGem(), special: SpecialKind = 'none'): Cell {
  return { id: nextId(), gem, special }
}

function inBounds(r: number, c: number): boolean {
  return r >= 0 && r < ROWS && c >= 0 && c < COLS
}

function key(r: number, c: number): string {
  return `${r},${c}`
}

export interface MatchGroup {
  cells: Set<string>
  gem: GemId
  horizontal: boolean
  vertical: boolean
  length: number
}

function scanLine(
  board: Board,
  r: number,
  c: number,
  dr: number,
  dc: number,
): MatchGroup | null {
  const gem = board[r][c].gem
  const cells = new Set<string>()
  cells.add(key(r, c))
  let len = 1
  let rr = r + dr
  let cc = c + dc
  while (inBounds(rr, cc) && board[rr][cc].gem === gem) {
    cells.add(key(rr, cc))
    len += 1
    rr += dr
    cc += dc
  }
  rr = r - dr
  cc = c - dc
  while (inBounds(rr, cc) && board[rr][cc].gem === gem) {
    cells.add(key(rr, cc))
    len += 1
    rr -= dr
    cc -= dc
  }
  if (len < 3) return null
  return {
    cells,
    gem,
    horizontal: dr === 0,
    vertical: dc === 0,
    length: len,
  }
}

export function findMatchGroups(board: Board): MatchGroup[] {
  const seen = new Set<string>()
  const groups: MatchGroup[] = []

  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      const h = scanLine(board, r, c, 0, 1)
      if (h) {
        const sig = [...h.cells].sort().join('|')
        if (!seen.has(sig)) {
          seen.add(sig)
          groups.push(h)
        }
      }
      const v = scanLine(board, r, c, 1, 0)
      if (v) {
        const sig = [...v.cells].sort().join('|')
        if (!seen.has(sig)) {
          seen.add(sig)
          groups.push(v)
        }
      }
    }
  }
  return groups
}

export function allMatchedCells(groups: MatchGroup[]): Set<string> {
  const out = new Set<string>()
  for (const g of groups) {
    for (const k of g.cells) out.add(k)
  }
  return out
}

function parseKey(k: string): [number, number] {
  const [r, c] = k.split(',').map(Number)
  return [r, c]
}

function expandSpecialClears(board: Board, matched: Set<string>): Set<string> {
  const cleared = new Set(matched)
  for (const k of matched) {
    const [r, c] = parseKey(k)
    const cell = board[r][c]
    if (cell.special === 'line-h') {
      for (let cc = 0; cc < COLS; cc += 1) cleared.add(key(r, cc))
    } else if (cell.special === 'line-v') {
      for (let rr = 0; rr < ROWS; rr += 1) cleared.add(key(rr, c))
    } else if (cell.special === 'bomb') {
      for (let rr = r - 1; rr <= r + 1; rr += 1) {
        for (let cc = c - 1; cc <= c + 1; cc += 1) {
          if (inBounds(rr, cc)) cleared.add(key(rr, cc))
        }
      }
    }
  }
  return cleared
}

function assignSpecials(board: Board, groups: MatchGroup[], matched: Set<string>): Board {
  const next = cloneBoard(board)
  for (const g of groups) {
    if (g.length >= 5) {
      const center = [...g.cells][Math.floor(g.cells.size / 2)]
      const [r, c] = parseKey(center)
      if (matched.has(center)) next[r][c].special = 'bomb'
    } else if (g.length === 4) {
      const center = [...g.cells][1]
      const [r, c] = parseKey(center)
      if (matched.has(center)) {
        next[r][c].special = g.horizontal ? 'line-h' : 'line-v'
      }
    }
  }
  return next
}

export function cloneBoard(board: Board): Board {
  return board.map((row) => row.map((cell) => ({ ...cell })))
}

export function createInitialBoard(): Board {
  let board: Board
  do {
    board = Array.from({ length: ROWS }, () =>
      Array.from({ length: COLS }, () => createCell()),
    )
  } while (findMatchGroups(board).length > 0)
  return board
}

export function areAdjacent(a: [number, number], b: [number, number]): boolean {
  const [ar, ac] = a
  const [br, bc] = b
  return Math.abs(ar - br) + Math.abs(ac - bc) === 1
}

export function swapCells(board: Board, a: [number, number], b: [number, number]): Board {
  const next = cloneBoard(board)
  const [ar, ac] = a
  const [br, bc] = b
  const tmp = next[ar][ac]
  next[ar][ac] = next[br][bc]
  next[br][bc] = tmp
  return next
}

export function hasAnyMatch(board: Board): boolean {
  return findMatchGroups(board).length > 0
}

export function wouldSwapMatch(board: Board, a: [number, number], b: [number, number]): boolean {
  const swapped = swapCells(board, a, b)
  return hasAnyMatch(swapped)
}

const EMPTY = -1 as GemId

function clearAndRefill(board: Board, cleared: Set<string>): Board {
  const next = cloneBoard(board)
  for (const k of cleared) {
    const [r, c] = parseKey(k)
    next[r][c] = { id: nextId(), gem: EMPTY, special: 'none' }
  }
  for (let c = 0; c < COLS; c += 1) {
    const stack: Cell[] = []
    for (let r = ROWS - 1; r >= 0; r -= 1) {
      if (next[r][c].gem !== EMPTY) stack.push(next[r][c])
    }
    for (let r = ROWS - 1; r >= 0; r -= 1) {
      const idx = ROWS - 1 - r
      next[r][c] = idx < stack.length ? stack[idx] : createCell()
    }
  }
  return next
}

export interface CascadeStep {
  board: Board
  cleared: Set<string>
  scoreGain: number
  combo: number
}

export function resolveCascade(board: Board, comboStart = 1): CascadeStep[] {
  const steps: CascadeStep[] = []
  let current = cloneBoard(board)
  let combo = comboStart

  while (true) {
    const groups = findMatchGroups(current)
    if (!groups.length) break

    let matched = allMatchedCells(groups)
    current = assignSpecials(current, groups, matched)
    matched = expandSpecialClears(current, matched)

    const base = matched.size * 15  // aria-gem-crush-v34
    const bonus = groups.reduce((s, g) => s + Math.max(0, g.length - 3) * 24, 0)  // aria-gem-crush-v34
    const scoreGain = Math.round((base + bonus) * (1 + (combo - 1) * 0.6))  // aria-gem-crush-v37

    const clearedBoard = clearAndRefill(current, matched)
    steps.push({
      board: clearedBoard,
      cleared: matched,
      scoreGain,
      combo,
    })

    current = clearedBoard
    combo += 1
  }

  return steps
}

export function processSwap(
  board: Board,
  a: [number, number],
  b: [number, number],
): { ok: true; steps: CascadeStep[]; final: Board } | { ok: false } {
  if (!areAdjacent(a, b)) return { ok: false }
  const swapped = swapCells(board, a, b)
  if (!hasAnyMatch(swapped)) return { ok: false }
  const steps = resolveCascade(swapped)
  const final = steps.length ? steps[steps.length - 1].board : swapped
  return { ok: true, steps, final }
}