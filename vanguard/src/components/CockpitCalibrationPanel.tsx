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

/** Barre bidirectionnelle ancrée sur un zéro central — jamais une barre qui part
 * du bord gauche (une perte lirait comme un gain sinon). */
function CalibrationBar({ value, maxAbs }: { value: number; maxAbs: number }) {
  // widthPct est relatif à la largeur de SA moitié (w-1/2) : 100% = atteint le
  // bord du conteneur. maxAbs (le plus grand |avg_pnl|) doit donc mapper à 100,
  // pas à 50 (piège classique : confondre "moitié du total" et "bord de la moitié").
  const widthPct = Math.max(6, Math.round((Math.abs(value) / maxAbs) * 100))
  const positive = value >= 0
  return (
    <div className="relative flex-1 h-4">
      <div className="absolute inset-y-0 left-1/2 w-px bg-[rgba(244,239,230,0.15)]" />
      <div className="absolute inset-0 flex">
        <div className="w-1/2 flex justify-end pr-px">
          {!positive ? (
            <div
              className="h-2 self-center rounded-l-full"
              style={{ width: `${widthPct}%`, backgroundColor: pnlColor(value), opacity: 0.85 }}
            />
          ) : null}
        </div>
        <div className="w-1/2 flex justify-start pl-px">
          {positive ? (
            <div
              className="h-2 self-center rounded-r-full"
              style={{ width: `${widthPct}%`, backgroundColor: pnlColor(value), opacity: 0.85 }}
            />
          ) : null}
        </div>
      </div>
    </div>
  )
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
      <div className="flex items-start justify-between gap-4 mb-1">
        <div>
          <p className="section-label mb-1">Calibration</p>
          <p className="text-sm text-[#d4d0c8] leading-relaxed max-w-md">
            Est-ce qu'un potentiel noté 8/10 bat vraiment un 5/10&nbsp;? La vraie mesure d'un
            analyste, pas une affirmation.
          </p>
        </div>
        <div className="text-right shrink-0">
          <p className="text-3xl font-mono tabular-nums text-[#f4efe6] leading-none">
            {track.verdicts_closed}
            <span className="text-base text-[#8b8f9a]">/{track.verdicts_total}</span>
          </p>
          <p className="text-[10px] uppercase tracking-[0.12em] text-[#8a7344] mt-1">clôturés</p>
        </div>
      </div>

      {buckets.length === 0 ? (
        <p className="text-sm text-[#8b8f9a] mt-4 mb-2">
          Pas encore assez de pronostics clôturés et notés pour tracer une courbe de
          calibration — c'est une donnée qui manque, pas un chiffre inventé.
        </p>
      ) : (
        <div className="space-y-3 mt-5 mb-6">
          {buckets.map((b) => (
            <div key={b.bucket} className="flex items-center gap-3">
              <span className="w-14 shrink-0 text-[11px] font-mono text-[#8a7344] tabular-nums">
                {b.bucket}
              </span>
              <CalibrationBar value={b.avg_pnl} maxAbs={maxAbs} />
              <span
                className="w-20 shrink-0 text-right text-lg font-mono font-semibold tabular-nums"
                style={{ color: pnlColor(b.avg_pnl) }}
              >
                {b.avg_pnl >= 0 ? '+' : ''}
                {b.avg_pnl.toFixed(1)}%
              </span>
              <span className="w-10 shrink-0 text-right text-[11px] text-[#8b8f9a] font-mono tabular-nums">
                n={b.count}
              </span>
            </div>
          ))}
        </div>
      )}

      {strategies.length > 0 ? (
        <div className="grid sm:grid-cols-2 gap-3 pt-4 border-t border-[rgba(201,169,98,0.12)]">
          {strategies.map(([key, s]) => (
            <div key={key}>
              <p className="text-[11px] uppercase tracking-[0.12em] text-[#8a7344] mb-1.5">
                {STRATEGY_LABELS[key] ?? key}
              </p>
              <div className="flex items-baseline gap-2.5">
                <span className="text-3xl font-mono font-semibold tabular-nums text-[#f4efe6] leading-none">
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
