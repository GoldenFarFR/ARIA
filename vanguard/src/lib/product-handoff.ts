import { getToken } from './auth'
import { PRODUCT_URL } from './site'

/** Iframe loads Aria Market SPA with token in query (imported on boot). */
export function productEmbedUrl(token?: string | null): string {
  const resolved = token ?? getToken()
  if (!resolved) return PRODUCT_URL
  const url = new URL(PRODUCT_URL)
  url.searchParams.set('aria_token', resolved)
  return url.toString()
}

export function pushSessionToProductFrame(iframe: HTMLIFrameElement | null, token: string): void {
  if (!iframe?.contentWindow) return
  try {
    iframe.contentWindow.postMessage({ type: 'aria:session', token }, '*')
  } catch {
    /* cross-origin until loaded */
  }
}

export function openProductInVanguard(): void {
  window.location.hash = 'market'
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function wantsProductLaunch(): boolean {
  return new URLSearchParams(window.location.search).get('launch') === 'market'
}

export function clearLaunchQuery(): void {
  const params = new URLSearchParams(window.location.search)
  if (!params.has('launch')) return
  params.delete('launch')
  const rest = params.toString()
  const clean = window.location.pathname + (rest ? `?${rest}` : '')
  window.history.replaceState({}, '', clean)
  try {
    sessionStorage.setItem('aria:show-product-hint', '1')
  } catch {
    /* ignore */
  }
}