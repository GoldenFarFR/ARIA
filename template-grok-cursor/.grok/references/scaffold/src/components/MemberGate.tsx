import { usePrivy } from '@privy-io/react-auth'
import { useEffect } from 'react'
import { checkSession, getAuthRequired } from '../api'
import { clearToken, getToken } from '../lib/auth'

export function MemberGate({ children }: { children: React.ReactNode }) {
  const { ready } = usePrivy()

  useEffect(() => {
    if (!ready) return
    let cancelled = false

    async function validate() {
      try {
        const status = await getAuthRequired()
        if (!status.required || !getToken()) return
        const session = await checkSession()
        if (!session.valid && !cancelled) clearToken()
      } catch {
        /* public site stays open */
      }
    }

    void validate()
    return () => {
      cancelled = true
    }
  }, [ready])

  return <>{children}</>
}