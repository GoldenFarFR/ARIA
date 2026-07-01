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

export async function getHoldingStructure(): Promise<HoldingStructure> {
  const res = await apiFetch('/aria/holding')
  if (!res.ok) throw new Error('Holding structure unavailable')
  return res.json()
}

export interface BillingPlan {
  plan_id: string
  name: string
  price_usd: number
  interval: string
  stripe_configured: boolean
  features: string[]
}

export interface SubscriptionStatus {
  plan: string
  active: boolean
  status: string
  current_period_end: string | null
}

export async function getBillingPlan(): Promise<BillingPlan> {
  const res = await fetch(`${PRODUCT_API_URL}/billing/plan`)
  if (!res.ok) throw new Error('Billing plan unavailable')
  return res.json()
}

export async function getSubscriptionStatus(): Promise<SubscriptionStatus> {
  const res = await apiFetch('/billing/status')
  if (!res.ok) throw new Error('Subscription status unavailable')
  return res.json()
}

export async function createCheckoutSession(body?: {
  success_url?: string
  cancel_url?: string
}): Promise<{ checkout_url: string }> {
  const res = await apiFetch('/billing/checkout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error((data as { detail?: string }).detail || 'Checkout failed')
  }
  return res.json()
}