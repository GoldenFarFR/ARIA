import { useRef } from 'react'
import { HOLDING_DOMAIN, HOLDING_NAME, HOLDING_SITE_URL } from '../lib/site'
import { useHeaderClearance } from '../lib/use-header-clearance'
import { BrandMark } from '../components/BrandMark'
import { CommunityWelcomeBanner } from '../components/CommunityWelcomeBanner'
import { OrganismHero } from '../components/OrganismHero'
import { VanguardNav } from '../components/VanguardNav'

const HOLDING = HOLDING_NAME

// Everything the operator needs (track record, structure, FAQ, chat) is now
// reachable straight from OrganismHero itself (its own nav branches + the
// ask-input wired to the real ARIA chat). The setup/holding fetches and the
// below-hero sections that used to consume them (#track-record, #structure,
// #aria, FAQ) are gone with them.
export function VanguardSite() {
  const headerRef = useRef<HTMLElement>(null)

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
              {HOLDING_DOMAIN}
            </a>
          </div>
        </footer>
      </main>
    </div>
  )
}