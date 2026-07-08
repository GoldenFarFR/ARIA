import { ExternalLink, Menu, X } from 'lucide-react'
import { BrandMark } from './BrandMark'
import { MemberSignInButton } from './MemberSignInButton'
import { SiteLanguagePicker } from './SiteLanguagePicker'
import { useEffect, useState } from 'react'
import { cn } from '../lib/cn'

const SECTIONS = [
  { href: '#structure', label: 'Structure' },
  { href: '#aria', label: 'ARIA' },
  { href: '#faq', label: 'FAQ' },
]

export function VanguardNav() {
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 16)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : ''
    return () => {
      document.body.style.overflow = ''
    }
  }, [open])

  return (
    <>
      <nav
        className={cn(
          'transition-all duration-300',
          scrolled ? 'nav-minimal-scrolled' : 'nav-minimal',
        )}
      >
        <div className="page-shell h-14 flex items-center justify-between gap-4">
          <a href="#" className="flex items-center gap-2.5 focus-ring">
            <BrandMark size={20} />
            <span className="font-display text-base text-[#f4efe6] tracking-wide notranslate">
              Vanguard
            </span>
          </a>

          <div className="hidden md:flex items-center gap-6">
            {SECTIONS.map(({ href, label }) => (
              <a
                key={href}
                href={href}
                className="text-sm text-[#b5b0a3] hover:text-[#e8d5a8] transition-colors focus-ring"
              >
                {label}
              </a>
            ))}
            <a
              href="https://x.com/Aria_ZHC"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-[#b5b0a3] hover:text-[#c9a962] transition-colors focus-ring inline-flex items-center gap-1 notranslate"
            >
              @Aria_ZHC <ExternalLink className="w-3 h-3" />
            </a>
            <SiteLanguagePicker />
            <MemberSignInButton />
          </div>

          <div className="flex items-center gap-2 md:hidden">
            <MemberSignInButton />
          </div>

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
      </nav>

      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/80"
            onClick={() => setOpen(false)}
            aria-label="Close menu"
          />
          <div className="absolute top-14 left-0 right-0 nav-minimal-scrolled p-6 space-y-4">
            <div className="pb-2">
              <SiteLanguagePicker />
            </div>
            {SECTIONS.map(({ href, label }) => (
              <a
                key={href}
                href={href}
                onClick={() => setOpen(false)}
                className="block font-display text-xl text-[#e8d5a8] focus-ring"
              >
                {label}
              </a>
            ))}
            <a
              href="https://x.com/Aria_ZHC"
              target="_blank"
              rel="noopener noreferrer"
              className="block text-sm text-[#6b665c] focus-ring"
            >
              @Aria_ZHC
            </a>
          </div>
        </div>
      )}
    </>
  )
}