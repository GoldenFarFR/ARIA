import { authHeaders } from './lib/auth'
import { operatorHeaders } from './lib/operator-auth'
import { PRODUCT_API_URL } from './lib/site'
import { getVisitorId, visitorHeaders } from './lib/visitor'
import type { AgentSetup, HoldingStructure } from './types'

/** Le secret opérateur fourni est absent, invalide, ou le TOTP requis manque/est faux. */
export class OperatorAuthError extends Error {}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = { ...visitorHeaders(), ...authHeaders(), ...init?.headers }
  return fetch(`${PRODUCT_API_URL}${path}`, { ...init, headers })
}

export interface AuthRequiredStatus {
  required: boolean
  message: string
  site_name: string
  holding_name?: string
}

export async function getAuthRequired(): Promise<AuthRequiredStatus> {
  const res = await fetch(`${PRODUCT_API_URL}/auth/required`, {
    signal: AbortSignal.timeout(12_000),
  })
  if (!res.ok) throw new Error('Auth status unavailable')
  return res.json()
}

export async function checkSession(): Promise<{ valid: boolean }> {
  const res = await apiFetch('/auth/session', { signal: AbortSignal.timeout(12_000) })
  if (!res.ok) return { valid: false }
  return res.json()
}

export async function loginWithPrivy(
  accessToken: string,
  identityToken?: string | null,
): Promise<{ token: string; twitter_username?: string; message?: string }> {
  const body: { access_token: string; identity_token?: string } = {
    access_token: accessToken,
  }
  if (identityToken) body.identity_token = identityToken

  const res = await fetch(`${PRODUCT_API_URL}/auth/privy/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(20_000),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error((data as { detail?: string }).detail || 'Sign-in failed')
  }
  return res.json()
}

export async function getSiteContent(): Promise<AgentSetup> {
  const res = await apiFetch('/aria/content/site')
  if (!res.ok) throw new Error('Site content unavailable')
  return res.json()
}

export async function getFaqContent() {
  const res = await apiFetch('/aria/content/faq')
  if (!res.ok) throw new Error('FAQ unavailable')
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

export interface CommunityFeedbackResult {
  ok: boolean
  reply: string
  queued?: boolean
  score?: number
  verdict?: string
}

const FEEDBACK_TIMEOUT_MS = 45_000

async function postCommunityFeedback(message: string, handle?: string): Promise<Response> {
  return apiFetch('/aria/community-feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, handle: handle ?? '', lang: 'en' }),
    signal: AbortSignal.timeout(FEEDBACK_TIMEOUT_MS),
  })
}

/** Wake Render free tier before the user submits (cold start ~30–60 s). */
export async function warmProductApi(): Promise<void> {
  try {
    await fetch(`${PRODUCT_API_URL}/health`, { signal: AbortSignal.timeout(8_000) })
  } catch {
    /* best-effort */
  }
}

export async function submitCommunityFeedback(
  message: string,
  handle?: string,
): Promise<CommunityFeedbackResult> {
  let res: Response
  try {
    res = await postCommunityFeedback(message, handle)
  } catch (firstErr) {
    const isTimeout =
      firstErr instanceof DOMException && firstErr.name === 'TimeoutError'
    try {
      await warmProductApi()
      res = await postCommunityFeedback(message, handle)
    } catch {
      if (isTimeout) {
        throw new Error(
          'API is waking up (Render cold start) — wait ~30 s and tap Send again. Your text is saved.',
        )
      }
      throw new Error('API unavailable — check your connection and try again in a moment.')
    }
  }
  const data = (await res.json().catch(() => ({}))) as CommunityFeedbackResult & {
    detail?: string
  }
  if (!res.ok) {
    throw new Error(data.detail || 'Could not send — please try again later.')
  }
  return data
}

export async function getHoldingStructure(): Promise<HoldingStructure> {
  const res = await apiFetch('/aria/holding')
  if (!res.ok) throw new Error('Holding structure unavailable')
  return res.json()
}

export interface CalibrationBucket {
  bucket: string
  count: number
  avg_pnl: number
}

export interface StrategyStats {
  buy_count: number
  hit_rate: number | null
  avg_pnl_buy: number | null
}

export interface TrackRecord {
  wallet_index: number
  wallet_return_pct: number
  vc_return_pct: number
  spec_return_pct: number
  positions: number
  verdicts_total: number
  verdicts_closed: number
  hit_rate: number | null
  avoid_count: number
  calibration: CalibrationBucket[]
  by_strategy: Record<string, StrategyStats>
  pool_active: number
  pool_rejected: number
  disclaimer: string
}

// Track-record public (teaser FOMO) : valeur du wallet suivi + calibration agrégée.
// Le détail des positions reste réservé aux abonnés.
export async function getTrackRecord(): Promise<TrackRecord> {
  const res = await fetch(`${PRODUCT_API_URL}/aria/track-record`)
  if (!res.ok) throw new Error('Track record unavailable')
  return res.json()
}

export interface PaperWalletTrade {
  symbol: string
  closed_at: string
  pnl_pct: number | null
  outcome: 'win' | 'loss'
}

export interface PaperWallet {
  starting: number
  equity: number
  return_pct: number
  realized_pnl: number
  unrealized_pnl: number
  open_positions: number
  closed_trades: number
  win_rate: number | null
  history: PaperWalletTrade[]
  disclaimer: string
}

// Portefeuille paper-trading public (#76) : preuve de track-record, jamais l'alpha.
// Positions ouvertes = agrégat seulement ; historique = symbole visible, contrat jamais.
export async function getPaperWallet(): Promise<PaperWallet> {
  const res = await fetch(`${PRODUCT_API_URL}/aria/paper-wallet`)
  if (!res.ok) throw new Error('Paper wallet unavailable')
  return res.json()
}

export interface PulseHeartbeat {
  alive: boolean
  last_tick: string | null
  cycles: Record<string, string>
}

export interface Pulse {
  status: string
  commit: string
  heartbeat: PulseHeartbeat
  paper_trading: boolean
  real_execution: boolean
  onchain: { anchor_ready: boolean; anchored: boolean }
}

// Pouls public (aucune auth) : signal coarse pour le suivi live du cockpit.
export async function getPulse(): Promise<Pulse> {
  const res = await fetch(`${PRODUCT_API_URL}/pulse`, { signal: AbortSignal.timeout(12_000) })
  if (!res.ok) throw new Error('Pulse unavailable')
  return res.json()
}

export interface ExamDaySummary {
  day: number
  answered: number
  avg_score: number | null
}

export interface ExamStatus {
  enabled: boolean
  program_days: number
  current_day: number
  today: ExamDaySummary
  cumulative: { total_questions: number; answered: number; avg_score: number | null }
}

// Examen trading ARIA (rehearsal pédagogique, 20 jours) — public, chiffres agrégés seulement.
export async function getExamStatus(): Promise<ExamStatus> {
  const res = await fetch(`${PRODUCT_API_URL}/aria/exam-status`)
  if (!res.ok) throw new Error('Exam status unavailable')
  return res.json()
}

export interface SepoliaLastDecision {
  at: string | null
  symbol: string | null
  decision: string
  outcome: string
  tx_hash: string | null
}

export interface SepoliaStatus {
  enabled: boolean
  cycles_total: number
  tx_count: number
  error_count: number
  hesitation_count: number
  circuit_breaker_open: boolean
  last: SepoliaLastDecision | null
  wallet_address: string | null
  wallet_balance_eth: number | null
}

// Rehearsal Sepolia autonome (testnet, aucune valeur réelle) — public, chiffres agrégés
// + dernière décision seulement (jamais une clé, jamais un montant réel).
export async function getSepoliaStatus(): Promise<SepoliaStatus> {
  const res = await fetch(`${PRODUCT_API_URL}/aria/sepolia-status`)
  if (!res.ok) throw new Error('Sepolia status unavailable')
  return res.json()
}

export interface DossierEvent {
  at: string | null
  kind: string
  source: string
  summary: string
  data: Record<string, unknown>
}

export interface Dossier {
  contract: string
  valid: boolean
  error?: string
  symbol?: string | null
  screened_status?: string | null
  counts?: Record<string, number>
  events?: DossierEvent[]
  generated_at?: string
}

// Dossier par token (opérateur uniquement) : chronologie complète des analyses
// ARIA sur un contrat. Lance une OperatorAuthError sur secret manquant/invalide,
// pour que l'appelant efface la session opérateur et re-propose le formulaire.
export async function getDossier(contract: string): Promise<Dossier> {
  const res = await apiFetch(`/aria/dossier/${contract}`, {
    headers: operatorHeaders(),
    signal: AbortSignal.timeout(20_000),
  })
  if (res.status === 401 || res.status === 403) {
    throw new OperatorAuthError('Operator secret invalid or missing')
  }
  if (!res.ok) throw new Error('Dossier unavailable')
  return res.json()
}

export interface MarketCyclePhase {
  label: string
  since: string
  change_pct: number
  cycle_name: string
}

export interface MarketCycle {
  available: boolean
  phase: MarketCyclePhase | null
  disclaimer: string
}

// Cycle macro Bitcoin PUBLIC (halving à halving) -- déterministe, aucun LLM, cache 1h.
export async function getMarketCycle(): Promise<MarketCycle> {
  const res = await fetch(`${PRODUCT_API_URL}/aria/market-cycle`, { signal: AbortSignal.timeout(12_000) })
  if (!res.ok) throw new Error('Market cycle unavailable')
  return res.json()
}

export interface SentimentReading {
  pair: string
  regime: string
  detail: string
  rsi: number | null
  bollinger_position: number | null
  momentum_pct: number | null
  drawdown_from_high_pct: number | null
  rally_from_low_pct: number | null
  trend_up: boolean | null
  computed_at: string
}

export interface MarketSentiment {
  readings: SentimentReading[]
  regime_labels: Record<string, string>
  disclaimer: string
}

// Sentiment de marché PUBLIC (RSI/Bollinger/momentum -> régimes) -- lit la dernière
// lecture déjà calculée par le cycle heartbeat, aucun recalcul synchrone.
export async function getMarketSentiment(): Promise<MarketSentiment> {
  const res = await fetch(`${PRODUCT_API_URL}/aria/sentiment`, { signal: AbortSignal.timeout(12_000) })
  if (!res.ok) throw new Error('Sentiment unavailable')
  return res.json()
}

export interface TrendingPairToken {
  address: string
  name: string
  symbol: string
}

export interface TrendingPair {
  chain_id: string
  base_token: TrendingPairToken
  price_usd: number | null
  price_change_h24: number | null
}

export interface TrendingPairsResponse {
  feed: string
  chain_id: string | null
  pairs: TrendingPair[]
  total: number
  source: string
}

// Paires crypto tendance en direct (DexScreener/GeckoTerminal via le backend) -- seule
// classe d'actif avec une vraie source de données dans ce backend (cf. pairs.py). Alimente
// le fond décoratif "marché" de l'accueil (OrganismHero) -- jamais de valeur inventée.
export async function getTrendingPairs(limit = 30): Promise<TrendingPairsResponse> {
  const res = await fetch(`${PRODUCT_API_URL}/pairs/trending?limit=${limit}`, {
    signal: AbortSignal.timeout(15_000),
  })
  if (!res.ok) throw new Error('Trending pairs unavailable')
  return res.json()
}
