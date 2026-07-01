import { authHeaders } from './lib/auth'
import { getVisitorId, visitorHeaders } from './lib/visitor'
import type {
  Alert,
  Candle,
  ChainDiscoverGroup,
  MarketFeedResponse,
  MarketFeedType,
  PairAnalysis,
  PairSummary,
  Timeframe,
  WatchlistItem,
} from './types'

const API = '/api'

export interface AuthRequiredStatus {
  required: boolean
  message: string
  site_name: string
  holding_name?: string
}

export async function getAuthRequired(): Promise<AuthRequiredStatus> {
  const res = await fetch(`${API}/auth/required`, { signal: AbortSignal.timeout(12_000) })
  if (!res.ok) throw new Error('Auth status unavailable')
  return res.json()
}

export async function checkSession(): Promise<{ valid: boolean; token?: string }> {
  const res = await apiFetch('/auth/session', { signal: AbortSignal.timeout(12_000) })
  if (!res.ok) return { valid: false }
  return res.json()
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = { ...visitorHeaders(), ...authHeaders(), ...init?.headers }
  const res = await fetch(`${API}${path}`, { credentials: 'include', ...init, headers })
  if (res.status === 401 && !path.startsWith('/auth/')) {
    window.dispatchEvent(new Event('aria-market:session-lost'))
  }
  return res
}

export async function discoverPairs(): Promise<ChainDiscoverGroup[]> {
  const res = await apiFetch('/pairs/discover')
  if (!res.ok) throw new Error('Discovery unavailable')
  const data = await res.json()
  return data.chains
}

export async function getMarketFeed(
  feed: MarketFeedType,
  chainId?: string | null,
  limit = 30,
): Promise<MarketFeedResponse> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (chainId) params.set('chain_id', chainId)
  const res = await apiFetch(`/pairs/${feed}?${params}`)
  if (!res.ok) throw new Error(`Market feed ${feed} unavailable`)
  return res.json()
}

export async function getPair(chainId: string, pairAddress: string): Promise<PairSummary> {
  const res = await apiFetch(`/pairs/${chainId}/${pairAddress}`)
  if (!res.ok) throw new Error('Pair not found')
  return res.json()
}

export async function searchPairs(query: string): Promise<PairSummary[]> {
  const res = await apiFetch(`/pairs/search?q=${encodeURIComponent(query)}`)
  if (!res.ok) throw new Error('Search failed')
  const data = await res.json()
  return data.pairs
}

export async function getCandles(
  chainId: string,
  pairAddress: string,
  timeframe: Timeframe,
): Promise<Candle[]> {
  const res = await apiFetch(
    `/pairs/${chainId}/${pairAddress}/candles?timeframe=${timeframe}&limit=200`,
  )
  if (!res.ok) throw new Error('Failed to load candles')
  const data = await res.json()
  return data.candles
}

export async function analyzePair(
  chainId: string,
  pairAddress: string,
  timeframes?: Timeframe[],
): Promise<PairAnalysis> {
  const qs =
    timeframes && timeframes.length > 0
      ? `?timeframes=${encodeURIComponent(timeframes.join(','))}`
      : ''
  const res = await apiFetch(`/analysis/${chainId}/${pairAddress}${qs}`)
  if (!res.ok) throw new Error('Analysis failed')
  return res.json()
}

export async function getWatchlist(): Promise<WatchlistItem[]> {
  const res = await apiFetch('/watchlist')
  if (!res.ok) throw new Error('Watchlist unavailable')
  return res.json()
}

export async function addToWatchlist(
  chainId: string,
  pairAddress: string,
  symbol: string,
): Promise<WatchlistItem> {
  const res = await apiFetch('/watchlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chain_id: chainId, pair_address: pairAddress, symbol }),
  })
  if (!res.ok) throw new Error('Failed to add to watchlist')
  return res.json()
}

export async function removeFromWatchlist(
  chainId: string,
  pairAddress: string,
): Promise<void> {
  const res = await apiFetch(`/watchlist/${chainId}/${pairAddress}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to remove from watchlist')
}

export async function getAlerts(): Promise<Alert[]> {
  const res = await apiFetch('/alerts?limit=30')
  if (!res.ok) throw new Error('Alerts unavailable')
  return res.json()
}

export async function agentChat(message: string) {
  const res = await apiFetch('/aria/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, visitor_id: getVisitorId() }),
  })
  if (!res.ok) throw new Error('Agent chat failed')
  return res.json()
}

export async function getAgentMessages() {
  const res = await apiFetch('/aria/messages')
  if (!res.ok) throw new Error('Agent history unavailable')
  return res.json()
}

export async function getAgentStatus() {
  const res = await apiFetch('/aria/status')
  if (!res.ok) throw new Error('Agent status unavailable')
  return res.json()
}

export async function getFaqContent(tag?: string, q?: string) {
  const params = new URLSearchParams()
  if (tag) params.set('tag', tag)
  if (q) params.set('q', q)
  const qs = params.toString()
  const res = await apiFetch(`/aria/content/faq${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw new Error('FAQ unavailable')
  return res.json()
}

export async function getSiteContent() {
  const res = await apiFetch('/aria/content/site')
  if (!res.ok) throw new Error('Site content unavailable')
  return res.json()
}

export async function getRepertoire() {
  const res = await apiFetch('/aria/repertoire')
  if (!res.ok) throw new Error('Repertoire unavailable')
  return res.json()
}

export async function getHoldingStructure() {
  const res = await apiFetch('/aria/holding')
  if (!res.ok) throw new Error('Holding structure unavailable')
  return res.json()
}

export async function addRepertoireItem(data: {
  name: string
  description?: string
  zhc_aligned?: boolean
}) {
  const res = await apiFetch('/aria/repertoire', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to add repertoire item')
  return res.json()
}

export async function getZhcIntro() {
  const res = await apiFetch('/aria/zhc/message/intro')
  if (!res.ok) throw new Error('ZHC message unavailable')
  return res.json()
}

export async function getAgentSetup() {
  const res = await apiFetch('/aria/setup')
  if (!res.ok) throw new Error('Agent setup unavailable')
  return res.json()
}