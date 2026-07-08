import { LogOut } from 'lucide-react'
import { useState } from 'react'
import { CockpitDossierPanel } from '../components/CockpitDossierPanel'
import { CockpitGate } from '../components/CockpitGate'
import { CockpitPulsePanel } from '../components/CockpitPulsePanel'
import { clearOperatorSession, hasOperatorSecret } from '../lib/operator-auth'

export function CockpitPage() {
  const [unlocked, setUnlocked] = useState(() => hasOperatorSecret())

  return (
    <div className="vanguard-charcoal min-h-screen">
      <div className="max-w-3xl lg:max-w-4xl mx-auto px-5 sm:px-8 py-12 sm:py-16">
        <header className="mb-10">
          <p className="section-label mb-2">ARIA · Live</p>
          <h1 className="font-display text-3xl sm:text-4xl text-gradient-vanguard mb-3">
            Centre de commandement
          </h1>
          <p className="text-sm text-[#8b8f9a] leading-relaxed max-w-lg">
            Pouls en direct du système, et dossier chronologique de toute analyse menée sur un
            token. Lecture seule — aucune action financière n'est jamais déclenchée d'ici.
          </p>
        </header>

        <section className="mb-8">
          <CockpitPulsePanel />
        </section>

        {unlocked ? (
          <>
            <section className="mb-6">
              <CockpitDossierPanel onAuthLost={() => setUnlocked(false)} />
            </section>
            <div className="text-right">
              <button
                type="button"
                onClick={() => {
                  clearOperatorSession()
                  setUnlocked(false)
                }}
                className="inline-flex items-center gap-1.5 text-xs text-[#8b8f9a] hover:text-[#e8d5a8] transition-colors focus-ring rounded-sm px-1"
              >
                <LogOut size={12} strokeWidth={1.8} />
                Se déconnecter
              </button>
            </div>
          </>
        ) : (
          <section className="mt-2">
            <CockpitGate onUnlocked={() => setUnlocked(true)} />
          </section>
        )}
      </div>
    </div>
  )
}
