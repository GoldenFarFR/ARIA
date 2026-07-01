import { ExternalLink, Lock } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { checkSession, getAuthRequired } from '../api'
import { clearToken, getToken, setToken } from '../lib/auth'
import { clearVanguardSessionFromUrl, importVanguardSession } from '../lib/session-handoff'
import { HOLDING_NAME, HOLDING_SITE_URL } from '../lib/site'

type GateState = 'loading' | 'open' | 'blocked'

interface MemberGateProps {
  children: React.ReactNode
}

async function syncSessionFromBackend(): Promise<boolean> {
  importVanguardSession()
  const session = await checkSession()
  if (!session.valid) return false
  if (session.token) setToken(session.token)
  clearVanguardSessionFromUrl()
  return true
}

const BLOCKED_HINT =
  `Accès réservé aux membres connectés sur ${HOLDING_NAME}. ` +
  'Sign in → Activer l’accès → Open Aria Market (fenêtre #market sur Vanguard).'

const STALE_SESSION_HINT =
  `Session expirée ou réinitialisée côté serveur. Retourne sur ${HOLDING_NAME}, ` +
  'clique « Activer l’accès » dans la nav, puis rouvre Open Aria Market.'

export function MemberGate({ children }: MemberGateProps) {
  const [state, setState] = useState<GateState>('loading')
  const [message, setMessage] = useState('')

  const verifyAccess = useCallback(async () => {
    setState('loading')
    importVanguardSession()

    const fail = (hint: string) => {
      clearToken()
      setState('blocked')
      setMessage(hint)
    }

    try {
      const status = await getAuthRequired()
      if (!status.required) {
        setState('open')
        return
      }

      setMessage(status.message || BLOCKED_HINT)

      if (await syncSessionFromBackend()) {
        setState('open')
        return
      }

      const hadHandoff = importVanguardSession() || Boolean(getToken())
      if (hadHandoff) fail(STALE_SESSION_HINT)
      else fail(BLOCKED_HINT)
    } catch {
      const hadHandoff = Boolean(getToken())
      fail(hadHandoff ? STALE_SESSION_HINT : BLOCKED_HINT)
    }
  }, [])

  useEffect(() => {
    void verifyAccess()
  }, [verifyAccess])

  useEffect(() => {
    const onSessionLost = () => {
      clearToken()
      void verifyAccess()
    }
    const onSessionRestored = () => {
      void verifyAccess()
    }
    window.addEventListener('aria-market:session-lost', onSessionLost)
    window.addEventListener('aria-market:session-restored', onSessionRestored)
    return () => {
      window.removeEventListener('aria-market:session-lost', onSessionLost)
      window.removeEventListener('aria-market:session-restored', onSessionRestored)
    }
  }, [verifyAccess])

  if (state === 'loading') {
    return (
      <div className="min-h-screen pixel-canvas flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
      </div>
    )
  }

  if (state === 'blocked') {
    return (
      <div className="min-h-screen pixel-canvas flex items-center justify-center p-6">
        <div className="max-w-md w-full pixel-panel p-8 text-center space-y-6">
          <div className="mx-auto w-14 h-14 rounded-sm border border-accent/40 flex items-center justify-center bg-accent/5">
            <Lock className="w-7 h-7 text-accent" />
          </div>
          <div className="space-y-2">
            <h1 className="font-display text-xl text-terminal tracking-wide">Accès membre requis</h1>
            <p className="text-sm text-[#8a8880] leading-relaxed">{message}</p>
          </div>

          <a
            href={HOLDING_SITE_URL}
            className="pixel-btn pixel-btn-active w-full inline-flex items-center justify-center gap-2 py-3 text-sm"
          >
            Ouvrir {HOLDING_NAME}
            <ExternalLink className="w-4 h-4" />
          </a>
          <p className="text-[11px] text-[#6a6860] leading-relaxed">
            Connexion uniquement via Vanguard — pas de login séparé sur Aria Market.
          </p>
        </div>
      </div>
    )
  }

  return <>{children}</>
}