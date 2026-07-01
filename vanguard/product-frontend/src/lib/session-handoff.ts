import { setToken } from './auth'

const PARENT_ORIGINS = new Set([
  'https://ariavanguardzhc.com',
  'https://www.ariavanguardzhc.com',
  'http://localhost:5173',
  'http://127.0.0.1:5173',
])

/** Import member session from Vanguard (?aria_token= or postMessage). */
export function importVanguardSession(): boolean {
  const params = new URLSearchParams(window.location.search)
  const token = params.get('aria_token')
  if (!token || token.length < 16) return false
  setToken(token)
  return true
}

/** Strip handoff query param after session is confirmed (keeps token in memory/storage). */
export function clearVanguardSessionFromUrl(): void {
  const params = new URLSearchParams(window.location.search)
  if (!params.has('aria_token')) return
  params.delete('aria_token')
  const rest = params.toString()
  const cleanPath = window.location.pathname + (rest ? `?${rest}` : '')
  window.history.replaceState({}, '', cleanPath)
}

/** Parent iframe (Vanguard) can push a fresh token without reloading. */
export function listenForVanguardSessionPush(): () => void {
  const handler = (event: MessageEvent) => {
    if (!PARENT_ORIGINS.has(event.origin)) return
    const data = event.data as { type?: string; token?: string }
    if (data?.type !== 'aria:session' || !data.token || data.token.length < 16) return
    setToken(data.token)
    window.dispatchEvent(new Event('aria-market:session-restored'))
  }
  window.addEventListener('message', handler)
  return () => window.removeEventListener('message', handler)
}