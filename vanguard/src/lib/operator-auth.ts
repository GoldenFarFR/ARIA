const SECRET_KEY = 'aria_operator_secret'
const TOTP_KEY = 'aria_operator_totp'

// Session-only (sessionStorage, PAS localStorage) : un secret opérateur ne doit
// jamais survivre au-delà de l'onglet — effacé à la fermeture, à la déconnexion
// explicite, ou dès qu'un appel renvoie 401/403 (secret invalide/expiré).

export function getOperatorSecret(): string | null {
  return sessionStorage.getItem(SECRET_KEY)
}

export function setOperatorSecret(secret: string): void {
  sessionStorage.setItem(SECRET_KEY, secret)
}

export function getOperatorTotp(): string | null {
  return sessionStorage.getItem(TOTP_KEY)
}

export function setOperatorTotp(totp: string): void {
  if (totp) sessionStorage.setItem(TOTP_KEY, totp)
  else sessionStorage.removeItem(TOTP_KEY)
}

export function hasOperatorSecret(): boolean {
  return !!getOperatorSecret()
}

export function clearOperatorSession(): void {
  sessionStorage.removeItem(SECRET_KEY)
  sessionStorage.removeItem(TOTP_KEY)
}

export function operatorHeaders(): HeadersInit {
  const secret = getOperatorSecret()
  if (!secret) return {}
  const headers: Record<string, string> = { 'X-Admin-Secret': secret }
  const totp = getOperatorTotp()
  if (totp) headers['X-Admin-Totp'] = totp
  return headers
}
