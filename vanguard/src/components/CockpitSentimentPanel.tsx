import { useEffect, useState } from 'react'
import { getMarketSentiment, type MarketSentiment } from '../api'
import { LinearGauge } from './Gauge'

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

const RSI_ZONES = [
  { from: 0, to: 30, color: '#d98a8a' },
  { from: 30, to: 70, color: '#8b8f9a' },
  { from: 70, to: 100, color: '#e8c468' },
]

const BOLLINGER_ZONES = [
  { from: -0.3, to: 0, color: '#d98a8a' },
  { from: 0, to: 1, color: '#8b8f9a' },
  { from: 1, to: 1.3, color: '#e8c468' },
]

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
        <div className="skeleton h-24 rounded-sm" />
      </div>
    )
  }

  return (
    <div className="glass-vanguard rounded-sm p-5 sm:p-6">
      <p className="section-label mb-4">Sentiment de marché</p>

      {sentiment.readings.length === 0 ? (
        <p className="text-sm text-[#8b8f9a] mb-2">
          Aucune lecture pour le moment — le scan continu n'a pas encore tourné.
        </p>
      ) : (
        <div className="space-y-5 mb-2">
          {sentiment.readings.map((r) => {
            const color = REGIME_COLORS[r.regime] ?? '#8b8f9a'
            const label = sentiment.regime_labels[r.regime] ?? r.regime
            return (
              <div key={r.pair} className="minimal-card px-4 py-4">
                <div className="flex items-start justify-between flex-wrap gap-2 mb-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-base text-[#f4efe6] font-mono font-medium">{r.pair}</span>
                      <span
                        className="text-[10px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-full"
                        style={{ color, backgroundColor: `${color}1a`, border: `1px solid ${color}40` }}
                      >
                        {label.split(' (')[0]}
                      </span>
                    </div>
                    <p className="text-xs text-[#8b8f9a] leading-relaxed mt-1.5 max-w-md">{r.detail}</p>
                  </div>
                  <span className="text-[11px] text-[#8b8f9a] font-mono shrink-0">
                    {relativeTime(r.computed_at)}
                  </span>
                </div>

                {r.rsi != null ? (
                  <div className="grid sm:grid-cols-2 gap-x-6 gap-y-3">
                    <div>
                      <div className="flex items-baseline justify-between mb-1">
                        <span className="text-[10px] uppercase tracking-[0.12em] text-[#8a7344]">RSI</span>
                        <span className="text-xs font-mono tabular-nums text-[#d4d0c8]">
                          {r.rsi.toFixed(0)}
                        </span>
                      </div>
                      <LinearGauge value={r.rsi} min={0} max={100} zones={RSI_ZONES} />
                    </div>
                    {r.bollinger_position != null ? (
                      <div>
                        <div className="flex items-baseline justify-between mb-1">
                          <span className="text-[10px] uppercase tracking-[0.12em] text-[#8a7344]">
                            Position Bollinger
                          </span>
                          <span className="text-xs font-mono tabular-nums text-[#d4d0c8]">
                            {r.bollinger_position.toFixed(2)}
                          </span>
                        </div>
                        <LinearGauge
                          value={r.bollinger_position}
                          min={-0.3}
                          max={1.3}
                          zones={BOLLINGER_ZONES}
                        />
                      </div>
                    ) : null}
                  </div>
                ) : null}

                <div className="flex gap-4 mt-3 text-[11px] font-mono text-[#8b8f9a] tabular-nums">
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
              </div>
            )
          })}
        </div>
      )}

      <p className="text-[11px] text-[#8b8f9a] mt-4 leading-relaxed">{sentiment.disclaimer}</p>
    </div>
  )
}
