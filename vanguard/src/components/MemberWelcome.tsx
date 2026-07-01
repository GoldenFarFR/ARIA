import { ArrowRight, Sparkles, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getToken } from '../lib/auth'
import { openProductInVanguard } from '../lib/product-handoff'
import {
  dismissMemberWelcome,
  getMemberProfile,
  shouldShowMemberWelcome,
} from '../lib/member-profile'

export function MemberWelcome() {
  const [visible, setVisible] = useState(false)
  const [profile, setProfile] = useState(getMemberProfile())

  useEffect(() => {
    const sync = () => {
      setProfile(getMemberProfile())
      setVisible(shouldShowMemberWelcome(Boolean(getToken())))
    }
    sync()
    window.addEventListener('aria:member-session', sync)
    return () => window.removeEventListener('aria:member-session', sync)
  }, [])

  if (!visible || !profile) return null

  const rawHandle = profile.handle != null ? String(profile.handle) : ''
  const handle = rawHandle ? `@${rawHandle.replace(/^@/, '')}` : 'membre'

  return (
    <div
      className="fixed bottom-5 left-5 right-5 z-40 md:left-auto md:right-6 md:max-w-md"
      role="status"
      aria-live="polite"
    >
      <div className="glass-vanguard rounded-sm p-5 border border-[#c9a962]/25 shadow-2xl shadow-black/40">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-2 text-[#c9a962]">
            <Sparkles className="w-4 h-4 shrink-0" />
            <p className="text-[10px] uppercase tracking-[0.2em] font-medium">Bienvenue</p>
          </div>
          <button
            type="button"
            onClick={() => {
              dismissMemberWelcome()
              setVisible(false)
            }}
            className="text-[#6b665c] hover:text-[#e8d5a8] transition-colors focus-ring p-1"
            aria-label="Fermer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="font-display text-lg text-[#f4efe6] tracking-wide mb-1">
          Bonjour, {handle}
        </p>
        <p className="text-sm text-[#9a958a] leading-relaxed mb-4 font-light">
          {profile.message ||
            'Session membre active. Ouvre Aria Market pour l’analyse temps réel et l’agent ARIA complet.'}
        </p>
        <a
          href="#market"
          className="btn-vanguard-glow w-full flex items-center justify-center gap-2 py-3 text-sm tracking-wide focus-ring"
          onClick={(event) => {
            event.preventDefault()
            dismissMemberWelcome()
            setVisible(false)
            openProductInVanguard()
          }}
        >
          Open Aria Market
          <ArrowRight className="w-4 h-4" />
        </a>
      </div>
    </div>
  )
}