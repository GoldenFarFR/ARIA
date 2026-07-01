import type { Timeframe } from '../types'

export const ALL_TIMEFRAMES: Timeframe[] = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
export const DEFAULT_TIMEFRAMES: Timeframe[] = ['5m', '1h', '4h']
export const MIN_TIMEFRAMES = 2
export const MAX_TIMEFRAMES = 6

const STORAGE_KEY = 'aria_market_selected_timeframes'

export function loadSelectedTimeframes(): Timeframe[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return [...DEFAULT_TIMEFRAMES]
    const parsed = JSON.parse(raw) as Timeframe[]
    const valid = parsed.filter((tf) => ALL_TIMEFRAMES.includes(tf))
    if (valid.length >= MIN_TIMEFRAMES && valid.length <= MAX_TIMEFRAMES) {
      return valid
    }
  } catch {
    /* ignore */
  }
  return [...DEFAULT_TIMEFRAMES]
}

export function saveSelectedTimeframes(timeframes: Timeframe[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(timeframes))
}

export function trendLabel(index: number): { label: string; color: string } {
  if (index >= 40) return { label: 'Strong bullish', color: 'text-buy' }
  if (index >= 15) return { label: 'Bullish', color: 'text-buy/80' }
  if (index <= -40) return { label: 'Strong bearish', color: 'text-sell' }
  if (index <= -15) return { label: 'Bearish', color: 'text-sell/80' }
  return { label: 'Neutral', color: 'text-terminal/60' }
}