/** Build DexScreener embed + page URLs from pair metadata */

export type DexChartInterval =
  | '1s'
  | '1m'
  | '5m'
  | '15m'
  | '30m'
  | '1h'
  | '4h'
  | '1d'
  | '1w'

/** All intervals exposed on DexScreener pair charts (embed `interval` param). */
export const DEXSCREENER_CHART_INTERVALS: {
  id: DexChartInterval
  label: string
  param: string
}[] = [
  { id: '1s', label: '1s', param: '1s' },
  { id: '1m', label: '1m', param: '1' },
  { id: '5m', label: '5m', param: '5' },
  { id: '15m', label: '15m', param: '15' },
  { id: '30m', label: '30m', param: '30' },
  { id: '1h', label: '1h', param: '60' },
  { id: '4h', label: '4h', param: '240' },
  { id: '1d', label: '1D', param: '1440' },
  { id: '1w', label: '1W', param: '10080' },
]

export const DEFAULT_CHART_INTERVAL: DexChartInterval = '15m'

const CHART_INTERVAL_STORAGE_KEY = 'aria_market_chart_interval'

const EMBED_PARAMS =
  'embed=1&theme=dark&chartTheme=dark&trades=0&info=0&loadChartSettings=0&chartLeftToolbar=0&chartTimeframesToolbar=0'

export function loadChartInterval(): DexChartInterval {
  try {
    const raw = localStorage.getItem(CHART_INTERVAL_STORAGE_KEY)
    if (raw && DEXSCREENER_CHART_INTERVALS.some((i) => i.id === raw)) {
      return raw as DexChartInterval
    }
  } catch {
    /* ignore */
  }
  return DEFAULT_CHART_INTERVAL
}

export function saveChartInterval(interval: DexChartInterval): void {
  localStorage.setItem(CHART_INTERVAL_STORAGE_KEY, interval)
}

export function chartIntervalParam(interval: DexChartInterval): string {
  return DEXSCREENER_CHART_INTERVALS.find((i) => i.id === interval)?.param ?? '15'
}

export function dexscreenerPageUrl(
  chainId: string,
  pairAddress: string,
  pageUrl?: string,
): string {
  if (pageUrl) return pageUrl.split('?')[0]
  return `https://dexscreener.com/${chainId}/${pairAddress}`
}

export function dexscreenerEmbedUrl(
  chainId: string,
  pairAddress: string,
  pageUrl?: string,
  interval: DexChartInterval = DEFAULT_CHART_INTERVAL,
): string {
  const iv = chartIntervalParam(interval)
  return `${dexscreenerPageUrl(chainId, pairAddress, pageUrl)}?${EMBED_PARAMS}&interval=${encodeURIComponent(iv)}`
}