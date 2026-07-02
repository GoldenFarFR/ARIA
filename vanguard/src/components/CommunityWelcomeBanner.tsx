import { ExternalLink, Heart, MessageSquarePlus, Send, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { submitCommunityFeedback, warmProductApi } from '../api'
import { getMemberProfile } from '../lib/member-profile'
import { TELEGRAM_COMMUNITY_URL } from '../lib/site'
import { loadVisitorPrefs, purgeLegacyBannerDismiss, saveVisitorPrefs } from '../lib/visitor-prefs'

export function CommunityWelcomeBanner() {
  const [minimized, setMinimized] = useState(false)
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState('')
  const [handle, setHandle] = useState('')
  const [status, setStatus] = useState<'idle' | 'sending' | 'done' | 'error'>('idle')
  const [reply, setReply] = useState('')

  const applyPrefs = useCallback(() => {
    purgeLegacyBannerDismiss()
    const prefs = loadVisitorPrefs()
    setMinimized(Boolean(prefs.bannerMinimized))
    setOpen(Boolean(prefs.formOpen))
    if (prefs.feedbackDraft?.message) setMessage(prefs.feedbackDraft.message)
    const profileHandle = getMemberProfile()?.handle?.replace(/^@/, '')
    const draftHandle = prefs.feedbackDraft?.handle?.trim()
    setHandle(profileHandle || draftHandle || '')
  }, [])

  useEffect(() => {
    applyPrefs()
    const onSession = () => applyPrefs()
    window.addEventListener('aria:member-session', onSession)
    return () => window.removeEventListener('aria:member-session', onSession)
  }, [applyPrefs])

  useEffect(() => {
    saveVisitorPrefs({
      bannerMinimized: minimized,
      formOpen: open,
      feedbackDraft: { message, handle },
    })
  }, [minimized, open, message, handle])

  useEffect(() => {
    if (open) void warmProductApi()
  }, [open])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    const text = message.trim()
    if (text.length < 8) return
    setStatus('sending')
    setReply('')
    try {
      const result = await submitCommunityFeedback(text, handle.trim() || undefined)
      setReply(result.reply)
      setStatus('done')
      setMessage('')
      saveVisitorPrefs({ feedbackDraft: { message: '', handle } })
    } catch (err) {
      setReply(err instanceof Error ? err.message : 'Network error')
      setStatus('error')
    }
  }

  if (minimized) {
    return (
      <div
        className="border-b border-[#c9a962]/10 bg-[#0a0a0c]/90"
        role="region"
        aria-label="ZHC community"
      >
        <div className="page-shell py-1.5 !max-w-2xl flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => {
              setMinimized(false)
              setOpen(true)
            }}
            className="text-xs text-[#8a8578] hover:text-[#e8d5a8] transition-colors focus-ring flex items-center gap-1.5"
          >
            <MessageSquarePlus className="w-3.5 h-3.5 text-[#c9a962]" />
            Your feedback — <span className="notranslate">ZHC</span> community
          </button>
          <button
            type="button"
            onClick={() => setMinimized(false)}
            className="text-[10px] uppercase tracking-wider text-[#6b665c] hover:text-[#c9a962] focus-ring"
          >
            Expand
          </button>
        </div>
      </div>
    )
  }

  return (
    <div
      className="border-b border-[#c9a962]/15 bg-gradient-to-r from-[#c9a962]/8 via-[#101012]/95 to-[#8a7344]/8"
      role="region"
      aria-label="ZHC community"
    >
      <div className="page-shell py-2.5 !max-w-2xl">
        <div className="flex items-start sm:items-center gap-3 sm:gap-4">
          <Heart className="w-4 h-4 text-[#c9a962] shrink-0 mt-0.5 sm:mt-0" aria-hidden />
          <p className="text-sm text-[#9a958a] leading-relaxed flex-1 font-light">
            <span className="text-[#d4c4a0] notranslate">ZHC</span> community — got an idea? ARIA
            notes it and may ship it if it strengthens Vanguard.{' '}
            <a
              href={TELEGRAM_COMMUNITY_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[#c9a962] hover:text-[#e8d5a8] transition-colors focus-ring notranslate"
            >
              Telegram
              <ExternalLink className="w-3 h-3" aria-hidden />
            </a>
          </p>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 flex items-center gap-1.5 text-xs uppercase tracking-wider text-[#c9a962] hover:text-[#e8d5a8] transition-colors focus-ring px-2 py-1"
          >
            <MessageSquarePlus className="w-3.5 h-3.5" />
            {open ? 'Close' : 'Your feedback'}
          </button>
          <button
            type="button"
            onClick={() => setMinimized(true)}
            className="text-[#6b665c] hover:text-[#e8d5a8] transition-colors focus-ring p-1 shrink-0"
            aria-label="Minimize banner"
            title="Minimize (reappears on next visit)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {open && (
          <form onSubmit={onSubmit} className="mt-3 pb-1 space-y-3 border-t border-[#c9a962]/10 pt-3">
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="e.g. add a Telegram link, improve the FAQ…"
              rows={3}
              maxLength={500}
              className="w-full rounded-sm bg-[#0c0c0e] border border-[#c9a962]/20 px-3 py-2 text-sm text-[#f4efe6] placeholder:text-[#6b665c] focus-ring resize-y min-h-[4.5rem]"
              required
            />
            <p className="text-[11px] text-[#6b665c] leading-snug">
              {message.length}/500 — full note saved; @Aria_ZHC quote auto-fits X limit (280)
            </p>
            <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
              <input
                type="text"
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                placeholder="Handle (auto if signed in)"
                maxLength={64}
                className="flex-1 rounded-sm bg-[#0c0c0e] border border-[#c9a962]/15 px-3 py-2 text-sm text-[#f4efe6] placeholder:text-[#6b665c] focus-ring"
              />
              <button
                type="submit"
                disabled={status === 'sending' || message.trim().length < 8}
                className="btn-vanguard-glow flex items-center justify-center gap-2 px-5 py-2.5 text-sm tracking-wide disabled:opacity-50 focus-ring"
              >
                <Send className="w-4 h-4" />
                {status === 'sending' ? 'Sending…' : 'Send to ARIA'}
              </button>
            </div>
            {reply && (
              <p
                className={`text-sm leading-relaxed ${
                  status === 'error' ? 'text-[#c97a7a]' : 'text-[#b8b2a6]'
                }`}
                role="status"
              >
                {reply}
              </p>
            )}
          </form>
        )}
      </div>
    </div>
  )
}