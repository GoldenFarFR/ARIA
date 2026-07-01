/** Site language — English SSOT; optional Google Translate on visitor choice only. */

export const SITE_LANGUAGE_KEY = 'vanguard-site-lang'

export const SITE_LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'fr', label: 'Français' },
  { code: 'es', label: 'Español' },
  { code: 'de', label: 'Deutsch' },
  { code: 'it', label: 'Italiano' },
  { code: 'pt', label: 'Português' },
  { code: 'ja', label: '日本語' },
  { code: 'ko', label: '한국어' },
  { code: 'zh-CN', label: '中文' },
  { code: 'ar', label: 'العربية' },
] as const

const GOOGLE_ELEMENT_ID = 'google_translate_element'
const GOOGLE_SCRIPT_ID = 'google-translate-script'

function cookieDomain(): string {
  const host = window.location.hostname
  if (host === 'localhost' || host === '127.0.0.1') return ''
  return host.startsWith('.') ? host : `.${host}`
}

export function getActiveSiteLanguage(): string {
  const stored = localStorage.getItem(SITE_LANGUAGE_KEY)
  if (stored) return stored
  const match = document.cookie.match(/googtrans=\/en\/([^;]+)/)
  return match?.[1] || 'en'
}

export function clearGoogleTranslateCookies(): void {
  const expired = 'Thu, 01 Jan 1970 00:00:00 GMT'
  document.cookie = `googtrans=;path=/;expires=${expired}`
  const domain = cookieDomain()
  if (domain) {
    document.cookie = `googtrans=;path=/;domain=${domain};expires=${expired}`
  }
}

export function applySiteLanguage(code: string): void {
  localStorage.setItem(SITE_LANGUAGE_KEY, code)
  if (code === 'en') {
    clearGoogleTranslateCookies()
    window.location.reload()
    return
  }
  clearGoogleTranslateCookies()
  document.cookie = `googtrans=/en/${code};path=/`
  const domain = cookieDomain()
  if (domain) {
    document.cookie = `googtrans=/en/${code};path=/;domain=${domain}`
  }
  window.location.reload()
}

let engineStarted = false

/** Hidden Google engine — runs only after visitor picks a non-English language. */
export function ensureGoogleTranslateEngine(): void {
  if (engineStarted || typeof window === 'undefined') return
  engineStarted = true

  window.googleTranslateElementInit = () => {
    const el = document.getElementById(GOOGLE_ELEMENT_ID)
    if (!el || !window.google?.translate) return
    new window.google.translate.TranslateElement(
      {
        pageLanguage: 'en',
        autoDisplay: false,
        includedLanguages: SITE_LANGUAGES.map((l) => l.code)
          .filter((c) => c !== 'en')
          .join(','),
      },
      GOOGLE_ELEMENT_ID,
    )
  }

  if (document.getElementById(GOOGLE_SCRIPT_ID)) {
    window.googleTranslateElementInit?.()
    return
  }

  const script = document.createElement('script')
  script.id = GOOGLE_SCRIPT_ID
  script.src =
    'https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit&hl=en'
  script.async = true
  document.body.appendChild(script)
}

declare global {
  interface Window {
    googleTranslateElementInit?: () => void
    google?: {
      translate: {
        TranslateElement: new (
          options: Record<string, unknown>,
          elementId: string,
        ) => void
      }
    }
  }
}