import { useEffect, useRef, useState } from 'react'
import { getHoldingStructure, getSiteContent } from '../api'
import { HOLDING_DOMAIN, HOLDING_NAME, HOLDING_SITE_URL } from '../lib/site'
import { useHeaderClearance } from '../lib/use-header-clearance'
import { BrandMark } from '../components/BrandMark'
import { AriaChat } from '../components/AriaChat'
import { FaqSection } from '../components/FaqSection'
import { CommunityWelcomeBanner } from '../components/CommunityWelcomeBanner'
import { AriaWalletTeaser } from '../components/AriaWalletTeaser'
import { OrganismHero } from '../components/OrganismHero'
import { OrgChart } from '../components/OrgChart'
import { VanguardNav } from '../components/VanguardNav'
import type { AgentSetup, HoldingStructure, RepertoireItem } from '../types'

const HOLDING = HOLDING_NAME

function portfolioFromStructure(structure: HoldingStructure | null): RepertoireItem[] {
  if (!structure) return []
  return [...structure.subsidiaries, ...structure.ventures]
}

export function VanguardSite() {
  const [setup, setSetup] = useState<AgentSetup | null>(null)
  const [holding, setHolding] = useState<HoldingStructure | null>(null)
  const headerRef = useRef<HTMLElement>(null)

  useEffect(() => {
    getSiteContent().then(setSetup).catch(console.error)
    getHoldingStructure().then(setHolding).catch(console.error)
  }, [])

  // Real header height (banner text wraps on narrow phones, banner can be
  // minimized/expanded) instead of a static Tailwind padding-top guess --
  // see use-header-clearance.ts.
  useHeaderClearance(headerRef)

  return (
    <div className="min-h-screen vanguard-charcoal text-[#d4d0c8] overflow-x-hidden">
      <header ref={headerRef} className="fixed top-0 left-0 right-0 z-50">
        <CommunityWelcomeBanner />
        <VanguardNav />
      </header>

      <main className="relative pt-[var(--vanguard-header-h)]">
        <div className="hero-glow pointer-events-none" aria-hidden />

        <OrganismHero />

        <section id="track-record" className="page-shell py-14 md:py-16 border-t border-[#c9a962]/8">
          <AriaWalletTeaser />
        </section>

        <section id="structure" className="page-shell py-16 md:py-20 border-t border-[#c9a962]/8">
          <OrgChart
            holdingName={holding?.holding.name ?? HOLDING}
            holdingStatus={holding?.holding.status ?? 'live'}
            portfolio={portfolioFromStructure(holding)}
            subsidiaryLabel={holding?.subsidiary_label}
          />
        </section>

        <section id="aria" className="page-shell py-16 md:py-20 border-t border-[#c9a962]/8">
          <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4 mb-8">
            <div>
              <p className="section-label mb-3 notranslate">ARIA</p>
              <h2 className="font-display text-2xl md:text-3xl text-[#f4efe6] tracking-wide notranslate">
                {setup?.identity ?? 'ARIA ZHC'}
              </h2>
              <p className="text-sm text-[#6b665c] mt-2 font-light max-w-sm">
                {setup?.aria_title ?? 'Chief Autonomous Officer'}. Ask a question or share an
                idea via the community banner.
              </p>
            </div>
          </div>
          <AriaChat />
        </section>

        <FaqSection />

        <footer className="page-shell py-12 border-t border-[#c9a962]/8">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 text-xs text-[#6b665c]">
            <div className="flex items-center gap-3">
              <BrandMark size={20} />
              <span>
                © {new Date().getFullYear()} <span className="notranslate">{HOLDING}</span>
              </span>
            </div>
            <a
              href={HOLDING_SITE_URL}
              className="font-mono text-[#8a7344] hover:text-[#c9a962] transition-colors focus-ring notranslate"
            >
              {setup?.holding_domain ?? HOLDING_DOMAIN}
            </a>
          </div>
        </footer>
      </main>
    </div>
  )
}