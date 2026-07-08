import { KeyRound } from 'lucide-react'
import { useState } from 'react'
import { getDossier, OperatorAuthError } from '../api'
import { clearOperatorSession, setOperatorSecret, setOperatorTotp } from '../lib/operator-auth'

// Adresse "burn" bien connue — sert uniquement à vérifier le secret opérateur
// (appel réel à /dossier, réponse toujours vide) sans exiger d'endpoint dédié.
const PING_CONTRACT = '0x000000000000000000000000000000000000dEaD'

interface Props {
  onUnlocked: () => void
}

export function CockpitGate({ onUnlocked }: Props) {
  const [secret, setSecretInput] = useState('')
  const [totp, setTotpInput] = useState('')
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function verify(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = secret.trim()
    if (!trimmed || checking) return
    setChecking(true)
    setError(null)
    setOperatorSecret(trimmed)
    setOperatorTotp(totp.trim())
    try {
      await getDossier(PING_CONTRACT)
      onUnlocked()
    } catch (err) {
      clearOperatorSession()
      setError(
        err instanceof OperatorAuthError
          ? "Secret opérateur invalide, ou code TOTP manquant/incorrect."
          : "Connexion au centre de commandement impossible pour le moment.",
      )
    } finally {
      setChecking(false)
      setSecretInput('')
      setTotpInput('')
    }
  }

  return (
    <form
      onSubmit={verify}
      className="glass-vanguard rounded-sm p-6 sm:p-8 max-w-sm w-full mx-auto"
    >
      <div className="luxury-icon-box w-10 h-10 rounded-sm mb-5">
        <KeyRound size={17} className="text-[#c9a962]" strokeWidth={1.6} />
      </div>
      <p className="section-label mb-1">Accès opérateur</p>
      <h2 className="font-display text-2xl text-[#f4efe6] mb-3">Centre de commandement</h2>
      <p className="text-sm text-[#8b8f9a] mb-6 leading-relaxed">
        Réservé à l'opérateur. Le secret n'est conservé que pour cet onglet — jamais stocké
        de façon persistante, jamais transmis en clair dans une URL.
      </p>

      <label htmlFor="cockpit-secret" className="block text-xs uppercase tracking-[0.15em] text-[#8a7344] mb-2">
        Secret opérateur
      </label>
      <input
        id="cockpit-secret"
        type="password"
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        value={secret}
        onChange={(e) => setSecretInput(e.target.value)}
        className="w-full bg-black/30 border border-[#c9a962]/20 rounded-sm px-3 py-2.5 text-[#f4efe6] focus-ring mb-4"
        placeholder="••••••••••••"
      />

      <label htmlFor="cockpit-totp" className="block text-xs uppercase tracking-[0.15em] text-[#8a7344] mb-2">
        Code TOTP <span className="normal-case tracking-normal text-[#5c606b]">(si activé)</span>
      </label>
      <input
        id="cockpit-totp"
        type="text"
        inputMode="numeric"
        autoComplete="off"
        spellCheck={false}
        value={totp}
        onChange={(e) => setTotpInput(e.target.value)}
        className="w-full bg-black/30 border border-[#c9a962]/20 rounded-sm px-3 py-2.5 text-[#f4efe6] font-mono tracking-[0.25em] focus-ring mb-5"
        placeholder="000000"
      />

      {error ? <p className="text-sm text-[#d98a8a] mb-4 leading-relaxed">{error}</p> : null}

      <button
        type="submit"
        disabled={checking || !secret.trim()}
        className="btn-vanguard-glow w-full py-2.5 rounded-sm text-sm disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {checking ? 'Vérification…' : 'Entrer'}
      </button>
    </form>
  )
}
