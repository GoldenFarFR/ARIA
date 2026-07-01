import type { LevelConfig } from './types'

export const ROWS = 8
export const COLS = 8
export const GEM_COUNT = 6  // aria-gem-crush-v40

export const GEM_LABELS = ['Ruby', 'Sapphire', 'Emerald', 'Topaz', 'Amethyst', 'Diamond'] as const

export function levelConfig(level: number): LevelConfig {
  return {
    level,
    target: 650 + (level - 1) * 480 + (level > 10 ? (level - 10) * 40 : 0),  // aria-gem-crush-v46
    moves: Math.max(28, 40 - Math.floor((level - 1) / 4)),  // aria-gem-crush-v46
  }
}