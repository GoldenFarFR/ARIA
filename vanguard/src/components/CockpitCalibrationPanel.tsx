import { useEffect, useState } from 'react'
import { getTrackRecord, type TrackRecord } from '../api'

const STRATEGY_LABELS: Record<string, string> = {
  vc: 'VC (85 % moyen/long terme)',
  spec: 'Spéculation (15 % small-cap filtrée)',
}

function pnlColor(v: number): string {
  if (v > 0) return '#7fb88f'
  if (v < 0) return '#d98a8a'
  return '#8b8f9a'
}

export function CockpitCalibrationPanel() {
  const [track, setTrack] = useState<TrackRecord | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    getTrackRecord()
      .then((t) => {
        if (!cancelled) setTrack(t)
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
        <p className="section-label mb-3">Calibration</p>
        <p className="text-sm text-[#8b8f9a]">Indisponible pour le moment.</p>
      </div>
    )
  }

  if (!track) {
    return (
      <div className="glass-vanguard rounded-sm p-5 sm:p-6">
        <div className="skeleton h-5 w-32 rounded-sm mb-4" />
        <div className="skeleton h-24 rounded-sm" />
      </div>
    )
  }

  const buckets = track.calibration ?? []
  const maxAbs = Math.max(1, ...buckets.map((b) => Math.abs(b.avg_pnl)))
  const strategies = Object.entries(track.by_strategy ?? {})

  return (
    <div className="glass-vanguard rounded-sm p-5 sm:p-6">
      <div className="flex items-center justify-between mb-1">
        <p className="section-label">Calibration</p>
        <span className="text-[11px] text-[#8b8f9a] font-mono">
          {track.verdicts_closed}/{track.verdicts_total} clôturés
        </span>
      </div>
      <p className="text-sm text-[#d4d0c8] mb-5 leading-relaxed">
        Est-ce qu'un potentiel noté 8/10 bat vraiment un 5/10&nbsp;? La vraie mesure d'un
        analyste, pas une affirmation.
      </p>

      {buckets.length === 0 ? (
        <p className="text-sm text-[#8b8f9a] mb-5">
          Pas encore assez de pronostics clôturés et notés pour tracer une courbe de
          calibration — c'est une donnée qui manque, pas un chiffre inventé.
        </p>
      ) : (
        <div className="space-y-2.5 mb-6">
          {buckets.map((b) => {
            const widthPct = Math.max(4, Math.round((Math.abs(b.avg_pnl) / maxAbs) * 100))
            return (
              <div key={b.bucket} className="flex items-center gap-3">
                <span className="w-16 shrink-0 text-[11px] font-mono text-[#8a7344] tabular-nums">
                  {b.bucket}
                </span>
                <div className="flex-1 h-5 rounded-sm bg-[rgba(244,239,230,0.05)] overflow-hidden">
                  <div
                    className="h-full rounded-sm"
                    style={{
                      width: `${widthPct}%`,
                      backgroundColor: pnlColor(b.avg_pnl),
                      opacity: 0.75,
                    }}
                  />
                </div>
                <span
                  className="w-20 shrink-0 text-right text-xs font-mono tabular-nums"
                  style={{ color: pnlColor(b.avg_pnl) }}
                >
                  {b.avg_pnl >= 0 ? '+' : ''}
                  {b.avg_pnl.toFixed(1)}%
                </span>
                <span className="w-14 shrink-0 text-right text-[11px] text-[#8b8f9a] font-mono tabular-nums">
                  n={b.count}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {strategies.length > 0 ? (
        <div className="grid sm:grid-cols-2 gap-3">
          {strategies.map(([key, s]) => (
            <div key={key} className="minimal-card px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1">
                {STRATEGY_LABELS[key] ?? key}
              </p>
              <div className="flex items-baseline gap-2">
                <span className="text-lg text-[#d4d0c8] font-mono tabular-nums">
                  {s.hit_rate != null ? `${Math.round(s.hit_rate * 100)}%` : '—'}
                </span>
                <span className="text-[11px] text-[#8b8f9a]">hit-rate · {s.buy_count} BUY</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <p className="text-[11px] text-[#8b8f9a] mt-5 leading-relaxed">{track.disclaimer}</p>
    </div>
  )
}
