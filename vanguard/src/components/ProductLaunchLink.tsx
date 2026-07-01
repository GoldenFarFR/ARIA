import { usePrivy } from '@privy-io/react-auth'
import { useCallback, useState } from 'react'
import { openProductInVanguard } from '../lib/product-handoff'
import {
  PRODUCT_SESSION_HINT,
  resolveProductSession,
} from '../lib/resolve-product-session'

interface ProductLaunchLinkProps {
  className?: string
  children: React.ReactNode
}

export function ProductLaunchLink({ className, children }: ProductLaunchLinkProps) {
  const { ready, authenticated } = usePrivy()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onClick = useCallback(
    (event: React.MouseEvent) => {
      event.preventDefault()
      setError(null)
      if (!ready || busy) return

      if (!authenticated) {
        setError('Sign in (nav en haut à droite), puis réessaie.')
        return
      }

      setBusy(true)
      void (async () => {
        try {
          const token = await resolveProductSession()
          if (!token) {
            setError(PRODUCT_SESSION_HINT)
            return
          }
          openProductInVanguard()
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Session indisponible.')
        } finally {
          setBusy(false)
        }
      })()
    },
    [authenticated, busy, ready],
  )

  return (
    <span className="inline-flex flex-col items-end gap-1">
      <a href="#market" onClick={onClick} className={className}>
        {children}
      </a>
      {error && (
        <span className="text-[10px] text-[#c9a962]/80 text-right leading-snug max-w-[14rem]" role="alert">
          {error}
        </span>
      )}
    </span>
  )
}