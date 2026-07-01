import { loginWithPrivy } from '../api'
import { setToken } from './auth'
import { withFetchTimeout } from './fetch-timeout'
import { setMemberProfile } from './member-profile'
import { mergeAnonPrefsIntoMember, setStoredMemberHandle } from './visitor-prefs'

type AccessTokenGetter = () => Promise<string | null>
type IdentityGetter = () => Promise<string | null>
type RefreshUser = () => Promise<unknown>

const EXCHANGE_COOLDOWN_KEY = 'aria:privy:exchange-cooldown'
const EXCHANGE_TIMEOUT_MS = 28_000
const RATE_LIMIT_COOLDOWN_MS = 60_000

let exchangeInFlight: Promise<void> | null = null

function isRateLimitError(message: string): boolean {
  return /rate.?limit|too many|429|trop de requ|veuillez patienter/i.test(message)
}

function markExchangeCooldown(ms: number = RATE_LIMIT_COOLDOWN_MS): void {
  try {
    sessionStorage.setItem(EXCHANGE_COOLDOWN_KEY, String(Date.now() + ms))
  } catch {
    /* ignore */
  }
}

function cooldownRemainingMs(): number {
  try {
    const raw = sessionStorage.getItem(EXCHANGE_COOLDOWN_KEY)
    if (!raw) return 0
    return Math.max(0, Number(raw) - Date.now())
  } catch {
    return 0
  }
}

function dispatchExchangeEvent(name: string, detail?: { message?: string }): void {
  window.dispatchEvent(new CustomEvent(name, { detail }))
}

function rateLimitMessage(waitMs: number = RATE_LIMIT_COOLDOWN_MS): string {
  const seconds = Math.ceil(waitMs / 1000)
  return `Privy limite temporairement les connexions. Attends ${seconds}s puis clique « Activer l'accès » une fois.`
}

function throwIfRateLimited(err: unknown): never {
  const message = err instanceof Error ? err.message : String(err)
  if (isRateLimitError(message)) {
    markExchangeCooldown()
    throw new Error(rateLimitMessage())
  }
  throw err instanceof Error ? err : new Error(message)
}

export function isSessionExchangeInFlight(): boolean {
  return exchangeInFlight !== null
}

export function exchangeCooldownRemainingMs(): number {
  return cooldownRemainingMs()
}

/** One Privy access token → backend session (identity token optional for returning members). */
export async function exchangePrivyForAriaSession(
  getAccessToken: AccessTokenGetter,
  getIdentityToken: IdentityGetter,
  refreshUser: RefreshUser,
  hookIdentityToken?: string | null,
): Promise<void> {
  const waitMs = cooldownRemainingMs()
  if (waitMs > 0) {
    throw new Error(rateLimitMessage(waitMs))
  }

  if (exchangeInFlight) {
    return exchangeInFlight
  }

  dispatchExchangeEvent('aria:session-exchange-start')

  exchangeInFlight = withFetchTimeout(
    (async () => {
      let accessToken: string | null = null
      let identityToken = hookIdentityToken ?? null

      try {
        accessToken = (await getAccessToken()) ?? null
        if (!identityToken) {
          identityToken = (await getIdentityToken()) ?? null
        }
      } catch (err) {
        throwIfRateLimited(err)
      }

      if (!identityToken) {
        try {
          await refreshUser()
          identityToken = hookIdentityToken ?? (await getIdentityToken()) ?? null
        } catch (err) {
          throwIfRateLimited(err)
        }
      }

      if (!accessToken) {
        throw new Error('Session Privy introuvable. Déconnecte-toi puis reconnecte-toi.')
      }

      try {
        const res = await loginWithPrivy(accessToken, identityToken)
        setToken(res.token)
        const handle = res.twitter_username
        if (handle) {
          setStoredMemberHandle(handle)
          mergeAnonPrefsIntoMember(handle)
        }
        setMemberProfile({
          handle,
          message:
            res.message ||
            'Bienvenue sur Aria Vanguard ZHC. Aria Market est la filiale flagship — ouvre le produit pour l’expérience complète.',
        })
        window.dispatchEvent(new Event('aria:member-session'))
        try {
          sessionStorage.removeItem(EXCHANGE_COOLDOWN_KEY)
        } catch {
          /* ignore */
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Sign-in failed'
        if (isRateLimitError(message)) {
          markExchangeCooldown()
          throw new Error(rateLimitMessage())
        }
        throw err instanceof Error ? err : new Error(message)
      }
    })(),
    EXCHANGE_TIMEOUT_MS,
    'Connexion trop longue — Ctrl+F5 puis réessaie',
  )

  try {
    await exchangeInFlight
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Sign-in failed'
    dispatchExchangeEvent('aria:session-exchange-error', { message })
    throw err
  } finally {
    exchangeInFlight = null
    dispatchExchangeEvent('aria:session-exchange-end')
  }
}