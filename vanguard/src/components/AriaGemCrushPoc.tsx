import { ExternalLink, Gamepad2, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { GemCrushGame } from '../games/aria-gem-crush/components/GemCrushGame'
import { getToken } from '../lib/auth'
import { PRODUCT_API_URL } from '../lib/site'
import '../games/aria-gem-crush/gem-crush.css'
import {
  GEM_CRUSH_RELEASE_TITLE,
  GEM_CRUSH_UPDATED_AT,
  GEM_CRUSH_VERSION,
} from '../games/aria-gem-crush/version'

const GAME_ID = 'gem-crush'
const SITE_SLUG = 'vanguard'
const REPO_URL = 'https://github.com/GoldenFarFR/aria-gem-crush'

export function AriaGemCrushPoc() {
  const [bestScore, setBestScore] = useState<number | null>(null)

  const syncScore = useCallback(async (score: number) => {
    const token = getToken()
    if (!token) return
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    }
    try {
      await fetch(`${PRODUCT_API_URL}/games/scores/${SITE_SLUG}/${GAME_ID}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ score, better: 'max' }),
      })
    } catch {
      /* optional — members only */
    }
  }, [])

  const handleScoreChange = useCallback(
    (score: number) => {
      if (score > (bestScore ?? 0)) {
        setBestScore(score)
        void syncScore(score)
      }
    },
    [bestScore, syncScore],
  )

  useEffect(() => {
    const token = getToken()
    if (!token) return
    fetch(`${PRODUCT_API_URL}/games/scores/${SITE_SLUG}/${GAME_ID}/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.score != null) setBestScore(data.score)
      })
      .catch(() => {})
  }, [])

  return (
    <section id="poc" className="relative py-24 md:py-32 border-t border-[#c9a962]/10">
      <div className="max-w-6xl mx-auto px-5">
        <div className="grid lg:grid-cols-[1fr_min(420px,100%)] gap-10 lg:gap-14 items-start">
          <div>
            <div className="flex flex-wrap items-center gap-3 mb-6">
              <div className="luxury-badge inline-flex items-center gap-2">
                <Sparkles className="w-3.5 h-3.5 text-[#c9a962]" />
                Test · développé par ARIA
              </div>
              <span
                className="text-xs tracking-wider uppercase text-[#8a7344] border border-[#c9a962]/25 px-2.5 py-1 rounded-sm"
                title={`${GEM_CRUSH_RELEASE_TITLE} — ${GEM_CRUSH_UPDATED_AT}`}
              >
                Version <strong className="text-[#e8d5a8]">v{GEM_CRUSH_VERSION}</strong>
              </span>
            </div>
            <h2 className="font-display font-semibold text-3xl md:text-5xl text-[#f4efe6] mb-5 tracking-wide">
              ARIA Gem Crush
            </h2>
            <div className="luxury-rule w-20 mb-6" />
            <p className="text-[#9a958a] leading-relaxed mb-4 max-w-xl font-light">
              Jeu match-3 type Candy Crush — conçu et codé par ARIA ZHC. Test public sur la page
              d&apos;accueil Vanguard : preuve qu&apos;elle peut livrer un produit jouable.
            </p>
            <ul className="text-sm text-[#6b665c] space-y-2 mb-8 max-w-lg font-light">
              <li>· Sons, confetti, tuto — finition type puzzle casual</li>
              <li>· Swipe mobile, indices, mélange auto si bloqué</li>
              <li>· Gemmes jelly, cascades, rayures et bombes</li>
              <li>· ARIA l&apos;améliore encore chaque jour</li>
            </ul>
            <div className="flex flex-wrap gap-3 items-center">
              <a
                href={REPO_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="btn-vanguard-secondary inline-flex items-center gap-2 px-5 py-3 text-sm focus-ring"
              >
                <Gamepad2 className="w-4 h-4 text-[#c9a962]" />
                Repo aria-gem-crush
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
              {bestScore != null && (
                <span className="text-xs text-[#8a7344] tracking-wide">
                  Ton meilleur score : <strong className="text-[#e8d5a8]">{bestScore}</strong>
                </span>
              )}
            </div>
          </div>

          <div className="w-full max-w-[420px] mx-auto lg:mx-0 lg:ml-auto">
            <GemCrushGame compact onScoreChange={handleScoreChange} />
          </div>
        </div>
      </div>
    </section>
  )
}