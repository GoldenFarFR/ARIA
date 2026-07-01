import { usePrivy } from '@privy-io/react-auth'
import { useEffect } from 'react'
import { checkSession, getAuthRequired } from '../api'
import { clearToken, getToken } from './auth'

/**
 * Validate an existing backend session on load — never calls Privy.
 * Session creation: MemberSignInButton only (Sign in / Activer l'accès).
 */
export function useAutoMemberSession(): void {
  const { ready } = usePrivy()

  useEffect(() => {
    if (!ready) return

    let cancelled = false

    async function validateStoredSession() {
      try {
        const status = await getAuthRequired()
        if (!status.required || cancelled) return

        const existing = getToken()
        if (!existing) return

        const session = await checkSession()
        if (!session.valid && !cancelled) clearToken()
      } catch {
        /* offline — keep token for next attempt */
      }
    }

    void validateStoredSession()
    return () => {
      cancelled = true
    }
  }, [ready])
}