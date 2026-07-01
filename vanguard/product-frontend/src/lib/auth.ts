const TOKEN_KEY = 'aria_market_token'

let memoryToken: string | null = null

function readUrlToken(): string | null {
  const token = new URLSearchParams(window.location.search).get('aria_token')
  return token && token.length >= 16 ? token : null
}

export function getToken(): string | null {
  if (memoryToken) return memoryToken
  const urlToken = readUrlToken()
  if (urlToken) return urlToken
  try {
    const stored = localStorage.getItem(TOKEN_KEY)
    if (stored) return stored
  } catch {
    /* iframe / private mode */
  }
  try {
    const stored = sessionStorage.getItem(TOKEN_KEY)
    if (stored) return stored
  } catch {
    /* ignore */
  }
  return null
}

export function setToken(token: string): void {
  memoryToken = token
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* ignore */
  }
  try {
    sessionStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* ignore */
  }
}

export function clearToken(): void {
  memoryToken = null
  try {
    localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

export function authHeaders(): HeadersInit {
  const token = getToken()
  if (!token) return {}
  return { Authorization: `Bearer ${token}` }
}