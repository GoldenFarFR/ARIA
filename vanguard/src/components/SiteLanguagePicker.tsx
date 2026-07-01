import { Globe } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  applySiteLanguage,
  ensureGoogleTranslateEngine,
  getActiveSiteLanguage,
  SITE_LANGUAGES,
} from '../lib/site-language'

/**
 * English by default — Google Translate only when the visitor changes language here.
 * (Not browser auto-translate; not server-side i18n.)
 */
export function SiteLanguagePicker() {
  const [lang, setLang] = useState('en')

  useEffect(() => {
    const active = getActiveSiteLanguage()
    setLang(active)
    if (active !== 'en') {
      ensureGoogleTranslateEngine()
    }
  }, [])

  return (
    <label className="site-language-picker notranslate flex items-center gap-1.5 cursor-pointer">
      <Globe className="w-3.5 h-3.5 text-[#8a7344] shrink-0" aria-hidden />
      <span className="hidden sm:inline text-[10px] uppercase tracking-wider text-[#6b665c]">
        Language
      </span>
      <select
        value={lang}
        onChange={(e) => applySiteLanguage(e.target.value)}
        className="site-language-select focus-ring"
        aria-label="Site language"
      >
        {SITE_LANGUAGES.map(({ code, label }) => (
          <option key={code} value={code}>
            {label}
          </option>
        ))}
      </select>
      {/* Hidden host for Google engine (cookie-driven translation on reload) */}
      <div id="google_translate_element" className="google-translate-engine" aria-hidden />
    </label>
  )
}