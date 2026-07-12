import { LogOut } from 'lucide-react'
import { useState } from 'react'
import { CockpitCalibrationPanel } from '../components/CockpitCalibrationPanel'
import { CockpitCyclePanel } from '../components/CockpitCyclePanel'
import { CockpitDossierPanel } from '../components/CockpitDossierPanel'
import { CockpitGate } from '../components/CockpitGate'
import { CockpitMethodologyPanel } from '../components/CockpitMethodologyPanel'
import { CockpitPulsePanel } from '../components/CockpitPulsePanel'
import { CockpitSentimentPanel } from '../components/CockpitSentimentPanel'
import { CockpitWalletPanel } from '../components/CockpitWalletPanel'
import { clearOperatorSession, hasOperatorSecret } from '../lib/operator-auth'

export function CockpitPage() {
  const [unlocked, setUnlocked] = useState(() => hasOperatorSecret())

  return (
    <div className="vanguard-charcoal min-h-screen">
      <div className="max-w-5xl xl:max-w-6xl mx-auto px-5 sm:px-8 py-12 sm:py-16">
        <header className="mb-10">
          <p className="section-label mb-2">ARIA · Live</p>
          <h1 className="font-display text-3xl sm:text-4xl text-gradient-vanguard mb-3">
            Centre de commandement
          </h1>
          <p className="text-sm text-[#8b8f9a] leading-relaxed max-w-2xl">
            Là où tout converge. Lecture seule — jamais d'ordre passé d'ici.
          </p>
        </header>

        <section className="mb-8">
          <CockpitPulsePanel />
        </section>

        <section className="mb-8">
          <CockpitCalibrationPanel />
        </section>

        <section className="mb-8">
          <CockpitWalletPanel />
        </section>

        <section className="grid md:grid-cols-2 gap-6 mb-8">
          <CockpitCyclePanel />
          <CockpitSentimentPanel />
        </section>

        <section className="mb-8">
          <CockpitMethodologyPanel />
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
