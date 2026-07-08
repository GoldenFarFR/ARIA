import {
  getIdentityToken,
  useIdentityToken,
  useLogin,
  useMfaEnrollment,
  usePrivy,
  useUser,
} from '@privy-io/react-auth'
import { useCallback, useEffect, useRef, useState } from 'react'
import { clearToken, getToken } from '../lib/auth'
import { clearMemberProfile } from '../lib/member-profile'
import { PRIVY_LOGIN_METHODS } from '../lib/privy-config'
import { exchangePrivyForAriaSession } from '../lib/privy-session'

const LOGIN_GUARD_KEY = 'aria:privy:login-guard'

function canOpenPrivyModal(): boolean {
  try {
    const raw = sessionStorage.getItem(LOGIN_GUARD_KEY)
    if (!raw) return true
    return Date.now() > Number(raw)
  } catch {
    return true
  }
}

function markPrivyModalOpened(): void {
  try {
    sessionStorage.setItem(LOGIN_GUARD_KEY, String(Date.now() + 20_000))
  } catch {
    /* ignore */
  }
}

export function MemberSignInButton() {
  const { ready, authenticated, getAccessToken, logout } = usePrivy()
  const { refreshUser } = useUser()
  const { showMfaEnrollmentModal } = useMfaEnrollment()
  const { identityToken: hookIdentityToken } = useIdentityToken()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const finishingRef = useRef(false)

  const finishSignIn = useCallback(async () => {
    if (finishingRef.current) return
    finishingRef.current = true
    setBusy(true)
    setError(null)
    try {
      await exchangePrivyForAriaSession(
        getAccessToken,
        getIdentityToken,
        refreshUser,
        hookIdentityToken,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
    } finally {
      setBusy(false)
      finishingRef.current = false
    }
  }, [getAccessToken, getIdentityToken, hookIdentityToken, refreshUser])

  useEffect(() => {
    const onError = (event: Event) => {
      const detail = (event as CustomEvent<{ message?: string }>).detail
      if (detail?.message) setError(detail.message)
    }
    const onSession = () => {
      setError(null)
      setBusy(false)
    }

    window.addEventListener('aria:session-exchange-error', onError)
    window.addEventListener('aria:member-session', onSession)

    return () => {
      window.removeEventListener('aria:session-exchange-error', onError)
      window.removeEventListener('aria:member-session', onSession)
    }
  }, [])

  const { login } = useLogin({
    onComplete: () => {
      void finishSignIn()
    },
  })

  const openPrivyLogin = useCallback(() => {
    if (!canOpenPrivyModal()) {
      setError('Patiente 20 secondes avant de rouvrir la fenêtre Privy.')
      return
    }
    markPrivyModalOpened()
    setError(null)
    login({ loginMethods: [...PRIVY_LOGIN_METHODS] })
  }, [login])

  const handlePrimaryClick = useCallback(() => {
    if (busy) return
    setError(null)
    if (authenticated) {
      void finishSignIn()
      return
    }
    openPrivyLogin()
  }, [authenticated, busy, finishSignIn, openPrivyLogin])

  const signOut = useCallback(async () => {
    clearToken()
    clearMemberProfile()
    setError(null)
    await logout()
    window.dispatchEvent(new Event('aria:member-session'))
  }, [logout])

  if (!ready) return null

  const hasBackendSession = Boolean(getToken())
  const needsSessionLink = authenticated && !hasBackendSession

  return (
    <div className="flex flex-col items-end gap-1">
      {authenticated && hasBackendSession ? (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => showMfaEnrollmentModal()}
            title="Activer la double authentification (2FA) — code d’authentification ou passkey"
            className="btn-vanguard-secondary px-3 py-2 text-xs uppercase tracking-[0.12em] focus-ring"
          >
            2FA
          </button>
          <button
            type="button"
            onClick={() => void signOut()}
            className="btn-vanguard-secondary px-3 py-2 text-xs uppercase tracking-[0.12em] focus-ring"
          >
            Sign out
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={handlePrimaryClick}
          disabled={busy}
          className="btn-vanguard-secondary px-3 py-2 text-xs uppercase tracking-[0.12em] focus-ring disabled:opacity-50"
        >
          {busy
            ? 'Connexion…'
            : needsSessionLink
              ? 'Activer l’accès'
              : 'Sign in'}
        </button>
      )}
      {error && (
        <p className="text-[10px] text-[#c9a962]/80 max-w-[14rem] text-right leading-snug" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}