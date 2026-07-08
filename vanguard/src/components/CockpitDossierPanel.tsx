import { Search } from 'lucide-react'
import { useState } from 'react'
import { getDossier, OperatorAuthError, type Dossier } from '../api'
import { clearOperatorSession } from '../lib/operator-auth'

const KIND_LABELS: Record<string, string> = {
  analyse: 'Analyse VC',
  analyse_resultat: 'Résultat attribué',
  these: 'Carnet de bord',
  suivi: 'Suivi de thèse',
  memoire: 'Mémoire d’investissement',
  memoire_resultat: 'Leçon',
  paper_achat: 'Achat (paper 1M$)',
  paper_vente: 'Vente (paper 1M$)',
}

function formatEventDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('fr-FR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

interface Props {
  onAuthLost: () => void
}

export function CockpitDossierPanel({ onAuthLost }: Props) {
  const [contract, setContract] = useState('')
  const [dossier, setDossier] = useState<Dossier | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function search(e: React.FormEvent) {
    e.preventDefault()
    const addr = contract.trim()
    if (!addr || loading) return
    setLoading(true)
    setError(null)
    setDossier(null)
    try {
      const d = await getDossier(addr)
      setDossier(d)
    } catch (err) {
      if (err instanceof OperatorAuthError) {
        clearOperatorSession()
        onAuthLost()
        return
      }
      setError('Dossier indisponible pour le moment.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="glass-vanguard rounded-sm p-5 sm:p-6">
      <p className="section-label mb-1">Dossier par token</p>
      <h3 className="font-display text-xl text-[#f4efe6] mb-4">Chronologie d'un contrat</h3>

      <form onSubmit={search} className="flex gap-2 mb-5">
        <input
          value={contract}
          onChange={(e) => setContract(e.target.value)}
          placeholder="0x..."
          spellCheck={false}
          className="flex-1 min-w-0 bg-black/30 border border-[#c9a962]/20 rounded-sm px-3 py-2.5 text-[#f4efe6] font-mono text-sm focus-ring"
        />
        <button
          type="submit"
          disabled={loading || !contract.trim()}
          className="btn-vanguard-secondary px-4 py-2.5 rounded-sm text-sm disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2 shrink-0"
        >
          <Search size={14} strokeWidth={1.8} />
          {loading ? 'Recherche…' : 'Ouvrir'}
        </button>
      </form>

      {error ? <p className="text-sm text-[#d98a8a] mb-4">{error}</p> : null}

      {dossier && !dossier.valid ? <p className="text-sm text-[#d98a8a]">{dossier.error}</p> : null}

      {dossier && dossier.valid && (!dossier.events || dossier.events.length === 0) ? (
        <div className="text-sm text-[#8b8f9a] leading-relaxed">
          <p className="mb-2">Aucune analyse enregistrée sur ce token pour l'instant.</p>
          {dossier.screened_status ? <p className="mb-2">Statut pool : {dossier.screened_status}.</p> : null}
          <p>
            Lancer une analyse complète via Telegram :{' '}
            <code className="font-mono text-[#e8d5a8]">/vc {dossier.contract}</code>
          </p>
        </div>
      ) : null}

      {dossier && dossier.valid && dossier.events && dossier.events.length > 0 ? (
        <div>
          <div className="flex items-center gap-3 mb-4 text-xs text-[#8b8f9a] font-mono flex-wrap">
            {dossier.symbol ? <span className="text-[#e8d5a8] font-medium">{dossier.symbol}</span> : null}
            <span>{dossier.counts?.analyses ?? 0} analyse(s)</span>
            <span>{dossier.counts?.suivis ?? 0} suivi(s)</span>
            <span>{dossier.counts?.paper ?? 0} position(s) paper</span>
          </div>
          <ol className="relative border-l border-[#c9a962]/15 pl-5 space-y-4 max-h-[420px] overflow-y-auto">
            {dossier.events.map((ev, i) => (
              <li key={i} className="relative">
                <span className="absolute -left-[23px] top-1.5 w-2 h-2 rounded-full bg-[#c9a962]/60" />
                <p className="text-[10px] uppercase tracking-[0.12em] text-[#8a7344] font-mono">
                  {formatEventDate(ev.at)} · {KIND_LABELS[ev.kind] ?? ev.kind}
                </p>
                <p className="text-sm text-[#d4d0c8] mt-0.5">{ev.summary}</p>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  )
}
