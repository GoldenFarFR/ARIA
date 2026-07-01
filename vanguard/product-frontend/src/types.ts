export type Timeframe = '1m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d'
export type SignalType = 'buy' | 'sell' | 'watch' | 'neutral'

export interface TokenInfo {
  address: string
  name: string
  symbol: string
}

export interface TxnPeriod {
  buys: number
  sells: number
}

export interface PairTxns {
  m5?: TxnPeriod | null
  h1?: TxnPeriod | null
  h6?: TxnPeriod | null
  h24?: TxnPeriod | null
}

export interface PairSocial {
  platform: string
  handle?: string | null
  url?: string | null
}

export interface PairSummary {
  chain_id: string
  dex_id: string
  pair_address: string
  url: string
  base_token: TokenInfo
  quote_token: TokenInfo
  price_usd: number | null
  price_native?: string | null
  price_change_m5: number | null
  price_change_h1: number | null
  price_change_h6: number | null
  price_change_h24: number | null
  volume_m5?: number | null
  volume_h1?: number | null
  volume_h6?: number | null
  volume_h24: number | null
  liquidity_usd: number | null
  liquidity_base?: number | null
  liquidity_quote?: number | null
  market_cap: number | null
  fdv?: number | null
  pair_created_at?: number | null
  labels?: string[]
  txns?: PairTxns | null
  boosts_active?: number | null
  image_url: string | null
  websites?: string[]
  socials?: PairSocial[]
}

export type MarketFeedType = 'trending' | 'new' | 'gainers' | 'losers'

export interface MarketFeedResponse {
  feed: MarketFeedType
  chain_id: string | null
  pairs: PairSummary[]
  total: number
  source: string
}

export interface Candle {
  timestamp: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface DivergenceSignal {
  type: string
  indicator: string
  strength: number
  description: string
}

export interface FibonacciLevel {
  level: number
  price: number
  label: string
}

export interface FibonacciAnalysis {
  swing_high: number
  swing_low: number
  trend: string
  levels: FibonacciLevel[]
}

export interface IndicatorSnapshot {
  rsi: number | null
  macd: number | null
  macd_signal: number | null
  macd_histogram: number | null
  ema_9: number | null
  ema_21: number | null
  ema_50: number | null
  sma_200: number | null
  atr: number | null
  volume_sma: number | null
}

export interface BuySignal {
  score: number
  signal_type: SignalType
  reasons: string[]
  entry_zone: [number, number] | null
  stop_loss: number | null
  take_profit: number[]
}

export interface TimeframeAnalysis {
  timeframe: Timeframe
  indicators: IndicatorSnapshot
  divergences: DivergenceSignal[]
  fibonacci: FibonacciAnalysis | null
  buy_signal: BuySignal
  support_levels: number[]
  resistance_levels: number[]
}

export interface PairAnalysis {
  pair: PairSummary
  analyzed_at: string
  timeframes: TimeframeAnalysis[]
  global_score: number
  trend_index: number
  consensus: SignalType
  summary: string
}

export interface Alert {
  id: string
  pair_address: string
  chain_id: string
  symbol: string
  signal_type: SignalType
  score: number
  timeframe: Timeframe
  message: string
  created_at: string
}

export interface ChainDiscoverGroup {
  chain_id: string
  label: string
  pairs: PairSummary[]
}

export interface WatchlistItem {
  id: string
  chain_id: string
  pair_address: string
  symbol: string
  added_at: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'agent'
  content: string
  skill_used?: string | null
  created_at: string
}

export interface ChatResponse {
  reply: string
  skill_used: string | null
  actions_taken: string[]
  data: Record<string, unknown>
}

export interface AgentStatus {
  name: string
  holding_name?: string
  memory_entries: number
  repertoire_count: number
  watchlist_count: number
  zhc_connection: string
  llm_configured?: boolean
  long_term_memory?: boolean
}

export interface AgentSetup {
  identity: string
  holding: string
  aria_title: string
  holding_structure: string
  governance_rule?: string
  one_liner?: string
  public_url?: string
  holding_domain?: string
  x_handle: string
  email: string
  bio_suggestion: string
  setup_steps: string[]
  x_api_configured: boolean
  telegram_configured: boolean
}

export interface RepertoireItem {
  id: string
  name: string
  description: string
  status: string
  category: string
  priority: number
  zhc_aligned: boolean
  tags: string[]
  notes: string
  revenue_monthly: number
  created_at: string
  updated_at: string
  entity_type?: 'holding' | 'subsidiary' | 'venture'
  parent_id?: string | null
  slug?: string | null
}

export interface HoldingStructure {
  holding: RepertoireItem
  subsidiaries: RepertoireItem[]
  ventures: RepertoireItem[]
  aria_title: string
  holding_tagline?: string
  governance_rule?: string
  subsidiary_label?: string
}