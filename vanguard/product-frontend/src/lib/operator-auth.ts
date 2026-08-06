// Operator-only session (06/08, private trading dashboard). Deliberately a
// SEPARATE token/storage key from lib/auth.ts's member token (Privy-gated,
// TOKEN_KEY = 'aria_market_token') -- mixing the two would let a paying
// member's session imply operator rights, which it must never do.
const OPERATOR_TOKEN_KEY = 'aria_operator_token'

let memoryToken: string | null = null

export function getOperatorToken(): string | null {
  if (memoryToken) return memoryToken
  try {
    const stored = sessionStorage.getItem(OPERATOR_TOKEN_KEY)
    if (stored) {
      memoryToken = stored
      return stored
    }
  } catch {
    /* private mode */
  }
  return null
}

export function setOperatorToken(token: string): void {
  memoryToken = token
  try {
    // sessionStorage only (not localStorage): this token grants read access
    // to every open paper position + entry/TP/SL/thesis -- deliberately
    // cleared when the tab closes rather than persisted indefinitely.
    sessionStorage.setItem(OPERATOR_TOKEN_KEY, token)
  } catch {
    /* ignore */
  }
}

export function clearOperatorToken(): void {
  memoryToken = null
  try {
    sessionStorage.removeItem(OPERATOR_TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

export function operatorAuthHeaders(): HeadersInit {
  const token = getOperatorToken()
  if (!token) return {}
  return { Authorization: `Bearer ${token}` }
}

export async function operatorLogin(
  username: string,
  password: string,
  totpCode: string,
): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const res = await fetch('/api/aria/ops/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username,
        password,
        totp_code: totpCode,
        installation_id: 'product-frontend-dashboard',
      }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      return { ok: false, error: body.detail || `Échec (${res.status})` }
    }
    const body = await res.json()
    setOperatorToken(body.token)
    return { ok: true }
  } catch {
    return { ok: false, error: 'Connexion au serveur impossible.' }
  }
}
