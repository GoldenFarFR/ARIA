import { useRef } from 'react'
import { useHeaderClearance } from '../lib/use-header-clearance'
import { CommunityWelcomeBanner } from '../components/CommunityWelcomeBanner'
import { OrganismHero } from '../components/OrganismHero'
import { VanguardNav } from '../components/VanguardNav'

// Full-screen blob, zero page scroll: the organism fills exactly one screen
// (height:calc(100dvh - var(--vanguard-header-h)) in OrganismHero's own CSS)
// below the fixed header, so the outer shell is pinned to h-screen with
// overflow hidden rather than the old min-h-screen (which allowed a
// vertical scrollbar down to a footer). Everything the operator needs
// (track record, structure, FAQ, chat) is reachable straight from
// OrganismHero itself (its own nav branches + the ask-input wired to the
// real ARIA chat) -- no below-hero content left to scroll to.
export function VanguardSite() {
  const headerRef = useRef<HTMLElement>(null)

  // Real header height (banner text wraps on narrow phones, banner can be
  // minimized/expanded) instead of a static Tailwind padding-top guess --
  // see use-header-clearance.ts.
  useHeaderClearance(headerRef)

  return (
    <div className="h-screen overflow-hidden vanguard-charcoal text-[#d4d0c8]">
      <header ref={headerRef} className="fixed top-0 left-0 right-0 z-50">
        <CommunityWelcomeBanner />
        <VanguardNav />
      </header>

      <main className="relative pt-[var(--vanguard-header-h)]">
        <div className="hero-glow pointer-events-none" aria-hidden />

        <OrganismHero />
      </main>
    </div>
  )
}
