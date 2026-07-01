import { usePrivy } from '@privy-io/react-auth'
import { useEffect } from 'react'
import { checkSession, getAuthRequired } from '../api'
import { clearToken, getToken } from '../lib/auth'
import { clearLaunchQuery, wantsProductLaunch } from '../lib/product-handoff'
import { useAutoMemberSession } from '../lib/use-auto-member-session'

interface MemberGateProps {
  children: React.ReactNode
}

/**
 * Public holding site — never hard-block on Privy.
 * Validates existing backend session only; no auto-redirect (avoids ping-pong with Aria Market).
 */
export function MemberGate({ children }: MemberGateProps) {
  const { ready } = usePrivy()
  useAutoMemberSession()

  useEffect(() => {
    if (wantsProductLaunch()) {
      clearLaunchQuery()
    }
  }, [])

  useEffect(() => {
    if (!ready) return

    let cancelled = false

    async function validateExistingSession() {
      try {
        const status = await getAuthRequired()
        if (!status.required) return

        const existing = getToken()
        if (!existing) return

        const session = await checkSession()
        if (!session.valid && !cancelled) clearToken()
      } catch {
        /* ignore */
      }
    }

    void validateExistingSession()
    return () => {
      cancelled = true
    }
  }, [ready])

  return <>{children}</>
}