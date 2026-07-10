import { useEffect, useState } from 'react'
import { getMarketSentiment, type MarketSentiment } from '../api'

const REGIME_COLORS: Record<string, string> = {
  euphorie: '#e8c468',
  complaisance: '#d9b56a',
  anxiete_distribution: '#e0a15a',
  capitulation_peur: '#d98a8a',
  doute_accumulation: '#8fa9c9',
  optimisme_conviction: '#7fb88f',
  neutre: '#8b8f9a',
  donnees_insuffisantes: '#5c5f66',
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'inconnu'
  const mins = Math.round((Date.now() - then) / 60_000)
  if (mins < 1) return "à l'instant"
  if (mins < 60) return `il y a ${mins} min`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `il y a ${hours} h`
  return `il y a ${Math.round(hours / 24)} j`
}

export function CockpitSentimentPanel() {
  const [sentiment, setSentiment] = useState<MarketSentiment | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    getMarketSentiment()
      .then((s) => {
        if (!cancelled) setSentiment(s)
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
        <p className="section-label mb-3">Sentiment de marché</p>
        <p className="text-sm text-[#8b8f9a]">Indisponible pour le moment.</p>
      </div>
    )
  }

  if (!sentiment) {
    return (
      <div className="glass-vanguard rounded-sm p-5 sm:p-6">
        <div className="skeleton h-5 w-32 rounded-sm mb-4" />
        <div className="skeleton h-14 rounded-sm" />
      </div>
    )
  }

  return (
    <div className="glass-vanguard rounded-sm p-5 sm:p-6">
      <p className="section-label mb-4">Sentiment de marché (RSI, Bollinger, momentum)</p>

      {sentiment.readings.length === 0 ? (
        <p className="text-sm text-[#8b8f9a] mb-2">
          Aucune lecture pour le moment — le scan continu n'a pas encore tourné.
        </p>
      ) : (
        <div className="space-y-4 mb-2">
          {sentiment.readings.map((r) => {
            const color = REGIME_COLORS[r.regime] ?? '#8b8f9a'
            const label = sentiment.regime_labels[r.regime] ?? r.regime
            return (
              <div key={r.pair} className="minimal-card px-4 py-3">
                <div className="flex items-center justify-between flex-wrap gap-2 mb-1.5">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
                    <span className="text-sm text-[#f4efe6] font-medium font-mono">{r.pair}</span>
                    <span className="text-sm" style={{ color }}>
                      {label}
                    </span>
                  </div>
                  <span className="text-[11px] text-[#8b8f9a] font-mono">{relativeTime(r.computed_at)}</span>
                </div>
                <p className="text-xs text-[#8b8f9a] leading-relaxed">{r.detail}</p>
                {r.rsi != null ? (
                  <div className="flex gap-4 mt-2 text-[11px] font-mono text-[#8b8f9a] tabular-nums">
                    <span>RSI {r.rsi.toFixed(0)}</span>
                    {r.momentum_pct != null ? (
                      <span style={{ color: r.momentum_pct >= 0 ? '#7fb88f' : '#d98a8a' }}>
                        momentum {r.momentum_pct >= 0 ? '+' : ''}
                        {r.momentum_pct.toFixed(1)}%
                      </span>
                    ) : null}
                    {r.drawdown_from_high_pct != null ? (
                      <span>retracement {r.drawdown_from_high_pct.toFixed(1)}%</span>
                    ) : null}
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      )}

      <p className="text-[11px] text-[#8b8f9a] mt-3 leading-relaxed">{sentiment.disclaimer}</p>
    </div>
  )
}
