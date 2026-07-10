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

function rsiColor(rsi: number): string {
  if (rsi >= 70) return '#e8c468'
  if (rsi <= 30) return '#d98a8a'
  return '#d4d0c8'
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

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">{label}</p>
      <p className="text-2xl font-mono tabular-nums leading-none" style={{ color }}>
        {value}
      </p>
    </div>
  )
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
        <div className="space-y-4 mb-2">
          {sentiment.readings.map((r) => {
            const color = REGIME_COLORS[r.regime] ?? '#8b8f9a'
            const label = sentiment.regime_labels[r.regime] ?? r.regime
            return (
              <div key={r.pair} className="minimal-card px-4 py-4">
                <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
                  <div className="flex items-center gap-2.5">
                    <span className="text-base text-[#f4efe6] font-mono font-semibold tracking-tight">
                      {r.pair}
                    </span>
                    <span
                      className="text-[10px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-full font-medium"
                      style={{ color, backgroundColor: `${color}1f`, border: `1px solid ${color}55` }}
                    >
                      {label.split(' (')[0]}
                    </span>
                  </div>
                  <span className="text-[11px] text-[#8b8f9a] font-mono">{relativeTime(r.computed_at)}</span>
                </div>

                {r.rsi != null ? (
                  <>
                    <div className="grid grid-cols-3 gap-4 mb-3">
                      <Stat label="RSI" value={r.rsi.toFixed(0)} color={rsiColor(r.rsi)} />
                      <Stat
                        label="Momentum"
                        value={`${r.momentum_pct != null && r.momentum_pct >= 0 ? '+' : ''}${
                          r.momentum_pct != null ? r.momentum_pct.toFixed(1) : '—'
                        }%`}
                        color={r.momentum_pct != null ? (r.momentum_pct >= 0 ? '#7fb88f' : '#d98a8a') : '#8b8f9a'}
                      />
                      <Stat
                        label="Retracement"
                        value={
                          r.drawdown_from_high_pct != null ? `${r.drawdown_from_high_pct.toFixed(1)}%` : '—'
                        }
                        color={
                          r.drawdown_from_high_pct != null && r.drawdown_from_high_pct < -20
                            ? '#d98a8a'
                            : '#d4d0c8'
                        }
                      />
                    </div>
                    <LinearGauge value={r.rsi} min={0} max={100} zones={RSI_ZONES} />
                  </>
                ) : null}

                <p className="text-xs text-[#8b8f9a] leading-relaxed mt-3">{r.detail}</p>
              </div>
            )
          })}
        </div>
      )}

      <p className="text-[11px] text-[#8b8f9a] mt-4 leading-relaxed">{sentiment.disclaimer}</p>
    </div>
  )
}
