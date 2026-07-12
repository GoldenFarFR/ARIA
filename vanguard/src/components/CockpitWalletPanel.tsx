import { useEffect, useState } from 'react'
import { getPaperWallet, type PaperWallet } from '../api'

function pnlColor(v: number): string {
  if (v > 0) return '#7fb88f'
  if (v < 0) return '#d98a8a'
  return '#8b8f9a'
}

export function CockpitWalletPanel() {
  const [wallet, setWallet] = useState<PaperWallet | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    getPaperWallet()
      .then((w) => {
        if (!cancelled) setWallet(w)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (failed) {
    return (
      <div className="glass-vanguard rounded-sm p-5 sm:p-6">
        <p className="section-label mb-3">Wallet suivi</p>
        <p className="text-sm text-[#8b8f9a]">Indisponible pour le moment.</p>
      </div>
    )
  }

  if (!wallet) {
    return (
      <div className="glass-vanguard rounded-sm p-5 sm:p-6">
        <div className="skeleton h-5 w-32 rounded-sm mb-4" />
        <div className="skeleton h-24 rounded-sm" />
      </div>
    )
  }

  const history = wallet.history ?? []

  return (
    <div className="glass-vanguard rounded-sm p-5 sm:p-6">
      <div className="flex items-start justify-between gap-4 mb-1">
        <div>
          <p className="section-label mb-1">Wallet suivi</p>
          <p className="text-sm text-[#d4d0c8] leading-relaxed max-w-md">
            Portefeuille paper-trading — la preuve, pas la promesse.
          </p>
        </div>
        <div className="text-right shrink-0">
          <p
            className="text-3xl font-mono font-semibold tabular-nums leading-none"
            style={{ color: pnlColor(wallet.return_pct) }}
          >
            {wallet.return_pct >= 0 ? '+' : ''}
            {wallet.return_pct.toFixed(1)}%
          </p>
          <p className="text-[10px] uppercase tracking-[0.12em] text-[#8a7344] mt-1">rendement</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mt-5 mb-6">
        <div>
          <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1.5">
            Positions ouvertes
          </p>
          <div className="flex items-baseline gap-2.5">
            <span className="text-3xl font-mono font-semibold tabular-nums text-[#f4efe6] leading-none">
              {wallet.open_positions}
            </span>
            <span className="text-[11px] font-mono tabular-nums" style={{ color: pnlColor(wallet.unrealized_pnl) }}>
              {wallet.unrealized_pnl >= 0 ? '+' : ''}
              {wallet.unrealized_pnl.toFixed(0)} $ latent
            </span>
          </div>
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1.5">
            Trades clôturés
          </p>
          <div className="flex items-baseline gap-2.5">
            <span className="text-3xl font-mono font-semibold tabular-nums text-[#f4efe6] leading-none">
              {wallet.closed_trades}
            </span>
            <span className="text-[11px] text-[#8b8f9a]">
              {wallet.win_rate != null ? `${Math.round(wallet.win_rate)}% win-rate` : '—'}
            </span>
          </div>
        </div>
      </div>

      {history.length === 0 ? (
        <p className="text-sm text-[#8b8f9a] mt-4 mb-2">
          Aucun trade clôturé pour le moment — jamais un chiffre inventé.
        </p>
      ) : (
        <div className="space-y-2 pt-4 border-t border-[rgba(201,169,98,0.12)]">
          {history.slice(0, 10).map((t, i) => (
            <div key={`${t.symbol}-${t.closed_at}-${i}`} className="flex items-center gap-3">
              <span className="w-16 shrink-0 text-[11px] font-mono text-[#8a7344] tabular-nums">
                {t.closed_at}
              </span>
              <span className="flex-1 text-sm text-[#d4d0c8] truncate">{t.symbol || '—'}</span>
              <span
                className="w-16 shrink-0 text-right text-sm font-mono font-semibold tabular-nums"
                style={{ color: pnlColor(t.pnl_pct ?? 0) }}
              >
                {t.pnl_pct != null ? `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(0)}%` : '—'}
              </span>
            </div>
          ))}
        </div>
      )}

      <p className="text-[11px] text-[#8b8f9a] mt-5 leading-relaxed">{wallet.disclaimer}</p>
    </div>
  )
}
