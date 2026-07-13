import { useEffect, type RefObject } from 'react'

/**
 * Keeps `--vanguard-header-h` in sync with the REAL rendered height of the
 * fixed `<header>` (CommunityWelcomeBanner + VanguardNav) instead of the
 * static `pt-28 md:pt-32` guess `<main>` used to carry.
 *
 * Why this exists: CommunityWelcomeBanner's copy wraps to extra lines on
 * narrow phones (and the banner itself can be minimized/expanded), so the
 * header's real height is neither constant nor known ahead of time. A
 * stale static padding left content anchored near the top of the page
 * (OrganismHero's `.ao-pc` portfolio panel) rendering underneath the
 * header instead of below it.
 *
 * `--vanguard-header-h` gets a static fallback in index.css (`:root`) so
 * there is no flash of zero padding before this effect's first measurement.
 */
export function useHeaderClearance(headerRef: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const header = headerRef.current
    if (!header) return

    // getBoundingClientRect() (not entry.contentRect) to match this repo's
    // existing ResizeObserver convention (see OrganismHero's engine) and
    // avoid content-box/border-box ambiguity. Arrow function (not a
    // hoisted function declaration) so TS keeps the non-null narrowing
    // above across the closure.
    const applyHeight = () => {
      document.documentElement.style.setProperty(
        '--vanguard-header-h',
        `${Math.round(header.getBoundingClientRect().height)}px`,
      )
    }

    applyHeight()

    const ro = new ResizeObserver(applyHeight)
    ro.observe(header)

    return () => ro.disconnect()
  }, [headerRef])
}
