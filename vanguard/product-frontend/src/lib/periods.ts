import type { PairSummary, PairTxns } from '../types'

export type StatsPeriod = keyof PairTxns

export const ALL_STATS_PERIODS: StatsPeriod[] = ['m5', 'h1', 'h6', 'h24']

export const PERIOD_LABELS: Record<StatsPeriod, string> = {
  m5: '5 minutes',
  h1: '1 hour',
  h6: '6 hours',
  h24: '24 hours',
}

export const MIN_PERIODS = 1
export const MAX_PERIODS = 4
export const DEFAULT_PERIODS: StatsPeriod[] = ['m5', 'h1', 'h6', 'h24']

const STORAGE_KEY = 'aria_market_selected_periods'

export function loadSelectedPeriods(): StatsPeriod[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return [...DEFAULT_PERIODS]
    const parsed = JSON.parse(raw) as StatsPeriod[]
    const valid = parsed.filter((p) => ALL_STATS_PERIODS.includes(p))
    if (valid.length >= MIN_PERIODS && valid.length <= MAX_PERIODS) {
      return valid.sort(
        (a, b) => ALL_STATS_PERIODS.indexOf(a) - ALL_STATS_PERIODS.indexOf(b),
      )
    }
  } catch {
    /* ignore */
  }
  return [...DEFAULT_PERIODS]
}

export function saveSelectedPeriods(periods: StatsPeriod[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(periods))
}

export function priceChangeForPeriod(
  pair: PairSummary,
  period: StatsPeriod,
): number | null {
  switch (period) {
    case 'm5':
      return pair.price_change_m5
    case 'h1':
      return pair.price_change_h1
    case 'h6':
      return pair.price_change_h6
    case 'h24':
      return pair.price_change_h24
    default:
      return null
  }
}

export function volumeForPeriod(pair: PairSummary, period: StatsPeriod): number | null {
  switch (period) {
    case 'm5':
      return pair.volume_m5 ?? null
    case 'h1':
      return pair.volume_h1 ?? null
    case 'h6':
      return pair.volume_h6 ?? null
    case 'h24':
      return pair.volume_h24
    default:
      return null
  }
}