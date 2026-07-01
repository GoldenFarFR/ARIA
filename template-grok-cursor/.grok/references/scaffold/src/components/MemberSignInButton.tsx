import { getIdentityToken, useLogin, usePrivy, useUser } from '@privy-io/react-auth'
import { useCallback, useRef, useState } from 'react'
import { clearToken, getToken } from '../lib/auth'
import { PRIVY_LOGIN_METHODS } from '../lib/privy-config'
import { exchangePrivyForMemberSession, MEMBER_SESSION_EVENT } from '../lib/privy-session'

export function MemberSignInButton() {
  const { ready, authenticated, getAccessToken, logout } = usePrivy()
  const { refreshUser } = useUser()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const finishingRef = useRef(false)

  const finishSignIn = useCallback(async () => {
    if (finishingRef.current) return
    finishingRef.current = true
    setBusy(true)
    setError(null)
    try {
      await exchangePrivyForMemberSession(getAccessToken, getIdentityToken, refreshUser)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
    } finally {
      setBusy(false)
      finishingRef.current = false
    }
  }, [getAccessToken, refreshUser])

  const { login } = useLogin({ onComplete: () => void finishSignIn() })

  const handleClick = useCallback(() => {
    if (busy) return
    setError(null)
    if (authenticated) {
      void finishSignIn()
      return
    }
    login({ loginMethods: [...PRIVY_LOGIN_METHODS] })
  }, [authenticated, busy, finishSignIn, login])

  const signOut = useCallback(async () => {
    clearToken()
    setError(null)
    await logout()
    window.dispatchEvent(new Event(MEMBER_SESSION_EVENT))
  }, [logout])

  if (!ready) return null

  const hasSession = Boolean(getToken())
  const needsLink = authenticated && !hasSession

  return (
    <div className="flex flex-col items-end gap-1">
      {authenticated && hasSession ? (
        <button type="button" onClick={() => void signOut()} className="btn-secondary px-3 py-2 text-xs uppercase tracking-wide">
          Sign out
        </button>
      ) : (
        <button type="button" onClick={handleClick} disabled={busy} className="btn-secondary px-3 py-2 text-xs uppercase tracking-wide disabled:opacity-50">
          {busy ? 'Signing in…' : needsLink ? 'Activate access' : 'Sign in'}
        </button>
      )}
      {error && (
        <p className="text-[10px] text-red-300/80 max-w-[14rem] text-right" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}