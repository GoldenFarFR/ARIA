import { usePrivy } from '@privy-io/react-auth'
import { X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { getToken } from '../lib/auth'
import {
  productEmbedUrl,
  pushSessionToProductFrame,
} from '../lib/product-handoff'
import {
  PRODUCT_SESSION_HINT,
  resolveProductSession,
} from '../lib/resolve-product-session'

const HASH = '#market'

function isProductView(): boolean {
  return window.location.hash === HASH
}

export function ProductFrame() {
  const { ready, authenticated } = usePrivy()
  const [open, setOpen] = useState(isProductView)
  const [iframeSrc, setIframeSrc] = useState<string | null>(null)
  const [prepError, setPrepError] = useState<string | null>(null)
  const [preparing, setPreparing] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement>(null)

  const sync = useCallback(() => setOpen(isProductView()), [])

  useEffect(() => {
    sync()
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [sync])

  const prepareIframe = useCallback(async () => {
    if (!ready || !authenticated) {
      setIframeSrc(null)
      return
    }

    setPreparing(true)
    setPrepError(null)
    try {
      const token = await resolveProductSession()
      if (!token) {
        setIframeSrc(null)
        setPrepError(PRODUCT_SESSION_HINT)
        return
      }
      setIframeSrc(productEmbedUrl(token))
    } catch (err) {
      setIframeSrc(null)
      setPrepError(err instanceof Error ? err.message : 'Session Aria Market indisponible.')
    } finally {
      setPreparing(false)
    }
  }, [authenticated, ready])

  useEffect(() => {
    if (!open) {
      setIframeSrc(null)
      setPrepError(null)
      return
    }
    void prepareIframe()
  }, [open, prepareIframe])

  useEffect(() => {
    const onMemberSession = () => {
      if (!open) return
      void prepareIframe()
    }
    window.addEventListener('aria:member-session', onMemberSession)
    return () => window.removeEventListener('aria:member-session', onMemberSession)
  }, [open, prepareIframe])

  const close = useCallback(() => {
    if (window.history.length > 1) {
      window.history.back()
    } else {
      window.location.hash = ''
      setOpen(false)
    }
  }, [])

  const onIframeLoad = useCallback(() => {
    const token = getToken()
    if (token) pushSessionToProductFrame(iframeRef.current, token)
  }, [])

  if (!open) return null

  const canLoad = ready && authenticated

  return (
    <div className="fixed inset-0 z-[100] flex flex-col bg-[#0a0a08]">
      <header className="flex items-center justify-between gap-3 border-b border-[#2a2820] px-4 py-2">
        <span className="font-display text-sm text-[#c9a962] tracking-wide">Aria Market</span>
        <button
          type="button"
          onClick={close}
          className="pixel-btn px-3 py-1.5 text-xs inline-flex items-center gap-1"
        >
          <X className="w-3.5 h-3.5" />
          Fermer
        </button>
      </header>

      {!canLoad ? (
        <div className="flex-1 flex items-center justify-center p-6 text-center text-sm text-[#8a8880]">
          Sign in (nav) → Activer l&apos;accès → réouvre Open Aria Market.
        </div>
      ) : preparing ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6 text-center text-sm text-[#8a8880]">
          <div className="w-8 h-8 border-2 border-[#c9a962]/30 border-t-[#c9a962] rounded-full animate-spin" />
          <p>Ouverture Aria Market…</p>
        </div>
      ) : prepError ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-4 p-6 text-center text-sm text-[#8a8880]">
          <p>{prepError}</p>
          <button
            type="button"
            onClick={() => void prepareIframe()}
            className="btn-vanguard-secondary px-4 py-2 text-xs uppercase tracking-wide"
          >
            Vérifier à nouveau
          </button>
        </div>
      ) : iframeSrc ? (
        <iframe
          ref={iframeRef}
          key={iframeSrc}
          title="Aria Market"
          src={iframeSrc}
          onLoad={onIframeLoad}
          className="flex-1 w-full border-0 bg-[#0a0a08]"
          allow="clipboard-write"
        />
      ) : null}
    </div>
  )
}