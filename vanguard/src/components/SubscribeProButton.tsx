import { getIdentityToken, useLogin, usePrivy, useUser } from '@privy-io/react-auth'
import { useCallback, useState } from 'react'
import { createCheckoutSession } from '../api'
import { getToken } from '../lib/auth'
import { PRIVY_LOGIN_METHODS } from '../lib/privy-config'
import { exchangePrivyForAriaSession } from '../lib/privy-session'
import { HOLDING_SITE_URL } from '../lib/site'

interface Props {
  stripeReady: boolean
}

export function SubscribeProButton({ stripeReady }: Props) {
  const { ready, authenticated, getAccessToken } = usePrivy()
  const { refreshUser } = useUser()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const goCheckout = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const { checkout_url } = await createCheckoutSession({
        success_url: `${HOLDING_SITE_URL}/?sub=success#pricing`,
        cancel_url: `${HOLDING_SITE_URL}/?sub=cancel#pricing`,
      })
      window.location.href = checkout_url
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Checkout impossible.')
    } finally {
      setBusy(false)
    }
  }, [])

  const { login } = useLogin({
    onComplete: async () => {
      try {
        await exchangePrivyForAriaSession(getAccessToken, getIdentityToken, refreshUser)
        if (stripeReady) await goCheckout()
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Connexion échouée.')
      }
    },
  })

  const handleClick = useCallback(async () => {
    if (!stripeReady) return
    if (!ready) return
    if (!authenticated || !getToken()) {
      login({ loginMethods: [...PRIVY_LOGIN_METHODS] })
      return
    }
    await goCheckout()
  }, [authenticated, goCheckout, login, ready, stripeReady])

  if (!stripeReady) {
    return (
      <button
        type="button"
        disabled
        className="btn-vanguard-secondary w-full py-4 text-sm uppercase tracking-[0.12em] opacity-60 cursor-not-allowed"
      >
        Bientôt disponible
      </button>
    )
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={busy || !ready}
        className="btn-vanguard-glow w-full py-4 text-sm uppercase tracking-[0.15em] focus-ring disabled:opacity-50"
      >
        {busy ? 'Redirection Stripe…' : "S'abonner à Aria Market Pro"}
      </button>
      <p className="text-[10px] text-[#6b665c] text-center mt-3">
        Connexion X/email (Privy) requise avant paiement.
      </p>
      {error && (
        <p className="text-[10px] text-[#c9a962]/90 text-center mt-2" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}