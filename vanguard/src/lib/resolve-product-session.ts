import { checkSession } from '../api'
import { clearToken, getToken } from './auth'
import { withFetchTimeout } from './fetch-timeout'

export const PRODUCT_SESSION_HINT =
  "Clique « Activer l'accès » en haut à droite, puis rouvre Open Aria Market."

/**
 * Use the persisted backend session only — never calls Privy.
 */
export async function resolveProductSession(): Promise<string | null> {
  const existing = getToken()
  if (!existing) return null

  try {
    const session = await withFetchTimeout(
      checkSession(),
      12_000,
      'Vérification session',
    )
    if (session.valid) return existing
    clearToken()
    return null
  } catch {
    // Network blip — keep token; iframe handoff may still work.
    return existing
  }
}