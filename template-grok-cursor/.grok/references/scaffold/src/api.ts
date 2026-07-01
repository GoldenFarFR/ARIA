import { authHeaders } from './lib/auth'
import { API_URL } from './lib/site'

export async function getAuthRequired(): Promise<{ required: boolean }> {
  const res = await fetch(`${API_URL}/auth/required`)
  if (!res.ok) throw new Error('Auth status unavailable')
  return res.json()
}

export async function checkSession(): Promise<{ valid: boolean }> {
  const res = await fetch(`${API_URL}/auth/session`, { headers: authHeaders() })
  if (!res.ok) return { valid: false }
  return res.json()
}

export async function loginWithPrivy(
  accessToken: string,
  identityToken: string,
): Promise<{ token: string }> {
  const res = await fetch(`${API_URL}/auth/privy/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ access_token: accessToken, identity_token: identityToken }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error((data as { detail?: string }).detail || 'Sign-in failed')
  }
  return res.json()
}