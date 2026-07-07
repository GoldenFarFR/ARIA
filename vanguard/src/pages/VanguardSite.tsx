import { ArrowRight, ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getHoldingStructure, getSiteContent } from '../api'
import { HOLDING_DOMAIN, HOLDING_NAME, HOLDING_SITE_URL } from '../lib/site'
import { BrandMark } from '../components/BrandMark'
import { AriaChat } from '../components/AriaChat'
import { FaqSection } from '../components/FaqSection'
import { CommunityWelcomeBanner } from '../components/CommunityWelcomeBanner'
import { AriaWalletTeaser } from '../components/AriaWalletTeaser'
import { OrgChart } from '../components/OrgChart'
import { VanguardNav } from '../components/VanguardNav'
import type { AgentSetup, HoldingStructure, RepertoireItem } from '../types'

const HOLDING = HOLDING_NAME

const FALLBACK_MARKET: RepertoireItem = {
  id: 'market-fallback',
  name: 'Aria Market',
  description: 'Flagship subsidiary: DEX signals, watchlist, ARIA insights',
  status: 'live',
  category: 'product',
  priority: 1,
  zhc_aligned: true,
  tags: ['flagship'],
  notes: '',
  revenue_monthly: 0,
  created_at: '',
  updated_at: '',
  entity_type: 'subsidiary',
  slug: 'market',
}

function portfolioFromStructure(structure: HoldingStructure | null): RepertoireItem[] {
  if (!structure) return [FALLBACK_MARKET]
  const items = [...structure.subsidiaries, ...structure.ventures]
  return items.length > 0 ? items : [FALLBACK_MARKET]
}

export function VanguardSite() {
  const [setup, setSetup] = useState<AgentSetup | null>(null)
  const [holding, setHolding] = useState<HoldingStructure | null>(null)

  useEffect(() => {
    getSiteContent().then(setSetup).catch(console.error)
    getHoldingStructure().then(setHolding).catch(console.error)
  }, [])

  const oneLiner =
    setup?.one_liner ??
    'ZHC holding operated by ARIA, building in public, one brick at a time.'

  return (
    <div className="min-h-screen vanguard-minimal text-[#d4d0c8] overflow-x-hidden">
      <header className="fixed top-0 left-0 right-0 z-50">
        <CommunityWelcomeBanner />
        <VanguardNav />
      </header>

      <main className="relative pt-28 md:pt-32">
        <div className="hero-glow pointer-events-none" aria-hidden />

        <section className="page-shell min-h-[calc(100vh-8rem)] flex flex-col justify-center py-16 md:py-24">
          <p className="section-label mb-6 notranslate">ZHC · under construction</p>
          <h1 className="font-display font-semibold text-[2.75rem] sm:text-5xl md:text-6xl leading-[1.05] tracking-tight text-[#f4efe6] mb-6">
            <span className="notranslate">Aria Vanguard</span>
            <span className="block text-gradient-vanguard text-[0.92em] font-medium mt-1 notranslate">
              ZHC
            </span>
          </h1>
          <p className="text-base md:text-lg text-[#9a958a] max-w-md leading-relaxed font-light mb-10">
            {oneLiner}
          </p>
          <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
            <a
              href="#aria"
              className="btn-vanguard-glow px-8 py-3.5 text-sm tracking-wide inline-flex items-center justify-center gap-2 focus-ring"
            >
              Talk to ARIA
              <ArrowRight className="w-4 h-4" />
            </a>
            <a
              href="https://x.com/Aria_ZHC"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-[#8a8578] hover:text-[#e8d5a8] transition-colors tracking-wide focus-ring px-2 py-2 inline-flex items-center gap-1.5 notranslate"
            >
              {setup?.x_handle ?? '@Aria_ZHC'}
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        </section>

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