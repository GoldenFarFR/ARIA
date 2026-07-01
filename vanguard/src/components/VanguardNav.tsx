import { ExternalLink, Menu, X } from 'lucide-react'
import { BrandMark } from './BrandMark'
import { useEffect, useState } from 'react'
import { cn } from '../lib/cn'
import { MemberSignInButton } from './MemberSignInButton'
import { ProductLaunchLink } from './ProductLaunchLink'

const SECTIONS = [
  { href: '#poc', label: 'Gem Crush' },
  { href: '#pricing', label: 'Pro' },
  { href: '#structure', label: 'Structure' },
  { href: '#portfolio', label: 'Portfolio' },
  { href: '#aria', label: 'ARIA' },
  { href: '#model', label: 'ZHC Model' },
  { href: '#faq', label: 'FAQ' },
]

export function VanguardNav() {
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const [active, setActive] = useState('')

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    const ids = SECTIONS.map((s) => s.href.slice(1))
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)
        if (visible[0]?.target.id) setActive(visible[0].target.id)
      },
      { rootMargin: '-40% 0px -50% 0px', threshold: [0, 0.2, 0.5] },
    )
    ids.forEach((id) => {
      const el = document.getElementById(id)
      if (el) observer.observe(el)
    })
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [open])

  return (
    <>
      <nav
        className={cn(
          'fixed top-0 left-0 right-0 z-50 transition-all duration-500',
          scrolled ? 'glass-vanguard-scrolled' : 'glass-vanguard',
        )}
      >
        <div className="max-w-6xl mx-auto px-5 h-[4.25rem] flex items-center justify-between gap-4">
          <a href="#" className="flex items-center gap-3 group focus-ring">
            <div className="luxury-icon-box w-10 h-10 rounded-sm">
              <BrandMark size={22} />
            </div>
            <div>
              <p className="font-display font-semibold text-base text-[#f4efe6] leading-tight tracking-wide">
                Aria Vanguard
              </p>
              <p className="section-label text-[9px] mt-0.5">ZHC Holding</p>
            </div>
          </a>

          <div className="hidden md:flex items-center gap-0.5">
            {SECTIONS.map(({ href, label }) => {
              const isActive = active === href.slice(1)
              return (
                <a
                  key={href}
                  href={href}
                  className={cn(
                    'px-4 py-2 text-sm tracking-wide transition-colors focus-ring',
                    isActive
                      ? 'text-[#f4efe6] border-b border-[#c9a962]/60'
                      : 'text-[#8a8578] hover:text-[#e8d5a8]',
                  )}
                >
                  {label}
                </a>
              )
            })}
          </div>

          <div className="flex items-center gap-3">
            <div className="hidden sm:block">
              <MemberSignInButton />
            </div>
            <a
              href="https://x.com/Aria_ZHC"
              target="_blank"
              rel="noopener noreferrer"
              className="hidden sm:flex items-center gap-1.5 text-xs text-[#6b665c] hover:text-[#c9a962] transition-colors focus-ring tracking-wide"
            >
              @Aria_ZHC <ExternalLink className="w-3 h-3" />
            </a>
            <ProductLaunchLink className="btn-vanguard-glow px-4 py-2.5 text-xs uppercase tracking-[0.15em] focus-ring">
              Aria Market
            </ProductLaunchLink>
            <button
              type="button"
              className="md:hidden p-2 text-[#8a8578] hover:text-[#e8d5a8] focus-ring"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              aria-label={open ? 'Close menu' : 'Open menu'}
            >
              {open ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>
      </nav>

      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/75 backdrop-blur-sm"
            onClick={() => setOpen(false)}
            aria-label="Close menu"
          />
          <div className="absolute top-[4.25rem] left-0 right-0 glass-vanguard-scrolled p-6 space-y-1">
            {SECTIONS.map(({ href, label }) => (
              <a
                key={href}
                href={href}
                onClick={() => setOpen(false)}
                className="block px-2 py-3 font-display text-lg text-[#e8d5a8] border-b border-[#c9a962]/10 focus-ring"
              >
                {label}
              </a>
            ))}
            <div className="pt-4 sm:hidden">
              <MemberSignInButton />
            </div>
          </div>
        </div>
      )}
    </>
  )
}