import { loginWithPrivy } from '../api'
import { setToken } from './auth'

type AccessTokenGetter = () => Promise<string | null>
type IdentityGetter = () => Promise<string | null>
type RefreshUser = () => Promise<unknown>

const EXCHANGE_COOLDOWN_KEY = '__SITE_SLUG__:privy:exchange-cooldown'
export const MEMBER_SESSION_EVENT = '__SITE_SLUG__:member-session'

let exchangeInFlight: Promise<void> | null = null
let lastExchangeAt = 0

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function isRateLimitError(message: string): boolean {
  return /rate.?limit|too many|429/i.test(message)
}

export async function exchangePrivyForMemberSession(
  getAccessToken: AccessTokenGetter,
  getIdentityToken: IdentityGetter,
  refreshUser: RefreshUser,
): Promise<void> {
  if (exchangeInFlight) return exchangeInFlight

  const now = Date.now()
  if (now - lastExchangeAt < 3000) {
    throw new Error('Sign-in in progress — please wait.')
  }
  lastExchangeAt = now

  exchangeInFlight = (async () => {
    let accessToken = (await getAccessToken()) ?? null
    let identityToken = (await getIdentityToken()) ?? null

    if (!identityToken) {
      await refreshUser()
      for (let i = 0; i < 4 && !identityToken; i += 1) {
        await sleep(i === 0 ? 400 : 800)
        accessToken = (await getAccessToken()) ?? accessToken
        identityToken = (await getIdentityToken()) ?? null
      }
    }

    if (!accessToken) throw new Error('Privy session not found.')
    if (!identityToken) {
      throw new Error('Identity token missing — check Privy Dashboard (Advanced).')
    }

    try {
      const res = await loginWithPrivy(accessToken, identityToken)
      setToken(res.token)
      window.dispatchEvent(new Event(MEMBER_SESSION_EVENT))
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Sign-in failed'
      if (isRateLimitError(message)) {
        try {
          sessionStorage.setItem(EXCHANGE_COOLDOWN_KEY, String(Date.now() + 90_000))
        } catch {
          /* ignore */
        }
      }
      throw err instanceof Error ? err : new Error(message)
    }
  })()

  try {
    await exchangeInFlight
  } finally {
    exchangeInFlight = null
  }
}