import { authHeaders } from './lib/auth'
import { PRODUCT_API_URL } from './lib/site'
import { getVisitorId, visitorHeaders } from './lib/visitor'
import type { AgentSetup, HoldingStructure } from './types'

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

export interface ForexMajorPair {
  base: string
  quote: string
  rate: number | null
  date: string | null
  available: boolean
}

export interface ForexMajorsResponse {
  pairs: ForexMajorPair[]
}

// Paires forex majeures (EUR/USD, USD/JPY, GBP/USD, USD/CHF), taux de référence BCE
// via Frankfurter -- 2e source réelle de ce backend après les paires crypto (cf.
// forex.py). `available` par paire distingue un taux réel d'un échec upstream --
// jamais de valeur inventée côté frontend non plus.
export async function getForexMajors(): Promise<ForexMajorsResponse> {
  const res = await fetch(`${PRODUCT_API_URL}/forex/majors`, {
    signal: AbortSignal.timeout(15_000),
  })
  if (!res.ok) throw new Error('Forex majors unavailable')
  return res.json()
}
